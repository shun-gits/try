"""Full モデル（B-arm + CD-arm）の妥当性テスト。"""

from __future__ import annotations

from datetime import datetime

from ortools.sat.python import cp_model

from route_opt.loader import load_instance
from route_opt.model import FullModel

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
    # CD 車両費は運転区間 A→C+C→A=6h のみ（C↔D は徒歩で配車不要）。
    # B-arm 800 + CD 2本×6h×100 = 2000。
    assert sol.solver.ObjectiveValue() == 2000


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


def test_occupancy_min_infeasible_single_cat1():
    # Cat1 が P001 のみだと、最大滞在36h を超えて常駐できず（交代要員なし）実行不能。
    inst = load_instance(CD_INSTANCE)
    inst.passengers = [p for p in inst.passengers if p.id != "P003"]
    inst.initial_state = [s for s in inst.initial_state if s.passenger_id != "P003"]
    inst.passenger_rules.pop("P003", None)
    sol = _solve(inst)
    assert sol.status == cp_model.INFEASIBLE
