"""Full モデル（B-arm + CD-arm）の妥当性テスト。"""

from __future__ import annotations

from datetime import datetime

from ortools.sat.python import cp_model

from route_opt.loader import load_instance
from route_opt.model import FullModel
from route_opt.schema import InitialPassengerState

CD_INSTANCE = "instances/full_cd.yaml"


def _solve(inst):
    return FullModel(inst).solve()


def _cd_trip_count(sol) -> int:
    s, mdl = sol.solver, sol.model
    return sum(int(s.Value(mdl.usedCD[j])) for j in range(mdl.JCD))


def test_cd_used_and_optimal():
    sol = _solve(load_instance(CD_INSTANCE))
    assert sol.ok
    assert _cd_trip_count(sol) >= 1          # CD-arm が実際に使われる
    # 車両費は CD-arm（A↔C）のみ。A↔Bx は徒歩で配車不要 = コスト 0。
    # CD 2本×6h×100 = 1200。
    assert sol.solver.ObjectiveValue() == 1200


def test_rotation_alternation():
    sol = _solve(load_instance(CD_INSTANCE))
    s, mdl = sol.solver, sol.model
    bsites = mdl.bsites
    for p in (pp.id for pp in mdl.inst.passengers):
        types = []
        for mi in range(mdl.M):
            if s.Value(mdl.atused[p, mi]):
                is_b = any(s.Value(mdl.at[p, mi, k]) for k in bsites)
                types.append("B" if is_b else "D")
        # 連続スロットは B/D が交互
        for i in range(len(types) - 1):
            assert types[i] != types[i + 1], f"{p}: {types}"


def test_d_min_stay_respected():
    sol = _solve(load_instance(CD_INSTANCE))
    s, mdl = sol.solver, sol.model
    tbl = mdl.inst.temporary_site.d_stay_table
    for p in (pp.id for pp in mdl.inst.passengers):
        for mi in range(mdl.M):
            if s.Value(mdl.atused[p, mi]) and s.Value(mdl.at[p, mi, "D"]):
                if s.Value(mdl.leaves[p, mi]):
                    stay = s.Value(mdl.d[p, mi]) - s.Value(mdl.a[p, mi])
                    assert stay >= min(tbl.values())


def test_short_horizon_no_cd():
    # 再循環不要の短い horizon では CD-arm は使われない（D 需要が生じない）。
    inst = load_instance(CD_INSTANCE)
    inst.planning_horizon.end = datetime(2026, 1, 3, 12, 0, 0)  # 60h
    sol = _solve(inst)
    assert sol.ok
    assert _cd_trip_count(sol) == 0


def test_together_escort_chain_infeasible():
    # together 相棒(Cat2)も B 滞在者になりローテーションが必要。逼迫プールでは連鎖して
    # 実行不能になる（spec §15 の確定挙動。意図的に過剰制約な instance）。
    sol = _solve(load_instance("instances/full_small.yaml"))
    assert sol.status == cp_model.INFEASIBLE


def _first_used_slot(s, mdl, p):
    for mi in range(mdl.M):
        if s.Value(mdl.atused[p, mi]):
            return mi
    return None


def test_initial_transit_to_d():
    # P003 は計画開始時に A→C 移動中（D へ向かう途中）。D 到着 = 5h。
    inst = load_instance(CD_INSTANCE)
    inst.initial_state = [
        InitialPassengerState(passenger_id="P001", location="B1",
                              arrived_at=datetime(2026, 1, 1, 0, 0, 0)),
        InitialPassengerState(passenger_id="P003", location="A->C",
                              arrived_at=datetime(2026, 1, 1, 5, 0, 0),
                              earliest_departure=datetime(2026, 1, 1, 17, 0, 0)),
    ]
    sol = _solve(inst)
    s, mdl = sol.solver, sol.model
    assert sol.ok
    # 先頭スロットは D 滞在、到着は指定どおり 5h、到着便(toD)は持たない。
    assert s.Value(mdl.at["P003", 0, "D"]) == 1
    assert s.Value(mdl.a["P003", 0]) == 5
    assert sum(s.Value(mdl.toD["P003", 0, j]) for j in range(mdl.JCD)) == 0
    # 離脱するなら D 必要滞在（earliest_departure=17h）を満たす。
    if s.Value(mdl.leaves["P003", 0]):
        assert s.Value(mdl.d["P003", 0]) >= 17


def test_initial_transit_from_d():
    # P003 は計画開始時に C→A 移動中（D から A へ戻る途中）。A 到着 = 8h。
    inst = load_instance(CD_INSTANCE)
    inst.initial_state = [
        InitialPassengerState(passenger_id="P001", location="B1",
                              arrived_at=datetime(2026, 1, 1, 0, 0, 0)),
        InitialPassengerState(passenger_id="P003", location="C->A",
                              arrived_at=datetime(2026, 1, 1, 8, 0, 0)),
    ]
    sol = _solve(inst)
    s, mdl = sol.solver, sol.model
    assert sol.ok
    mi = _first_used_slot(s, mdl, "P003")
    assert mi is not None
    # 次勤務は B（D の次は B、ローテーション）。
    assert any(s.Value(mdl.at["P003", mi, k]) for k in mdl.bsites)
    # A 到着(8h)前に次勤務で A を発てない: A 発 = a - sdin >= 8。
    a_dep = s.Value(mdl.a["P003", mi]) - s.Value(mdl.sdin["P003", mi])
    assert a_dep >= 8


def test_occupancy_min_infeasible_single_cat1():
    # Cat1 が P001 のみだと、最大滞在36h を超えて常駐できず（交代要員なし）実行不能。
    inst = load_instance(CD_INSTANCE)
    inst.passengers = [p for p in inst.passengers if p.id != "P003"]
    inst.initial_state = [s for s in inst.initial_state if s.passenger_id != "P003"]
    inst.passenger_rules.pop("P003", None)
    sol = _solve(inst)
    assert sol.status == cp_model.INFEASIBLE
