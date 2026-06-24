"""B-arm（A→B→A 交代輸送）の CP-SAT モデル。

model.md v0.2 の §3/§4 を B-arm 部分集合に限定して実装する。
2層構造:
  (L1) トリップ層: 各島 k への往復トリップ j（used / 車両割当 / 出発時刻）
  (L2) 乗客訪問層: 各乗客の訪問スロット m（at[p,m,k] / 到着 a / 出発 d）
結合: inB[p,m,k,j], outB[p,m,k,j]

スライス特有の確定事項:
  - 滞在 max は「期限 a+max が horizon 内に収まる場合のみ」帰還を強制する
    （期限が H を超える最後の常駐者は計画末まで残れる）。model.md §7 Q の確定。
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from .loader import holiday_hour_intervals, hour_offset
from .schema import Instance


@dataclass
class _Vehicle:
    id: str
    vtype: str
    capacity: int
    hourly_cost: int


def _build_vehicles(inst: Instance) -> list[_Vehicle]:
    vs: list[_Vehicle] = []
    for ov in inst.fleet.owned:
        vt = inst.vehicle_types[ov.type]
        vs.append(_Vehicle(ov.id, ov.type, vt.capacity, vt.cost_per_hour))
    return vs


class BArmModel:
    def __init__(self, inst: Instance):
        self.inst = inst
        self.m = cp_model.CpModel()
        self.H = inst.planning_horizon.hours
        self.sites = list(inst.staffed_sites.keys())
        self.vehicles = _build_vehicles(inst)
        self.holidays = holiday_hour_intervals(inst)
        self.M = inst.solver.max_visits_per_passenger
        self.J = inst.solver.trips_per_site
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        m, inst, H = self.m, self.inst, self.H
        sites, vehicles = self.sites, self.vehicles
        M, J = self.M, self.J
        pax = [p.id for p in inst.passengers]

        # ---- L1: トリップ変数 ----
        # used[k,j], dep[k,j], assignV[k,j,v]
        self.used: dict = {}
        self.dep: dict = {}
        self.assignV: dict = {}
        self.cap_trip: dict = {}
        # 車両ごとの optional interval（NoOverlap 用）
        veh_intervals: dict = {v.id: [] for v in vehicles}

        for k in sites:
            din = inst.staffed_sites[k].segments.inbound_hours
            dout = inst.staffed_sites[k].segments.outbound_hours
            trip_h = din + dout
            for j in range(J):
                used = m.NewBoolVar(f"used_{k}_{j}")
                dep = m.NewIntVar(0, H, f"dep_{k}_{j}")
                self.used[k, j] = used
                self.dep[k, j] = dep
                # 未使用トリップは dep=H に固定（時系列の末尾へ）
                m.Add(dep == H).OnlyEnforceIf(used.Not())
                # 使用トリップは horizon 内に往復完了
                m.Add(dep + trip_h <= H).OnlyEnforceIf(used)

                # 車両割当
                avs = []
                for v in vehicles:
                    a = m.NewBoolVar(f"assign_{k}_{j}_{v.id}")
                    self.assignV[k, j, v.id] = a
                    avs.append(a)
                    # optional interval [dep, dep+trip_h]
                    iv = m.NewOptionalIntervalVar(
                        dep, trip_h, dep + trip_h, a, f"iv_{k}_{j}_{v.id}"
                    )
                    veh_intervals[v.id].append(iv)
                m.Add(sum(avs) == used)  # 使用なら丁度1台

                # 定員（割当車両の容量）
                cap = m.NewIntVar(0, max(v.capacity for v in vehicles), f"cap_{k}_{j}")
                m.Add(cap == sum(self.assignV[k, j, v.id] * v.capacity for v in vehicles))
                self.cap_trip[k, j] = cap

                # 休日に運行区間が重ならない
                for (hs, he) in self.holidays:
                    before = m.NewBoolVar(f"hol_before_{k}_{j}_{hs}")
                    after = m.NewBoolVar(f"hol_after_{k}_{j}_{hs}")
                    m.Add(dep + trip_h <= hs).OnlyEnforceIf(before)
                    m.Add(dep >= he).OnlyEnforceIf(after)
                    m.AddBoolOr([before, after]).OnlyEnforceIf(used)

            # 対称性除去＋used 先詰め（時系列＝index 順を保証）
            for j in range(J - 1):
                m.Add(self.used[k, j] >= self.used[k, j + 1])
                m.Add(self.dep[k, j] <= self.dep[k, j + 1])

        # 同一車両の運行区間は重ならない
        for v in vehicles:
            m.AddNoOverlap(veh_intervals[v.id])

        # ---- L2: 乗客訪問変数 ----
        # at[p,m,k], a[p,m], d[p,m], leaves[p,m]
        self.at: dict = {}
        self.atused: dict = {}
        self.a: dict = {}
        self.d: dict = {}
        self.leaves: dict = {}
        self.slot_din: dict = {}
        self.slot_dout: dict = {}
        self.inB: dict = {}
        self.outB: dict = {}

        for p in pax:
            allowed = set(inst.allowed_sites_of(p))
            init = inst.initial_of(p)
            init_site = init.location if (init and init.location in sites) else None
            init_off = hour_offset(inst, init.arrived_at) if (init and init.arrived_at) else 0

            for mi in range(M):
                a = m.NewIntVar(-10000, H, f"a_{p}_{mi}")
                d = m.NewIntVar(-10000, H + 10000, f"d_{p}_{mi}")
                self.a[p, mi] = a
                self.d[p, mi] = d
                atu = m.NewBoolVar(f"atused_{p}_{mi}")
                self.atused[p, mi] = atu
                leaves = m.NewBoolVar(f"leaves_{p}_{mi}")
                self.leaves[p, mi] = leaves
                m.Add(leaves <= atu)  # 不在なら帰還もない

                site_bools = []
                for k in sites:
                    at = m.NewBoolVar(f"at_{p}_{mi}_{k}")
                    self.at[p, mi, k] = at
                    site_bools.append(at)
                    if k not in allowed:
                        m.Add(at == 0)  # 適格性
                m.Add(sum(site_bools) == atu)  # 使用スロットは丁度1サイト

                # スロットのサイト依存所要（A 往復の連続性チェック用）
                maxdur = max(
                    (s.segments.inbound_hours + s.segments.outbound_hours)
                    for s in inst.staffed_sites.values()
                )
                sdin = m.NewIntVar(0, maxdur, f"sdin_{p}_{mi}")
                sdout = m.NewIntVar(0, maxdur, f"sdout_{p}_{mi}")
                m.Add(sdin == sum(
                    self.at[p, mi, k] * inst.staffed_sites[k].segments.inbound_hours
                    for k in sites
                ))
                m.Add(sdout == sum(
                    self.at[p, mi, k] * inst.staffed_sites[k].segments.outbound_hours
                    for k in sites
                ))
                self.slot_din[p, mi] = sdin
                self.slot_dout[p, mi] = sdout

                # 結合変数 inB/outB
                for k in sites:
                    if k not in allowed:
                        continue
                    din = inst.staffed_sites[k].segments.inbound_hours
                    for j in range(J):
                        inb = m.NewBoolVar(f"inB_{p}_{mi}_{k}_{j}")
                        outb = m.NewBoolVar(f"outB_{p}_{mi}_{k}_{j}")
                        self.inB[p, mi, k, j] = inb
                        self.outB[p, mi, k, j] = outb
                        m.Add(inb <= self.at[p, mi, k])
                        m.Add(outb <= self.at[p, mi, k])
                        m.Add(inb <= self.used[k, j])
                        m.Add(outb <= self.used[k, j])
                        # 時刻連動（島での乗降は dep+din の同時刻）
                        m.Add(a == self.dep[k, j] + din).OnlyEnforceIf(inb)
                        m.Add(d == self.dep[k, j] + din).OnlyEnforceIf(outb)

            # スロット先詰め
            for mi in range(M - 1):
                m.Add(self.atused[p, mi] >= self.atused[p, mi + 1])

            # 初期常駐者: スロット0 を初期サイトに固定、到着便なし
            if init_site is not None:
                m.Add(self.at[p, 0, init_site] == 1)
                m.Add(self.a[p, 0] == init_off)

            for mi in range(M):
                is_init_slot = init_site is not None and mi == 0
                # 入域便: 初期常駐スロットは到着便なし、それ以外は使用時に丁度1便
                insum = sum(
                    self.inB[p, mi, k, j]
                    for k in sites if k in allowed for j in range(J)
                )
                if is_init_slot:
                    m.Add(insum == 0)
                else:
                    m.Add(insum == self.atused[p, mi])
                # 出域便: 帰還する場合のみ丁度1便
                outsum = sum(
                    self.outB[p, mi, k, j]
                    for k in sites if k in allowed for j in range(J)
                )
                m.Add(outsum == self.leaves[p, mi])

                # 滞在時間（帰還する場合のみ）
                for k in sites:
                    if k not in allowed:
                        continue
                    smin = inst.staffed_sites[k].stay.min_hours
                    smax = inst.staffed_sites[k].stay.max_hours
                    here_and_leaves = m.NewBoolVar(f"hl_{p}_{mi}_{k}")
                    m.AddBoolAnd([self.at[p, mi, k], self.leaves[p, mi]]).OnlyEnforceIf(here_and_leaves)
                    m.AddBoolOr([self.at[p, mi, k].Not(), self.leaves[p, mi].Not()]).OnlyEnforceIf(here_and_leaves.Not())
                    m.Add(self.d[p, mi] >= self.a[p, mi] + smin).OnlyEnforceIf(here_and_leaves)
                    m.Add(self.d[p, mi] <= self.a[p, mi] + smax).OnlyEnforceIf(here_and_leaves)

                    # 期限が horizon 内なら帰還強制
                    deadline_in = m.NewBoolVar(f"dlin_{p}_{mi}_{k}")
                    m.Add(self.a[p, mi] + smax <= H).OnlyEnforceIf(deadline_in)
                    m.Add(self.a[p, mi] + smax >= H + 1).OnlyEnforceIf(deadline_in.Not())
                    # at_k ∧ deadline_in ⇒ leaves
                    m.AddBoolOr(
                        [self.at[p, mi, k].Not(), deadline_in.Not(), self.leaves[p, mi]]
                    )

                # スロット順序: 帰還して初めて次スロットへ。
                # 物理的連続性: スロット m を出て A に戻る時刻 ≤ スロット m+1 で A を発つ時刻。
                #   A 帰着 = d[m] + dout(m)、  A 発 = a[m+1] - din(m+1)
                if mi < M - 1:
                    m.Add(self.atused[p, mi + 1] <= self.leaves[p, mi])
                    m.Add(
                        self.a[p, mi + 1] - self.slot_din[p, mi + 1]
                        >= self.d[p, mi] + self.slot_dout[p, mi]
                    ).OnlyEnforceIf(self.atused[p, mi + 1])

            # 同一乗客は同一トリップで入域と出域を兼ねられない（自己交代の禁止）
            for k in sites:
                if k not in allowed:
                    continue
                for j in range(J):
                    m.Add(
                        sum(self.inB[p, mi, k, j] for mi in range(M))
                        + sum(self.outB[p, mi, k, j] for mi in range(M))
                        <= 1
                    )

        # ---- 容量制約（トリップ単位） ----
        for k in sites:
            if any(k in inst.allowed_sites_of(p) for p in pax):
                for j in range(J):
                    incount = sum(
                        self.inB[p, mi, k, j] for p in pax
                        if k in inst.allowed_sites_of(p) for mi in range(M)
                    )
                    outcount = sum(
                        self.outB[p, mi, k, j] for p in pax
                        if k in inst.allowed_sites_of(p) for mi in range(M)
                    )
                    m.Add(incount <= self.cap_trip[k, j])
                    m.Add(outcount <= self.cap_trip[k, j])

        # ---- 常駐 / カテゴリ / 交代（イベント評価） ----
        cats = sorted({p.category for p in inst.passengers})
        for k in sites:
            site = inst.staffed_sites[k]
            elig = [p for p in pax if k in inst.allowed_sites_of(p)]
            # 初期占有
            init_occ = sum(
                1 for p in elig
                if (inst.initial_of(p) and inst.initial_of(p).location == k)
            )
            init_cat = {
                c: sum(
                    1 for p in elig
                    if inst.category_of(p) == c
                    and inst.initial_of(p) and inst.initial_of(p).location == k
                )
                for c in cats
            }
            if init_occ < site.occupancy_min:
                raise ValueError(f"{k}: 初期占有 {init_occ} < occupancy_min {site.occupancy_min}")
            for c, req in site.category_requirements.items():
                if init_cat.get(c, 0) < req:
                    raise ValueError(f"{k}: 初期 {c} {init_cat.get(c,0)} < 要求 {req}")

            occ_prev = init_occ
            cat_prev = dict(init_cat)
            for j in range(J):
                inc = sum(self.inB[p, mi, k, j] for p in elig for mi in range(M))
                outc = sum(self.outB[p, mi, k, j] for p in elig for mi in range(M))
                occ = m.NewIntVar(0, len(elig) + init_occ, f"occ_{k}_{j}")
                m.Add(occ == occ_prev + inc - outc)
                m.Add(occ >= site.occupancy_min)
                occ_prev = occ
                # カテゴリ別
                new_cat = {}
                for c in cats:
                    incc = sum(
                        self.inB[p, mi, k, j] for p in elig
                        if inst.category_of(p) == c for mi in range(M)
                    )
                    outcc = sum(
                        self.outB[p, mi, k, j] for p in elig
                        if inst.category_of(p) == c for mi in range(M)
                    )
                    cc = m.NewIntVar(0, len(elig) + init_occ, f"catcnt_{k}_{c}_{j}")
                    m.Add(cc == cat_prev[c] + incc - outcc)
                    req = site.category_requirements.get(c, 0)
                    if req:
                        m.Add(cc >= req)
                    new_cat[c] = cc
                cat_prev = new_cat

        # ---- 同乗 together（入域便） ----
        for k in sites:
            site = inst.staffed_sites[k]
            elig = [p for p in pax if k in inst.allowed_sites_of(p)]
            for group in site.ride_together:
                for j in range(J):
                    has = {}
                    for c in group:
                        cnt = sum(
                            self.inB[p, mi, k, j] for p in elig
                            if inst.category_of(p) == c for mi in range(M)
                        )
                        hc = m.NewBoolVar(f"has_{k}_{j}_{c}")
                        m.Add(cnt >= 1).OnlyEnforceIf(hc)
                        m.Add(cnt == 0).OnlyEnforceIf(hc.Not())
                        has[c] = hc
                    gl = list(group)
                    for ci in range(len(gl) - 1):
                        m.Add(has[gl[ci]] == has[gl[ci + 1]])

        # ---- 目的関数 ----
        terms = []
        for k in sites:
            din = inst.staffed_sites[k].segments.inbound_hours
            dout = inst.staffed_sites[k].segments.outbound_hours
            trip_h = din + dout
            for j in range(J):
                for v in vehicles:
                    terms.append(self.assignV[k, j, v.id] * trip_h * v.hourly_cost)
        m.Minimize(sum(terms))

    # ------------------------------------------------------------------
    def solve(self) -> "BArmSolution":
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.inst.solver.max_seconds
        solver.parameters.num_search_workers = 8
        status = solver.Solve(self.m)
        return BArmSolution(self, solver, status)


class BArmSolution:
    def __init__(self, model: BArmModel, solver: cp_model.CpSolver, status):
        self.model = model
        self.solver = solver
        self.status = status

    @property
    def ok(self) -> bool:
        return self.status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    def summary(self) -> str:
        s, mdl, inst = self.solver, self.model, self.model.inst
        name = s.StatusName(self.status)
        lines = [f"status: {name}"]
        if not self.ok:
            return "\n".join(lines)
        lines.append(f"objective (total vehicle cost): {s.ObjectiveValue():.0f}")
        lines.append("--- trips ---")
        for k in mdl.sites:
            din = inst.staffed_sites[k].segments.inbound_hours
            for j in range(mdl.J):
                if s.Value(mdl.used[k, j]):
                    dep = s.Value(mdl.dep[k, j])
                    veh = next(v.id for v in mdl.vehicles
                               if s.Value(mdl.assignV[k, j, v.id]))
                    ins = [p for p in (pp.id for pp in inst.passengers)
                           for mi in range(mdl.M)
                           if (p, mi, k, j) in mdl.inB and s.Value(mdl.inB[p, mi, k, j])]
                    outs = [p for p in (pp.id for pp in inst.passengers)
                            for mi in range(mdl.M)
                            if (p, mi, k, j) in mdl.outB and s.Value(mdl.outB[p, mi, k, j])]
                    lines.append(
                        f"  {k} trip{j}: depart A@{dep}h, arrive {k}@{dep+din}h, "
                        f"veh={veh}, in={ins}, out={outs}"
                    )
        lines.append("--- passenger visits ---")
        for p in (pp.id for pp in inst.passengers):
            for mi in range(mdl.M):
                if s.Value(mdl.atused[p, mi]):
                    k = next(kk for kk in mdl.sites if s.Value(mdl.at[p, mi, kk]))
                    a = s.Value(mdl.a[p, mi])
                    left = s.Value(mdl.leaves[p, mi])
                    d = s.Value(mdl.d[p, mi]) if left else None
                    when = f"depart@{d}h" if left else "stays to end"
                    lines.append(f"  {p} visit{mi}: {k} arrive@{a}h, {when}")
        return "\n".join(lines)
