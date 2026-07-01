"""Full model: B-arm + CD-arm（A→C→D→C→A）。

model.md v0.2 を 2 アーム両方で実装する。barm_model.py（B-arm 単体・検証済）の
パターンを D（一時サイト）まで一般化したもの。

CD-arm の要点:
  - 1 CD トリップ = A→C→D→C→A（往復8h）。往路で D 行き(toD)、復路で D 帰還者(frD)を運ぶ。
  - C は通過点。D 到着 = dep+a_c+c_d、D 発(帰還) = dep'+a_c+c_d（個別帰還で別便 dep' 可）。
  - D 必要滞在 = d_stay_table[乗客の体重カテゴリ][その到着便の同乗総人数]（min のみ, 上限なし）。
    体重を区別しない従来形（d_stay_table[n]）も後方互換で受理する。
ローテーション（確定）: 乗客単位・交互順のみ。連続する勤務スロットは B と D を交互に。
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from .barm_model import _build_vehicles
from .loader import holiday_hour_intervals, hour_offset
from .schema import Instance, expand_departures

D = "D"  # 一時サイトのキー


class SolutionRecorder(cp_model.CpSolverSolutionCallback):
    """求解中、改善解（より安い feasible 解）を見つけるたびに記録するコールバック。

    各行は (解番号 n, 発見経過秒 t, コスト cost, その時点の下限 bound)。
    GUI の「解の改善グラフ（時刻×コスト）」用。bound と cost の差が gap で、
    bound が cost まで上がり切れば最適確定。
    """

    def __init__(self) -> None:
        super().__init__()
        self._t0 = time.perf_counter()
        self.rows: list[dict] = []

    def on_solution_callback(self) -> None:
        self.rows.append({
            "n": len(self.rows) + 1,
            "t": round(time.perf_counter() - self._t0, 2),
            "cost": self.ObjectiveValue(),
            "bound": self.BestObjectiveBound(),
        })


class FullModel:
    def __init__(self, inst: Instance):
        if inst.cd_arm is None or inst.temporary_site is None:
            raise ValueError("FullModel は cd_arm と temporary_site が必要")
        self.inst = inst
        self.m = cp_model.CpModel()
        self.H = inst.planning_horizon.hours
        self.bsites = list(inst.staffed_sites.keys())
        self.vehicles = _build_vehicles(inst)
        self.holidays = holiday_hour_intervals(inst)
        self.M = inst.solver.max_visits_per_passenger
        self.J = inst.solver.trips_per_site
        self.JCD = inst.solver.trips_cd
        self._build()

    # ------------------------------------------------------------------
    def _no_overlap_holiday(self, dep, trip_h, used, tag):
        m = self.m
        for (hs, he) in self.holidays:
            before = m.NewBoolVar(f"hb_{tag}_{hs}")
            after = m.NewBoolVar(f"ha_{tag}_{hs}")
            m.Add(dep + trip_h <= hs).OnlyEnforceIf(before)
            m.Add(dep >= he).OnlyEnforceIf(after)
            m.AddBoolOr([before, after]).OnlyEnforceIf(used)

    def _build(self):
        m, inst, H = self.m, self.inst, self.H
        bsites, vehicles = self.bsites, self.vehicles
        M, J, JCD = self.M, self.J, self.JCD
        cd = inst.cd_arm
        # トリップは [0, commit] のみ。lookahead 区間 [commit, H] は常駐の余裕確保用（trip なし）。
        commit = inst.solver.commit_hours if inst.solver.commit_hours is not None else H
        self.commit = commit
        pax = [p.id for p in inst.passengers]
        cats = sorted({p.category for p in inst.passengers})
        cap_max = max(v.capacity for v in vehicles)
        veh_intervals = {v.id: [] for v in vehicles}

        # ============ L1a: B-arm 交代イベント（A↔Bx は徒歩 = 配車不要） ============
        # B への往復は徒歩のため車両を消費せずコストも生まない。usedB/depB は
        # 「同時刻交代イベント」の足場としてのみ用い、占有 min・カテゴリ・together・
        # 乗降タイミングを離散イベントで評価する（車両割当・定員・コストは持たない）。
        self.usedB, self.depB = {}, {}
        for k in bsites:
            din = inst.staffed_sites[k].segments.inbound_hours
            dout = inst.staffed_sites[k].segments.outbound_hours
            th = din + dout
            for j in range(J):
                used = m.NewBoolVar(f"usedB_{k}_{j}")
                dep = m.NewIntVar(0, H, f"depB_{k}_{j}")
                self.usedB[k, j], self.depB[k, j] = used, dep
                m.Add(dep == H).OnlyEnforceIf(used.Not())
                m.Add(dep + th <= commit).OnlyEnforceIf(used)
                self._no_overlap_holiday(dep, th, used, f"B_{k}_{j}")
            for j in range(J - 1):
                m.Add(self.usedB[k, j] >= self.usedB[k, j + 1])
                m.Add(self.depB[k, j] <= self.depB[k, j + 1])

        # ============ L1b: CD-arm トリップ ============
        # 車両が占有するのは運転区間 A→C + C→A のみ（C↔D は徒歩 = 配車不要）。
        # 乗客の往復総時間 round_hours と、車両拘束時間 drive_hours を区別する。
        self.usedCD, self.depCD, self.assignCD, self.capCD = {}, {}, {}, {}
        thcd = cd.round_hours        # 乗客の論理往復（commit / 休日回避はこちらで保守的に判定）
        drive = cd.drive_hours       # 車両の実拘束（インターバル / コスト）
        # 固定ダイヤ: 各保有車両の a_c_departures を [0,commit] 全日に展開し、
        # 便 = (出発時刻, 車両) ごとに 1 つ生成する。各便は当該車両のみが担当し、
        # 出す/出さないのみ選ぶ（時刻は最適化対象外）。便ダイヤは全車の和で、同時刻に
        # 複数車があれば各々が別便（＝定員スタック可）。いずれの車両にもダイヤが無い
        # 場合のみ従来どおり自由ダイヤ（JCD=solver.trips_cd, 時刻は最適化対象）。
        cd_trips = [(t, v) for v in vehicles
                    for t in expand_departures(v.departures, commit, thcd)]
        cd_trips.sort(key=lambda tv: (tv[0], tv[1].id))
        self.cd_trips = cd_trips
        timetabled = bool(cd_trips)
        self.cd_sched = sorted({t for t, _ in cd_trips})
        if timetabled:
            # ダイヤ便数が CD トリップ数（窓内に便が無ければ 0 = 運休）。
            JCD = self.JCD = len(cd_trips)
        for j in range(JCD):
            used = m.NewBoolVar(f"usedCD_{j}")
            if timetabled:
                t, v = cd_trips[j]
                dep = m.NewConstant(t)           # 出発はダイヤ時刻に固定
                self.usedCD[j], self.depCD[j] = used, dep
                # 便 j は車両 v が担当（割当 = used）。
                self.assignCD[j, v.id] = used
                # 車両拘束は [dep, dep+drive]。C↔D 徒歩・D 滞在中は車両を解放。
                veh_intervals[v.id].append(
                    m.NewOptionalIntervalVar(dep, drive, dep + drive, used,
                                             f"ivCD_{j}_{v.id}"))
                cap = m.NewIntVar(0, cap_max, f"capCD_{j}")
                m.Add(cap == used * v.capacity)
                self.capCD[j] = cap
            else:
                dep = m.NewIntVar(0, H, f"depCD_{j}")
                m.Add(dep == H).OnlyEnforceIf(used.Not())
                m.Add(dep + thcd <= commit).OnlyEnforceIf(used)
                self.usedCD[j], self.depCD[j] = used, dep
                avs = []
                for v in vehicles:
                    a = m.NewBoolVar(f"asgCD_{j}_{v.id}")
                    self.assignCD[j, v.id] = a
                    avs.append(a)
                    veh_intervals[v.id].append(
                        m.NewOptionalIntervalVar(dep, drive, dep + drive, a,
                                                 f"ivCD_{j}_{v.id}"))
                m.Add(sum(avs) == used)
                cap = m.NewIntVar(0, cap_max, f"capCD_{j}")
                m.Add(cap == sum(self.assignCD[j, v.id] * v.capacity for v in vehicles))
                self.capCD[j] = cap
            # 休日に当たるダイヤ便は used=0 に強制（自動運休）。
            self._no_overlap_holiday(dep, thcd, used, f"CD_{j}")
        if not timetabled:
            # 自由ダイヤのみ: 互換トリップ間の対称性除去（早い便を先に詰める）。
            # 固定ダイヤでは各便が別時刻/別車両のため used は独立。
            for j in range(JCD - 1):
                m.Add(self.usedCD[j] >= self.usedCD[j + 1])
                m.Add(self.depCD[j] <= self.depCD[j + 1])

        # 車両 NoOverlap（B-arm と CD-arm を共有プールで）
        for v in vehicles:
            m.AddNoOverlap(veh_intervals[v.id])

        # ============ L2: 乗客訪問スロット ============
        sites = bsites + [D]
        self.at, self.atused, self.a, self.d, self.leaves = {}, {}, {}, {}, {}
        self.btype = {}
        self.sdin, self.sdout = {}, {}
        self.inB, self.outB, self.toD, self.frD = {}, {}, {}, {}

        def arr_travel(k):   # A→site
            return (inst.staffed_sites[k].segments.inbound_hours if k != D else cd.to_d_hours)

        def dep_travel(k):   # site→A
            return (inst.staffed_sites[k].segments.outbound_hours if k != D else cd.from_d_hours)

        maxdur = max(
            [inst.staffed_sites[k].segments.inbound_hours
             + inst.staffed_sites[k].segments.outbound_hours for k in bsites]
            + [cd.round_hours]
        )

        for p in pax:
            allowedB = set(inst.allowed_sites_of(p))
            init = inst.initial_of(p)
            transit = init.transit_leg if init else None
            init_site = init.location if (init and init.location in sites) else None
            init_off = hour_offset(inst, init.arrived_at) if (init and init.arrived_at) else 0
            # 島間移動中（A→C = D へ向かう途中）は D 滞在の継続として扱う:
            # 先頭スロットを D 滞在に固定し、a[p,0] = D 到着時刻（未来 ≥0 可）とする。
            # 到着便 toD は持たず（pre-t0 便）、出発便 frD は当ウィンドウ内で持てる。
            if transit == ("A", "C"):
                init_site = D

            for mi in range(M):
                a = m.NewIntVar(-10000, H, f"a_{p}_{mi}")
                d = m.NewIntVar(-10000, H + 10000, f"d_{p}_{mi}")
                self.a[p, mi], self.d[p, mi] = a, d
                atu = m.NewBoolVar(f"atu_{p}_{mi}")
                leaves = m.NewBoolVar(f"lv_{p}_{mi}")
                self.atused[p, mi], self.leaves[p, mi] = atu, leaves
                m.Add(leaves <= atu)

                sb = []
                for k in sites:
                    at = m.NewBoolVar(f"at_{p}_{mi}_{k}")
                    self.at[p, mi, k] = at
                    sb.append(at)
                    if k != D and k not in allowedB:
                        m.Add(at == 0)
                m.Add(sum(sb) == atu)

                bt = m.NewBoolVar(f"bt_{p}_{mi}")   # B 勤務スロットか
                m.Add(bt == sum(self.at[p, mi, k] for k in bsites))
                self.btype[p, mi] = bt

                sdin = m.NewIntVar(0, maxdur, f"sdin_{p}_{mi}")
                sdout = m.NewIntVar(0, maxdur, f"sdout_{p}_{mi}")
                m.Add(sdin == sum(self.at[p, mi, k] * arr_travel(k) for k in sites))
                m.Add(sdout == sum(self.at[p, mi, k] * dep_travel(k) for k in sites))
                self.sdin[p, mi], self.sdout[p, mi] = sdin, sdout

                # 結合: B-arm
                for k in bsites:
                    if k not in allowedB:
                        continue
                    din = inst.staffed_sites[k].segments.inbound_hours
                    for j in range(J):
                        inb = m.NewBoolVar(f"inB_{p}_{mi}_{k}_{j}")
                        outb = m.NewBoolVar(f"outB_{p}_{mi}_{k}_{j}")
                        self.inB[p, mi, k, j], self.outB[p, mi, k, j] = inb, outb
                        m.Add(inb <= self.at[p, mi, k])
                        m.Add(outb <= self.at[p, mi, k])
                        m.Add(inb <= self.usedB[k, j])
                        m.Add(outb <= self.usedB[k, j])
                        m.Add(a == self.depB[k, j] + din).OnlyEnforceIf(inb)
                        m.Add(d == self.depB[k, j] + din).OnlyEnforceIf(outb)
                # 結合: CD-arm（D）
                for j in range(JCD):
                    td = m.NewBoolVar(f"toD_{p}_{mi}_{j}")
                    fd = m.NewBoolVar(f"frD_{p}_{mi}_{j}")
                    self.toD[p, mi, j], self.frD[p, mi, j] = td, fd
                    m.Add(td <= self.at[p, mi, D])
                    m.Add(fd <= self.at[p, mi, D])
                    m.Add(td <= self.usedCD[j])
                    m.Add(fd <= self.usedCD[j])
                    # 往路: A→C 乗車 + C→D 徒歩 で D 到着（= dep + a_c + c_d）。
                    m.Add(a == self.depCD[j] + cd.to_d_hours).OnlyEnforceIf(td)
                    # 帰路: D→C 徒歩で C へ歩き、dep+a_c に折り返す車両へ乗車。
                    #   ⇒ D 発 = dep + a_c - d_c（= dep + d_depart_offset）。
                    m.Add(d == self.depCD[j] + cd.d_depart_offset).OnlyEnforceIf(fd)

            # スロット先詰め
            for mi in range(M - 1):
                m.Add(self.atused[p, mi] >= self.atused[p, mi + 1])

            # 初期位置
            if init_site is not None:
                m.Add(self.at[p, 0, init_site] == 1)
                m.Add(self.a[p, 0] == init_off)
                # handoff: 残り必要滞在（D の継続滞在など）。この時刻まで出られない。
                if init and init.earliest_departure is not None:
                    ed = hour_offset(inst, init.earliest_departure)
                    m.Add(self.d[p, 0] >= ed).OnlyEnforceIf(self.leaves[p, 0])
            elif transit == ("C", "A"):
                # 島間移動中（C→A = D から A へ戻る途中）: D 勤務は完了済み。A 待機者と
                # して扱い、次勤務は B に固定（D の次は B）。加えて A 到着前には次勤務で
                # A を発てない（a[p,0]-sdin = A 発の時刻 ≥ A 到着時刻）。
                m.Add(self.btype[p, 0] == 1).OnlyEnforceIf(self.atused[p, 0])
                m.Add(self.a[p, 0] - self.sdin[p, 0] >= init_off).OnlyEnforceIf(
                    self.atused[p, 0]
                )
            elif init is not None and init.last_duty in ("B", "D"):
                # A 待機者: 直前勤務と交互（B の後は D、D の後は B）を最初のスロットに課す。
                want_b = 1 if init.last_duty == "D" else 0
                m.Add(self.btype[p, 0] == want_b).OnlyEnforceIf(self.atused[p, 0])

            for mi in range(M):
                is_init_slot = init_site is not None and mi == 0
                arr_sum = (
                    sum(self.inB[p, mi, k, j] for k in bsites if k in allowedB for j in range(J))
                    + sum(self.toD[p, mi, j] for j in range(JCD))
                )
                dep_sum = (
                    sum(self.outB[p, mi, k, j] for k in bsites if k in allowedB for j in range(J))
                    + sum(self.frD[p, mi, j] for j in range(JCD))
                )
                m.Add(arr_sum == (0 if is_init_slot else self.atused[p, mi]))
                m.Add(dep_sum == self.leaves[p, mi])

                # 滞在: B サイト（min/max, 期限内なら帰還強制）
                for k in bsites:
                    if k not in allowedB:
                        continue
                    smin = inst.staffed_sites[k].stay.min_hours
                    smax = inst.staffed_sites[k].stay.max_hours
                    hl = m.NewBoolVar(f"hl_{p}_{mi}_{k}")
                    m.AddBoolAnd([self.at[p, mi, k], self.leaves[p, mi]]).OnlyEnforceIf(hl)
                    m.AddBoolOr([self.at[p, mi, k].Not(), self.leaves[p, mi].Not()]).OnlyEnforceIf(hl.Not())
                    m.Add(self.d[p, mi] >= self.a[p, mi] + smin).OnlyEnforceIf(hl)
                    m.Add(self.d[p, mi] <= self.a[p, mi] + smax).OnlyEnforceIf(hl)
                    dlin = m.NewBoolVar(f"dlin_{p}_{mi}_{k}")
                    m.Add(self.a[p, mi] + smax <= H).OnlyEnforceIf(dlin)
                    m.Add(self.a[p, mi] + smax >= H + 1).OnlyEnforceIf(dlin.Not())
                    m.AddBoolOr([self.at[p, mi, k].Not(), dlin.Not(), self.leaves[p, mi]])

                # 滞在: D の動的 min は nAC 確定後にまとめて課す（後段）。

                # スロット順序 + A 往復連続性
                if mi < M - 1:
                    m.Add(self.atused[p, mi + 1] <= self.leaves[p, mi])
                    m.Add(
                        self.a[p, mi + 1] - self.sdin[p, mi + 1]
                        >= self.d[p, mi] + self.sdout[p, mi]
                    ).OnlyEnforceIf(self.atused[p, mi + 1])
                    # ローテーション: 連続勤務は B/D 交互
                    m.Add(self.btype[p, mi] + self.btype[p, mi + 1] == 1).OnlyEnforceIf(
                        self.atused[p, mi + 1]
                    )
                    # B から戻ったら必ず D: commit 内に D 往復が収まるなら次スロットは D 必須
                    rit = m.NewBoolVar(f"rit_{p}_{mi}")
                    m.Add(
                        self.d[p, mi] + self.sdout[p, mi] + cd.round_hours <= commit
                    ).OnlyEnforceIf(rit)
                    m.Add(
                        self.d[p, mi] + self.sdout[p, mi] + cd.round_hours >= commit + 1
                    ).OnlyEnforceIf(rit.Not())
                    m.Add(self.atused[p, mi + 1] == 1).OnlyEnforceIf([
                        self.btype[p, mi], self.leaves[p, mi], rit
                    ])

            # 最終スロットが B で commit 内に D 往復が収まるなら M 不足（B を許可しない）
            rit_last = m.NewBoolVar(f"rit_{p}_{M - 1}")
            m.Add(
                self.d[p, M - 1] + self.sdout[p, M - 1] + cd.round_hours <= commit
            ).OnlyEnforceIf(rit_last)
            m.Add(
                self.d[p, M - 1] + self.sdout[p, M - 1] + cd.round_hours >= commit + 1
            ).OnlyEnforceIf(rit_last.Not())
            m.AddBoolOr([
                self.atused[p, M - 1].Not(),
                self.btype[p, M - 1].Not(),
                rit_last.Not(),
            ])

            # 自己交代禁止
            for k in bsites:
                if k not in allowedB:
                    continue
                for j in range(J):
                    m.Add(
                        sum(self.inB[p, mi, k, j] for mi in range(M))
                        + sum(self.outB[p, mi, k, j] for mi in range(M)) <= 1
                    )
            for j in range(JCD):
                m.Add(
                    sum(self.toD[p, mi, j] for mi in range(M))
                    + sum(self.frD[p, mi, j] for mi in range(M)) <= 1
                )

        # ============ A 待機 下限（カテゴリ毎・常時） ============
        # 「派遣可能 = A 待機」= D から戻った／初期から A に居て次は B へ向かう人
        # （flow の d2b プール = anim の "Await"）。B から戻り次は D へ向かう「A 復帰」
        # （anim の "Aout"）は物理的には A に居るが派遣可能には含めない。これを満たすため、
        # 各スロットの「不在（=派遣不可）」区間を次のように定義し、カテゴリ c の同時不在数
        # ≤ 総数 - 下限 を AddCumulative で全時刻保証する（⇔ A 待機 ≥ 下限）:
        #   - 帰還しない（在勤継続）        : [A 発, H]
        #   - D 勤務スロットで帰還          : [A 発, A 帰着]（帰着後は Await=派遣可能）
        #   - B 勤務スロットで帰還          : [A 発, 次スロットの A 発]（= 区間に続く
        #                                     A 復帰 Aout も不在に含める。次が無ければ H）
        # ここで A 発 = a - sdin、A 帰着 = d + sdout。移動中も A 不在＝派遣不可側。
        # 注: ローリング handoff の C→A 移動中（初期に D から A へ帰還途中）の乗客は、
        # その到着までの区間は不在区間を持たないため A 待機側に数える（近似）。
        amin = inst.await_min_by_category
        if amin:
            away_by_cat: dict[str, list] = {}
            for p in pax:
                c = inst.category_of(p)
                if amin.get(c, 0) <= 0:
                    continue
                ivs = away_by_cat.setdefault(c, [])
                for mi in range(M):
                    lv, bt = self.leaves[p, mi], self.btype[p, mi]
                    start = m.NewIntVar(-10000, H, f"awayst_{p}_{mi}")
                    m.Add(start == self.a[p, mi] - self.sdin[p, mi])
                    end = m.NewIntVar(0, H, f"awayen_{p}_{mi}")
                    # 帰還しない: 末尾 H まで不在
                    m.Add(end == H).OnlyEnforceIf(lv.Not())
                    # D スロットで帰還（lv ∧ ¬bt）: A 帰着まで
                    d_leave = m.NewBoolVar(f"dlv_{p}_{mi}")
                    m.AddBoolAnd([lv, bt.Not()]).OnlyEnforceIf(d_leave)
                    m.AddBoolOr([lv.Not(), bt]).OnlyEnforceIf(d_leave.Not())
                    m.Add(end == self.d[p, mi] + self.sdout[p, mi]).OnlyEnforceIf(d_leave)
                    # B スロットで帰還（lv ∧ bt）: 続く A 復帰も含め次スロットの A 発まで
                    b_leave = m.NewBoolVar(f"blv_{p}_{mi}")
                    m.AddBoolAnd([lv, bt]).OnlyEnforceIf(b_leave)
                    m.AddBoolOr([lv.Not(), bt.Not()]).OnlyEnforceIf(b_leave.Not())
                    if mi < M - 1:
                        nxt = self.atused[p, mi + 1]
                        m.Add(end == self.a[p, mi + 1] - self.sdin[p, mi + 1]).OnlyEnforceIf(
                            [b_leave, nxt])
                        m.Add(end == H).OnlyEnforceIf([b_leave, nxt.Not()])
                    else:
                        m.Add(end == H).OnlyEnforceIf(b_leave)
                    size = m.NewIntVar(0, H + 10000, f"awaysz_{p}_{mi}")
                    m.Add(size == end - start)
                    ivs.append(m.NewOptionalIntervalVar(
                        start, size, end, self.atused[p, mi],
                        f"awayiv_{p}_{mi}"))
            for c, ivs in away_by_cat.items():
                total_c = sum(1 for p in pax if inst.category_of(p) == c)
                cap = total_c - amin[c]   # 同時に派遣可能でなくてよい上限（schema で cap>=0 を保証）
                m.AddCumulative(ivs, [1] * len(ivs), cap)

        # ============ 容量（トリップ単位） ============
        # B-arm は徒歩のため定員制約なし。車両定員は A↔C 便（CD-arm）のみに効く。
        self.nAC = {}
        for j in range(JCD):
            nac = m.NewIntVar(0, cap_max, f"nAC_{j}")
            m.Add(nac == sum(self.toD[p, mi, j] for p in pax for mi in range(M)))
            self.nAC[j] = nac
            frc = sum(self.frD[p, mi, j] for p in pax for mi in range(M))
            m.Add(nac <= self.capCD[j])
            m.Add(frc <= self.capCD[j])

        # ============ D 動的滞在（nAC 確定後） ============
        # 必要滞在hは「乗客の体重カテゴリ × 同乗総人数 nAC」で決まる。
        # 体重カテゴリごとに table[n] を配列化（AddElement の添字 = nAC[j]）。
        ts = inst.temporary_site
        pax_weights = {inst.weight_of(p) for p in pax}
        Lmax = max(max(ts.stay_table_for(w)) for w in pax_weights)
        weight_arr: dict[str, list[int]] = {}
        for w in pax_weights:
            arr = [0] * (Lmax + 1)
            for n, h in ts.stay_table_for(w).items():
                arr[n] = h
            weight_arr[w] = arr
        for j in range(JCD):
            reqw = {}
            for w, arr in weight_arr.items():
                rv = m.NewIntVar(0, max(arr), f"req_{w}_{j}")
                m.AddElement(self.nAC[j], arr, rv)   # rv = arr[nAC[j]]
                reqw[w] = rv
            for p in pax:
                rv = reqw[inst.weight_of(p)]
                for mi in range(M):
                    m.Add(
                        self.d[p, mi] >= self.a[p, mi] + rv
                    ).OnlyEnforceIf([self.toD[p, mi, j], self.leaves[p, mi]])

        # ============ B 常駐 / カテゴリ / 交代（イベント評価） ============
        for k in bsites:
            site = inst.staffed_sites[k]
            elig = [p for p in pax if k in inst.allowed_sites_of(p)]
            init_occ = sum(1 for p in elig
                           if inst.initial_of(p) and inst.initial_of(p).location == k)
            init_cat = {c: sum(1 for p in elig if inst.category_of(p) == c
                               and inst.initial_of(p) and inst.initial_of(p).location == k)
                        for c in cats}
            if init_occ < site.occupancy_min:
                raise ValueError(f"{k}: 初期占有 {init_occ} < min {site.occupancy_min}")
            occ_prev, cat_prev = init_occ, dict(init_cat)
            for j in range(J):
                inc = sum(self.inB[p, mi, k, j] for p in elig for mi in range(M))
                outc = sum(self.outB[p, mi, k, j] for p in elig for mi in range(M))
                occ = m.NewIntVar(0, len(elig) + init_occ, f"occ_{k}_{j}")
                m.Add(occ == occ_prev + inc - outc)
                m.Add(occ >= site.occupancy_min)
                if site.occupancy_max is not None:
                    m.Add(occ <= site.occupancy_max)
                occ_prev = occ
                ncat = {}
                for c in cats:
                    incc = sum(self.inB[p, mi, k, j] for p in elig
                               if inst.category_of(p) == c for mi in range(M))
                    outcc = sum(self.outB[p, mi, k, j] for p in elig
                                if inst.category_of(p) == c for mi in range(M))
                    cc = m.NewIntVar(0, len(elig) + init_occ, f"cc_{k}_{c}_{j}")
                    m.Add(cc == cat_prev[c] + incc - outcc)
                    if site.category_requirements.get(c, 0):
                        m.Add(cc >= site.category_requirements[c])
                    ncat[c] = cc
                cat_prev = ncat

        # ============ D 在室上限（任意） ============
        dmax = inst.temporary_site.occupancy_max
        if dmax is not None:
            # 初期 D 在室は location == D のみ。A→C 移動中（D へ向かう途中）は t=0 時点で
            # 未到着のためベースラインに含めない（到着便は当ウィンドウに無いので増分も無し）。
            init_d = sum(1 for p in pax
                         if inst.initial_of(p) and inst.initial_of(p).location == D)
            occ_prev = init_d
            for j in range(JCD):
                inc = sum(self.toD[p, mi, j] for p in pax for mi in range(M))
                outc = sum(self.frD[p, mi, j] for p in pax for mi in range(M))
                occ = m.NewIntVar(0, len(pax) + init_d, f"occD_{j}")
                m.Add(occ == occ_prev + inc - outc)
                m.Add(occ <= dmax)
                occ_prev = occ

        # ============ together（B 入域便・退域便に対称適用） ============
        # 同一 together 群は、入域(A→Bx)便でも退域(Bx→A)便でも「全員揃う or
        # 全員不在」を満たす。これにより群はペアで島に入り、ペアで島を出る
        # （spec §15, model.md §4.7）。
        for k in bsites:
            site = inst.staffed_sites[k]
            elig = [p for p in pax if k in inst.allowed_sites_of(p)]
            for group in site.ride_together:
                gl = list(group)
                for j in range(J):
                    for tag, link in (("in", self.inB), ("out", self.outB)):
                        has = {}
                        for c in group:
                            cnt = sum(link[p, mi, k, j] for p in elig
                                      if inst.category_of(p) == c for mi in range(M))
                            hc = m.NewBoolVar(f"has_{tag}_{k}_{j}_{c}")
                            m.Add(cnt >= 1).OnlyEnforceIf(hc)
                            m.Add(cnt == 0).OnlyEnforceIf(hc.Not())
                            has[c] = hc
                        for ci in range(len(gl) - 1):
                            m.Add(has[gl[ci]] == has[gl[ci + 1]])

        # ============ 目的関数 ============
        # 車両費は A↔C を走る CD-arm のみ。A↔Bx は徒歩でコストを生まない。
        terms = []
        cost_by_id = {v.id: v.hourly_cost for v in vehicles}
        for (j, vid), a in self.assignCD.items():
            # 車両費は運転区間 A→C + C→A のみ（C↔D は徒歩で配車不要）。
            terms.append(a * cd.drive_hours * cost_by_id[vid])
        m.Minimize(sum(terms))

    # ------------------------------------------------------------------
    def add_hints(self) -> None:
        """探索ヒント（warm start）。占有 min=1 の島では j 番目のハンドオーバが
        概ね一定周期で並ぶ骨格を与え、長 horizon での feasible 発見を助ける。"""
        m, inst, H = self.m, self.inst, self.H
        for k in self.bsites:
            site = inst.staffed_sites[k]
            din = site.segments.inbound_hours
            smax = site.stay.max_hours
            cadence = max(1, int(smax * 0.8))   # 余裕をもった交代間隔
            n = min(self.J, max(1, self.commit // cadence))
            for j in range(self.J):
                if j < n:
                    m.AddHint(self.usedB[k, j], 1)
                    arr = min((j + 1) * cadence, self.commit - 1)
                    m.AddHint(self.depB[k, j], max(0, arr - din))
                else:
                    m.AddHint(self.usedB[k, j], 0)

    def solve(self, hint: bool = True,
              callback: cp_model.CpSolverSolutionCallback | None = None) -> "FullSolution":
        if hint:
            self.add_hints()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.inst.solver.max_seconds
        solver.parameters.num_search_workers = 8
        status = (solver.Solve(self.m, callback) if callback is not None
                  else solver.Solve(self.m))
        return FullSolution(self, solver, status)


class FullSolution:
    def __init__(self, model: FullModel, solver, status):
        self.model, self.solver, self.status = model, solver, status

    @property
    def ok(self) -> bool:
        return self.status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    def summary(self) -> str:
        s, mdl, inst = self.solver, self.model, self.model.inst
        lines = [f"status: {s.StatusName(self.status)}"]
        if not self.ok:
            return "\n".join(lines)
        lines.append(f"objective (total vehicle cost): {s.ObjectiveValue():.0f}")
        pax = [p.id for p in inst.passengers]
        lines.append("--- B handovers (徒歩, 配車なし) ---")
        for k in mdl.bsites:
            din = inst.staffed_sites[k].segments.inbound_hours
            for j in range(mdl.J):
                if s.Value(mdl.usedB[k, j]):
                    ins = [p for p in pax for mi in range(mdl.M)
                           if (p, mi, k, j) in mdl.inB and s.Value(mdl.inB[p, mi, k, j])]
                    outs = [p for p in pax for mi in range(mdl.M)
                            if (p, mi, k, j) in mdl.outB and s.Value(mdl.outB[p, mi, k, j])]
                    lines.append(f"  {k} t{j}: depA@{s.Value(mdl.depB[k,j])} "
                                 f"arr{k}@{s.Value(mdl.depB[k,j])+din} in={ins} out={outs}")
        lines.append("--- CD-arm trips ---")
        for j in range(mdl.JCD):
            if s.Value(mdl.usedCD[j]):
                veh = next(v.id for v in mdl.vehicles
                           if (j, v.id) in mdl.assignCD
                           and s.Value(mdl.assignCD[j, v.id]))
                td = [p for p in pax for mi in range(mdl.M)
                      if s.Value(mdl.toD[p, mi, j])]
                fd = [p for p in pax for mi in range(mdl.M)
                      if s.Value(mdl.frD[p, mi, j])]
                lines.append(f"  CD t{j}: depA@{s.Value(mdl.depCD[j])} "
                             f"arrD@{s.Value(mdl.depCD[j])+inst.cd_arm.to_d_hours} "
                             f"veh={veh} toD={td} frD={fd} nAC={s.Value(mdl.nAC[j])}")
        lines.append("--- passenger visits ---")
        for p in pax:
            for mi in range(mdl.M):
                if s.Value(mdl.atused[p, mi]):
                    site = next(kk for kk in (mdl.bsites + [D]) if s.Value(mdl.at[p, mi, kk]))
                    a = s.Value(mdl.a[p, mi])
                    left = s.Value(mdl.leaves[p, mi])
                    when = f"dep@{s.Value(mdl.d[p,mi])}" if left else "stays"
                    lines.append(f"  {p} v{mi}: {site} arr@{a} {when}")
        return "\n".join(lines)
