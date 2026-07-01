"""時間展開フローモデル（固定ダイヤ前提）。

model.py の連続時間・個体ワーカー定式化を、ダイヤグリッド上の「互換クラス内匿名フロー」へ
置き換えた代替ソルバ。ワーカーをコモディティ=(B サイト, カテゴリ, 体重) 単位の整数フローとして
扱い、個体識別は求解後の経路分解（decode）で復元する。連続時間の自由変数・ワーカー対称性・
弱い下界を排し、長 horizon を単発で扱える（設計根拠と検証は BENCH.md 7章）。

対応:
  - 固定ダイヤ（保有車両ごとの a_c_departures）必須。A→C/C→A は便、A↔Bx は徒歩。
    便ダイヤは全保有車両の和、各時刻スロットの提供定員は「その時刻に出発する
    保有車両」で決まる（owned を増やすとその時刻の定員が増える）。
  - 多島（島別 occupancy_min / category_requirements / B 滞在ウィンドウ[min,max] / 乗降所要）。
  - 動的 D 滞在: 便を積載クラス z[tau,n] に分割、滞在 = temporary_site.required_hours(weight,n)。
    復路便は「滞在を満たす最早の便」へスナップ（一般ダイヤ・任意滞在に対応）。
  - ride_together（サイトのカテゴリグループを便単位で全乗船 or 全不在）、D 同時在室上限。
  - 初期状態: B サイト在室 / A 待機。車両はタイプ別に台数選択しコスト最小化。
未対応（明示エラー）:
  - ダイヤ未指定 / 1 乗客が複数 B サイト適格 / 初期 location が D・島間移動中。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .loader import hour_offset
from .schema import Instance

D = "D"


class FlowUnsupported(ValueError):
    """フローモデルが未対応のインスタンス機能。"""


@dataclass
class _Site:
    name: str
    occ_min: int
    occ_max: int | None
    cat_req: dict           # category -> 必要数
    smin: int
    smax: int
    din: int                # A→site 徒歩
    dout: int               # site→A 徒歩
    together: list          # list[tuple[category,...]]


class FlowModel:
    def __init__(self, inst: Instance):
        self.inst = inst
        self._validate()
        self.H = inst.planning_horizon.hours
        self.commit = inst.solver.commit_hours or self.H
        self._setup()
        self._build()

    # ------------------------------------------------------------------
    def _validate(self):
        inst = self.inst
        if inst.cd_arm is None or inst.temporary_site is None:
            raise FlowUnsupported("flow は cd_arm と temporary_site が必要")
        if not inst.fleet.has_timetable():
            raise FlowUnsupported(
                "flow は固定ダイヤ（保有車両の a_c_departures）が必須。"
                "いずれの車両にもダイヤが無い（自由ダイヤ）は非対応")
        for p in inst.passengers:
            allowed = inst.allowed_sites_of(p.id)
            if len(allowed) != 1:
                raise FlowUnsupported(
                    f"乗客 {p.id} の allowed_sites は単一 B サイトのみ対応（実際: {allowed}）")
        for st in inst.initial_state:
            if st.location not in (set(inst.staffed_sites) | {"A"}):
                raise FlowUnsupported(
                    f"初期 location '{st.location}' は未対応（B サイト在室 / A 待機のみ）")

    def _setup(self):
        inst = self.inst
        cd = inst.cd_arm
        self.a_c, self.c_d, self.d_c, self.c_a = (
            cd.a_c_hours, cd.c_d_hours, cd.d_c_hours, cd.c_a_hours)
        self.to_d = self.a_c + self.c_d
        self.drive = cd.drive_hours
        # ダイヤ展開（horizon 全域）: 便ダイヤ = 各保有車両の a_c_departures の和。
        # avail[(t, vtype)] = 時刻 t に出発する保有車両のうちタイプ vtype の台数。
        # 各スロットの提供定員はこの台数で上限が決まる（owned が便の定員を規定）。
        self.avail: dict[tuple[int, str], int] = {}
        fset: set[int] = set()
        for ov in inst.fleet.owned:
            for tau in sorted(set(ov.a_c_departures)):
                for d in range(self.H // 24 + 1):
                    t = d * 24 + tau
                    if 0 <= t <= self.H:
                        fset.add(t)
                        self.avail[t, ov.type] = self.avail.get((t, ov.type), 0) + 1
        self.ferries = sorted(fset)
        self.fset = fset
        # サイト
        self.sites: dict[str, _Site] = {}
        for name, s in inst.staffed_sites.items():
            self.sites[name] = _Site(
                name=name, occ_min=s.occupancy_min, occ_max=s.occupancy_max,
                cat_req=dict(s.category_requirements),
                smin=s.stay.min_hours, smax=s.stay.max_hours,
                din=s.segments.inbound_hours, dout=s.segments.outbound_hours,
                together=[tuple(g) for g in s.ride_together])
        # コモディティ = (site, category, weight)
        self.comm: dict[tuple, list[str]] = {}
        self.pax_comm: dict[str, tuple] = {}
        for p in inst.passengers:
            site = inst.allowed_sites_of(p.id)[0]
            key = (site, p.category, p.weight)
            self.comm.setdefault(key, []).append(p.id)
            self.pax_comm[p.id] = key
        # 初期内訳（コモディティ別の B 在室 / A 待機）
        self.init_B: dict[tuple, int] = {k: 0 for k in self.comm}
        self.init_A: dict[tuple, int] = {k: 0 for k in self.comm}
        for k, pids in self.comm.items():
            for pid in pids:
                st = inst.initial_of(pid)
                if st is not None and st.location == k[0]:
                    self.init_B[k] += 1
                else:
                    self.init_A[k] += 1
        # D 滞在表（weight, n）
        ts = inst.temporary_site
        # cap_max は「実際に配車される車両（fleet.owned）」の最大定員で決める。
        # vehicle_types 全体ではなく owned に存在する型のみを見るため、truck を
        # owned に入れれば自動で truck 定員、ミニバンだけなら minivan 定員になる。
        # これは下界カット（便数 >= ceil(T/cap_max)）と board グループ上限の両方を
        # 実態に合わせて締める（owned に居ない大型車で下界が緩むのを防ぐ）。
        owned_types = {ov.type for ov in inst.fleet.owned}
        self.cap_max = max(inst.vehicle_types[t].capacity for t in owned_types)
        self.weights = sorted({k[2] for k in self.comm})
        self.dstay = {(w, n): ts.required_hours(w, n)
                      for w in self.weights for n in range(1, self.cap_max + 1)}
        self.d_occ_max = ts.occupancy_max
        # 車両タイプ（capacity, hourly_cost）と保有台数
        self.vtypes = {name: (vt.capacity, vt.cost_per_hour)
                       for name, vt in inst.vehicle_types.items()}

    # 復路便スナップ: 出発 tau・滞在 S を満たす最早の便と A 帰着時刻
    def _return(self, tau: int, S: int):
        tmin = tau + self.c_d + self.d_c + S        # 復路便 C 折返しの最早時刻
        cand = [t for t in self.ferries if t >= tmin]
        if not cand:
            return None, None
        t_ret = cand[0]
        home = t_ret + self.a_c + self.c_a
        return t_ret, home

    # ------------------------------------------------------------------
    def _build(self):
        m = cp_model.CpModel()
        self.m = m
        H, commit = self.H, self.commit
        comm = self.comm
        Wn = {k: len(v) for k, v in comm.items()}

        # ---- 便: 積載クラス z[tau,n] と コモディティ内訳 bo[k,tau,n] ----
        # 往復が commit 内に収まる便のみ出発に使える。
        out_ok = []
        ret_of = {}        # (tau, w, n) -> (t_ret, home)
        for tau in self.ferries:
            ok_any = False
            for w in self.weights:
                for n in range(1, self.cap_max + 1):
                    S = self.dstay.get((w, n), 0)
                    t_ret, home = self._return(tau, S)
                    if t_ret is not None and home <= commit:
                        ret_of[tau, w, n] = (t_ret, home)
                        ok_any = True
            if ok_any:
                out_ok.append(tau)
        self.out_ok = out_ok

        z, bo = {}, {}
        for tau in out_ok:
            ns = [n for n in range(1, self.cap_max + 1)
                  if all((tau, w, n) in ret_of for w in self.weights)]
            for n in ns:
                z[tau, n] = m.NewBoolVar(f"z_{tau}_{n}")
                for k in comm:
                    bo[k, tau, n] = m.NewIntVar(0, Wn[k], f"bo_{k[0]}_{k[1]}_{k[2]}_{tau}_{n}")
                m.Add(sum(bo[k, tau, n] for k in comm) == n * z[tau, n])
            if ns:
                m.Add(sum(z[tau, n] for n in ns) <= 1)
        self.z, self.bo = z, bo

        def board(k, tau):
            ts = [bo[k, tau, n] for n in range(1, self.cap_max + 1) if (k, tau, n) in bo]
            return sum(ts) if ts else 0

        # ---- B 在室（コモディティ別, 滞在ウィンドウは累積制約）----
        ein = {(k, g): m.NewIntVar(0, Wn[k], f"ein_{k[0]}_{k[1]}_{k[2]}_{g}")
               for k in comm for g in range(H + 1)}
        eout = {(k, g): m.NewIntVar(0, Wn[k], f"eout_{k[0]}_{k[1]}_{k[2]}_{g}")
                for k in comm for g in range(H + 1)}
        # 早期自由入場の抑止: din 未満では A から歩いて入れない（初期 B 在室は別途 baseline）。
        for k in comm:
            for g in range(min(self.sites[k[0]].din, H + 1)):
                m.Add(ein[k, g] == 0)
        Ein = {}; Eout = {}; Bocc = {}
        for k in comm:
            site = self.sites[k[0]]
            ce = 0; co = 0
            for g in range(H + 1):
                ce = ce + ein[k, g] + (self.init_B[k] if g == 0 else 0)
                co = co + eout[k, g]
                Ein[k, g], Eout[k, g] = ce, co
            for g in range(H + 1):
                # B 在室 = 累積入 - 累積出 >= 0
                b = m.NewIntVar(0, Wn[k] + self.init_B[k], f"B_{k[0]}_{k[1]}_{k[2]}_{g}")
                m.Add(b == Ein[k, g] - Eout[k, g])
                Bocc[k, g] = b
                # 滞在ウィンドウ: 入って min 未満では出られない / max までに出る
                gm = g - site.smin
                if gm >= 0:
                    m.Add(Eout[k, g] <= Ein[k, gm])
                else:
                    m.Add(Eout[k, g] == 0)
                gM = g - site.smax
                if gM >= 0:
                    m.Add(Eout[k, g] >= Ein[k, gM])
        self.Bocc = Bocc
        self.ein, self.eout = ein, eout

        # 占有 + カテゴリ要件（サイト単位の総和 / カテゴリ別）
        for sname, site in self.sites.items():
            ks = [k for k in comm if k[0] == sname]
            for g in range(commit):
                if site.occ_min:
                    m.Add(sum(Bocc[k, g] for k in ks) >= site.occ_min)
                if site.occ_max is not None:
                    m.Add(sum(Bocc[k, g] for k in ks) <= site.occ_max)
                for c, req in site.cat_req.items():
                    if req:
                        kc = [k for k in ks if k[1] == c]
                        m.Add(sum(Bocc[k, g] for k in kc) >= req)

        # ---- プール（D→B 待機 / B→D 待機）コモディティ別保存則 ----
        d2b = {(k, g): m.NewIntVar(0, Wn[k], f"d2b_{k[0]}_{k[1]}_{k[2]}_{g}")
               for k in comm for g in range(-1, H + 1)}
        b2d = {(k, g): m.NewIntVar(0, Wn[k], f"b2d_{k[0]}_{k[1]}_{k[2]}_{g}")
               for k in comm for g in range(-1, H + 1)}
        for k in comm:
            m.Add(d2b[k, -1] == self.init_A[k])
            m.Add(b2d[k, -1] == 0)
        # 復路 A 帰着（d2b refill）を時刻別に集計
        refill = {(k, g): [] for k in comm for g in range(H + 1)}
        for (tau, w, n), (t_ret, home) in ret_of.items():
            if home <= H:
                for k in comm:
                    if k[2] == w and (k, tau, n) in bo:
                        refill[k, home].append(bo[k, tau, n])
        for k in comm:
            site = self.sites[k[0]]
            for g in range(H + 1):
                # d2b: 復路帰着で増え、B 入場(ein は din 前に A を発つ)で減る
                leave = ein[k, g + site.din] if (k, g + site.din) in ein else 0
                rf = refill[k, g]
                m.Add(d2b[k, g] == d2b[k, g - 1] + (sum(rf) if rf else 0) - leave)
                # b2d: B 退出(dout 後 A 帰着)で増え、便乗船で減る
                arr = eout[k, g - site.dout] if (k, g - site.dout) in eout else 0
                m.Add(b2d[k, g] == b2d[k, g - 1] + arr - board(k, g))
        self.d2b, self.b2d = d2b, b2d

        # ---- A 待機 下限（カテゴリ毎・常時） ----
        # 「派遣可能 = A 待機」= D から戻った／初期から A に居て B 未派遣のプール d2b
        # （= anim の "Await"。B から戻り D 待ちの b2d="A 復帰"/"Aout" は含めない）。
        # カテゴリ毎に各時刻 g で当該カテゴリの d2b 総和 >= 指定人数 を課す。
        amin = self.inst.await_min_by_category
        if amin:
            for c, n in amin.items():
                if n <= 0:
                    continue
                ks = [k for k in comm if k[1] == c]
                for g in range(commit):
                    m.Add(sum(d2b[k, g] for k in ks) >= n)

        # ---- 車両（便ごとにタイプ別台数を選択）----
        # 各便 tau の台数上限は avail[(tau, vt)]＝その時刻に出発する保有車両の台数。
        # 出発ダイヤを持たないタイプは上限 0（= その便では使えない）。
        nveh = {}
        for tau in out_ok:
            for vt in self.vtypes:
                nveh[tau, vt] = m.NewIntVar(
                    0, self.avail.get((tau, vt), 0), f"veh_{tau}_{vt}")
        # 往路・復路積載 <= 提供定員。op は「便 tau の往路運行有無」。
        for tau in out_ok:
            cap_here = sum(self.vtypes[vt][0] * nveh[tau, vt] for vt in self.vtypes)
            outload = sum(bo[k, tau, n] for k in comm
                          for n in range(1, self.cap_max + 1) if (k, tau, n) in bo)
            m.Add(outload <= cap_here)
        # 復路積載 <= その復路便のタイプ別定員（復路便 t に乗る人を集計）
        retload = {t: [] for t in self.ferries}
        for (tau, w, n), (t_ret, home) in ret_of.items():
            for k in comm:
                if k[2] == w and (k, tau, n) in bo:
                    retload[t_ret].append(bo[k, tau, n])
        for t in self.ferries:
            if retload[t] and t in out_ok:
                cap_here = sum(self.vtypes[vt][0] * nveh[t, vt] for vt in self.vtypes)
                m.Add(sum(retload[t]) <= cap_here)
            elif retload[t]:
                # 復路のみで往路に使わない便でも車両が要る
                for vt in self.vtypes:
                    nveh.setdefault(
                        (t, vt), m.NewIntVar(0, self.avail.get((t, vt), 0),
                                             f"veh_{t}_{vt}"))
                cap_here = sum(self.vtypes[vt][0] * nveh[t, vt] for vt in self.vtypes)
                m.Add(sum(retload[t]) <= cap_here)
        self.nveh = nveh

        # ride_together（サイトのカテゴリグループを便単位で揃える）
        for sname, site in self.sites.items():
            for grp in site.together:
                for tau in out_ok:
                    has = {}
                    for c in grp:
                        ks = [k for k in comm if k[0] == sname and k[1] == c]
                        cnt = sum(board(k, tau) for k in ks)
                        hv = m.NewBoolVar(f"tg_{sname}_{c}_{tau}")
                        if isinstance(cnt, int):
                            continue
                        m.Add(cnt >= 1).OnlyEnforceIf(hv)
                        m.Add(cnt == 0).OnlyEnforceIf(hv.Not())
                        has[c] = hv
                    hs = list(has.values())
                    for a, b in zip(hs, hs[1:]):
                        m.Add(a == b)

        # D 同時在室上限（任意, 全コモディティ総和）
        if self.d_occ_max is not None:
            for g in range(H + 1):
                atD = []
                for (tau, w, n), (t_ret, home) in ret_of.items():
                    arrD = tau + self.to_d
                    leaveD = t_ret + (self.a_c - self.d_c)
                    if arrD <= g < leaveD:
                        for k in comm:
                            if k[2] == w and (k, tau, n) in bo:
                                atD.append(bo[k, tau, n])
                if atD:
                    m.Add(sum(atD) <= self.d_occ_max)

        # 下界カット（安全版 N>=ceil(T/cap)。BENCH 7.7: これ以上は列生成が必要）
        T = 0
        for sname, site in self.sites.items():
            nb = math.ceil(max(0, commit) / max(1, site.smin)) * max(1, site.occ_min)
            Wsite = sum(Wn[k] for k in comm if k[0] == sname)
            T += max(0, nb - Wsite)
        if T:
            allop = [self.nveh[t, vt] for (t, vt) in self.nveh]
            m.Add(sum(allop) >= math.ceil(T / self.cap_max))

        # 目的: 車両費 = Σ 台数 * drive * hourly
        terms = [self.nveh[t, vt] * self.drive * self.vtypes[vt][1]
                 for (t, vt) in self.nveh]
        self.obj = sum(terms)          # K-best 列挙で objective>=lb を課すため保持
        m.Minimize(self.obj)
        self.ret_of = ret_of

    # ------------------------------------------------------------------
    def solve(self, max_seconds: float | None = None, workers: int = 8,
              callback=None) -> "FlowSolution":
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = max_seconds or self.inst.solver.max_seconds
        s.parameters.num_search_workers = workers
        # 相対ギャップ許容（>0 なら early stop で OPTIMAL 扱い）。
        if self.inst.solver.relative_gap > 0:
            s.parameters.relative_gap_limit = self.inst.solver.relative_gap
        # callback（SolutionRecorder）を渡すと改善解の列を記録できる（GUI の改善グラフ用）。
        st = s.Solve(self.m, callback) if callback is not None else s.Solve(self.m)
        return FlowSolution(self, s, st)


@dataclass
class FlowSolution:
    model: FlowModel
    solver: object
    status: int

    @property
    def ok(self) -> bool:
        return self.status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    def decode(self) -> dict[str, list[dict]]:
        """コモディティ別に経路分解し、実乗客 id にタイムラインを割り当てて返す。
        モデルの実 ein/eout/bo 値を使い、入場↔退出を FIFO で対応付けて個体を復元する
        （滞在ウィンドウの可変退出を忠実に再構成し、占有を保つ）。"""
        import heapq
        from collections import deque
        mdl, s = self.model, self.solver
        out: dict[str, list[dict]] = {pid: [] for pid in mdl.pax_comm}
        for k, pids in mdl.comm.items():
            site = mdl.sites[k[0]]
            W = len(pids)
            # 初期: A 待機者は ready_B, B 在室者は inB(入場時刻0)。
            ready_B = [(0, i) for i in range(mdl.init_A[k])]; heapq.heapify(ready_B)
            inB = deque((0, i) for i in range(mdl.init_A[k], W))   # (入場時刻, worker)
            ready_D: list = []
            tl: dict[int, list] = {i: [] for i in range(W)}
            cur_B: dict[int, dict] = {}     # worker -> 現在の B レコード
            for _, i in inB:                # 初期 B 在室者の初回勤務レコードを作る
                rec = {'kind': 'B', 'site': k[0], 'arrive': 0, 'depart': None}
                tl[i].append(rec); cur_B[i] = rec
            events = []
            for g in range(mdl.H + 1):
                for _ in range(int(s.Value(mdl.ein[k, g]))):
                    events.append((g, 2, 'ein', None))
                for _ in range(int(s.Value(mdl.eout[k, g]))):
                    events.append((g, 0, 'eout', None))
            for (kk, tau, n) in mdl.bo:
                if kk == k:
                    for _ in range(int(s.Value(mdl.bo[k, tau, n]))):
                        events.append((tau, 1, 'board', n))
            events.sort(key=lambda e: (e[0], e[1]))
            for g, _, kind, n in events:
                if kind == 'ein':
                    # B 入場(g)は A を g-din に発つ ⇒ A 到着時刻 <= g-din の worker のみ可。
                    if not ready_B or ready_B[0][0] > g - site.din:
                        continue
                    _, i = heapq.heappop(ready_B)
                    rec = {'kind': 'B', 'site': k[0], 'arrive': g, 'depart': None}
                    tl[i].append(rec); cur_B[i] = rec
                    inB.append((g, i))
                elif kind == 'eout':
                    if not inB:
                        continue
                    _, i = inB.popleft()           # FIFO(最古入場) = 窓を必ず満たす
                    if i in cur_B:
                        cur_B[i]['depart'] = g; cur_B.pop(i)
                    heapq.heappush(ready_D, (g + site.dout, i))   # A 到着 = 退出+dout
                else:  # board: A 到着時刻 <= tau の worker のみ乗船可
                    if not ready_D or ready_D[0][0] > g:
                        continue
                    _, i = heapq.heappop(ready_D)
                    t_ret, home = mdl.ret_of[g, k[2], n]
                    tl[i].append({'kind': 'D', 'board': g, 'load': n,
                                  'arriveD': g + mdl.to_d, 'returnA': home})
                    heapq.heappush(ready_B, (home, i))   # 復路 A 帰着で再び入場可
            # 末端で B 在室のまま(退出未割当)の worker は depart を horizon 末に
            for i, rec in cur_B.items():
                if rec['depart'] is None:
                    rec['depart'] = mdl.H
            # worker スロット [0,init_A)=A 始発, [init_A,W)=B 始発。乗客を初期状態で対応付ける。
            a_pids = [pid for pid in pids
                      if (mdl.inst.initial_of(pid) is None
                          or mdl.inst.initial_of(pid).location != k[0])]
            b_pids = [pid for pid in pids
                      if mdl.inst.initial_of(pid) is not None
                      and mdl.inst.initial_of(pid).location == k[0]]
            ordered = a_pids + b_pids                # ready_B 由来 → inB 由来 の順
            for i, pid in enumerate(ordered):
                out[pid] = tl[i]
        return out

    def summary(self) -> str:
        from .model import optimality_note, search_stat_lines
        s, mdl = self.solver, self.model
        lines = [f"status: {s.StatusName(self.status)}"]
        if not self.ok:
            lines += search_stat_lines(s)
            return "\n".join(lines)
        c, b = s.ObjectiveValue(), s.BestObjectiveBound()
        gap = (c - b) / c * 100 if c else 0.0
        lines.append(f"objective (vehicle cost): {c:.0f}")
        lines.append(f"bound: {b:.0f}")
        lines.append(f"gap: {gap:.1f}%")
        nf = sum(int(s.Value(mdl.nveh[t, vt])) for (t, vt) in mdl.nveh)
        lines.append(f"vehicle-trips: {nf}")
        lines += search_stat_lines(s)
        lines.append(optimality_note(self.status, gap))
        return "\n".join(lines)


def k_best_costs(inst: Instance, k: int = 5, seconds_each: float = 15.0,
                 workers: int = 8) -> list[dict]:
    """採用解を含め、達成可能なコスト水準を安い順に最大 k 個列挙する。

    各段で「目的関数 >= 前段コスト + 1」を課して再求解し、次に安い実行可能コストを
    得る（同一モデルへ制約を追記して再ソルブ＝再構築なしで高速）。返り値は
    [{rank, cost, status, wall, branches}]。status=OPTIMAL の段は「そのコストが
    その下限以下で最小」と証明済み。INFEASIBLE に達したら（=それ以上高い解は無い）
    列挙を打ち切る。「他の候補はどれも採用解より高い」ことを可視化する用途。
    """
    mdl = FlowModel(inst)
    rows: list[dict] = []
    for rank in range(1, k + 1):
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = seconds_each
        s.parameters.num_search_workers = workers
        st = s.Solve(mdl.m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break                       # これより高いコストの解は存在しない
        c = int(round(s.ObjectiveValue()))
        rows.append({"rank": rank, "cost": c, "status": s.StatusName(st),
                     "wall": round(s.WallTime(), 1), "branches": s.NumBranches()})
        mdl.m.Add(mdl.obj >= c + 1)     # 次段はこれより高いコストのみ
    return rows
