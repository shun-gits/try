"""B-arm スライスの妥当性テスト。"""

from __future__ import annotations

import copy

from ortools.sat.python import cp_model

from route_opt.barm_model import BArmModel
from route_opt.loader import load_instance

INSTANCE = "instances/barm_small.yaml"


def _solve(inst):
    return BArmModel(inst).solve()


def test_feasible_small():
    sol = _solve(load_instance(INSTANCE))
    assert sol.ok
    # owned minivan 1便のみ = 4h * 100
    assert sol.solver.ObjectiveValue() == 400


def test_min_stay_respected():
    sol = _solve(load_instance(INSTANCE))
    mdl = sol.model
    # P001 は初期常駐(0h)、最低24h 滞在してから帰還
    d = sol.solver.Value(mdl.d["P001", 0])
    assert d >= 24


def test_replacement_required_infeasible_without_cat1():
    # 交代要員 P003(Cat1) を除くと、P001 は max48 以内に帰れず（Cat1 常駐を割れる）実行不能。
    inst = load_instance(INSTANCE)
    inst.passengers = [p for p in inst.passengers if p.id != "P003"]
    inst.initial_state = [s for s in inst.initial_state if s.passenger_id != "P003"]
    inst.passenger_rules.pop("P003", None)
    sol = _solve(inst)
    assert sol.status == cp_model.INFEASIBLE


def test_together_infeasible_without_cat2():
    # together 相棒 P002(Cat2) を除くと、A_B1 に Cat1 を乗せられず交代不可 → 実行不能。
    inst = load_instance(INSTANCE)
    inst.passengers = [p for p in inst.passengers if p.id != "P002"]
    inst.initial_state = [s for s in inst.initial_state if s.passenger_id != "P002"]
    inst.passenger_rules.pop("P002", None)
    sol = _solve(inst)
    assert sol.status == cp_model.INFEASIBLE


def test_holiday_shifts_or_blocks():
    # 交代に使える時間帯(22-46h付近)を全部休日にすると実行不能になるはず。
    inst = load_instance(INSTANCE)
    inst.calendar.holidays = ["2026-01-02"]  # 24h..48h を運休
    sol = _solve(inst)
    # 22h発の便(22..26h)は 24h境界に重なるため不可。代替が無ければ INFEASIBLE。
    assert sol.status == cp_model.INFEASIBLE
