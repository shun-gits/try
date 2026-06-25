"""時間展開フローモデル（route_opt/flow.py）のテスト。

連続時間の FullModel に対する代替ソルバ。固定ダイヤ前提で、匿名フロー求解＋経路分解により
個体（乗客 id）スケジュールを復元する。検証観点:
  - 固定ダイヤ必須（未指定は FlowUnsupported）。
  - 解の妥当性: 復号した個体スケジュールが B 占有・カテゴリ要件を満たし、各乗客が時間的に
    重複せず B/D 交互である。
  - weight 別 D 滞在・初期ピン・ride_together が反映される。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from route_opt.bench import make_instance
from route_opt.flow import FlowModel, FlowUnsupported
from route_opt.schema import (
    CDArm, Calendar, Fleet, Instance, OwnedVehicle, Passenger, PassengerRule,
    PlanningHorizon, Segments, StaffedSite, Stay, TemporarySite, VehicleType,
    InitialPassengerState,
)

DEP = [0, 6, 12, 18]


def _tt(days, islands, w, **kw):
    inst = make_instance(days=days, islands=islands, workers_per_island=w,
                         vans=2, trucks=0, M=6, J=60, JCD=60, max_seconds=15, **kw)
    inst.fleet = inst.fleet.model_copy(update={"owned": [
        ov.model_copy(update={"a_c_departures": DEP}) for ov in inst.fleet.owned]})
    return inst


def _decode_metrics(mdl, sol):
    """復号スケジュールの占有違反・乗客重複・交互の検証指標を返す。"""
    tl = sol.decode()
    H, commit = mdl.H, mdl.commit
    occ_viol = 0
    cat_viol = 0
    for sname, site in mdl.sites.items():
        cov = [0] * (H + 1)
        catcov = {c: [0] * (H + 1) for c in site.cat_req}
        for pid, acts in tl.items():
            if mdl.pax_comm[pid][0] != sname:
                continue
            cat = mdl.pax_comm[pid][1]
            for a in acts:
                if a["kind"] == "B":
                    for g in range(a["arrive"], min(a["depart"], H + 1)):
                        cov[g] += 1
                        if cat in catcov:
                            catcov[cat][g] += 1
        occ_viol += sum(1 for g in range(commit) if cov[g] < site.occ_min)
        for c, req in site.cat_req.items():
            cat_viol += sum(1 for g in range(commit) if catcov[c][g] < req)
    overlap = 0
    alt = True
    for acts in tl.values():
        ivs = sorted((a["arrive"], a["depart"]) if a["kind"] == "B"
                     else (a["board"], a["returnA"]) for a in acts)
        overlap += sum(1 for x, y in zip(ivs, ivs[1:]) if y[0] < x[1])
        kinds = [a["kind"] for a in acts]
        if any(x == y for x, y in zip(kinds, kinds[1:])):
            alt = False
    return tl, occ_viol, cat_viol, overlap, alt


# ---------------------------------------------------------------------------
def test_requires_timetable():
    inst = make_instance(days=7, islands=1, workers_per_island=3, vans=1, trucks=0,
                         M=4, J=8, JCD=8, max_seconds=10)
    # a_c_departures 未指定（自由ダイヤ）は非対応
    with pytest.raises(FlowUnsupported):
        FlowModel(inst)


def test_multi_site_passenger_rejected():
    inst = _tt(7, 2, 3)
    # ある乗客に2サイト適格を与える → 非対応
    pid = inst.passengers[0].id
    inst.passenger_rules[pid] = PassengerRule(allowed_sites=list(inst.staffed_sites))
    with pytest.raises(FlowUnsupported):
        FlowModel(inst)


def test_solves_and_covers_single_island():
    inst = _tt(14, 1, 4)
    mdl = FlowModel(inst)
    sol = mdl.solve(max_seconds=20)
    assert sol.ok, sol.summary()
    tl, occ, cat, overlap, alt = _decode_metrics(mdl, sol)
    assert set(tl) == {p.id for p in inst.passengers}     # 全乗客が結果に現れる
    assert occ == 0 and cat == 0                          # 占有・カテゴリ要件を満たす
    assert overlap == 0                                   # 個体が時間的に重複しない
    assert alt                                            # B/D 交互


def test_solves_and_covers_multi_island():
    inst = _tt(14, 2, 4)
    mdl = FlowModel(inst)
    sol = mdl.solve(max_seconds=25)
    assert sol.ok, sol.summary()
    tl, occ, cat, overlap, alt = _decode_metrics(mdl, sol)
    assert occ == 0 and cat == 0 and overlap == 0 and alt
    # ferry/D 共有でも島ごとに乗客が割り当てられる
    sites = {mdl.pax_comm[pid][0] for pid in tl}
    assert sites == set(inst.staffed_sites)


def test_decode_dstay_matches_table():
    """復号した各 D 便の滞在が temporary_site.required_hours と整合する。"""
    inst = _tt(14, 1, 4)
    mdl = FlowModel(inst)
    sol = mdl.solve(max_seconds=20)
    assert sol.ok
    tl = sol.decode()
    ts = inst.temporary_site
    for pid, acts in tl.items():
        w = mdl.pax_comm[pid][2]
        for a in acts:
            if a["kind"] == "D":
                stay = a["returnA"] - a["board"]
                # 実滞在は「必要滞在 + 復路便スナップ余裕」以上（最低でも required を満たす）
                assert stay >= ts.required_hours(w, a["load"]) > 0


def _weighted_instance():
    """weight 別 D 滞在・初期ピンを持つ最小インスタンス（固定ダイヤ）。"""
    start = datetime(2026, 1, 1)
    return Instance(
        planning_horizon=PlanningHorizon(start=start, end=start + timedelta(days=12)),
        calendar=Calendar(holidays=[]),
        vehicle_types={"minivan": VehicleType(capacity=4, cost_per_hour=100)},
        fleet=Fleet(owned=[OwnedVehicle(id="VAN1", type="minivan",
                                        a_c_departures=DEP)]),
        staffed_sites={"B1": StaffedSite(
            occupancy_min=1, category_requirements={"Cat1": 1},
            stay=Stay(min_hours=24, max_hours=48), replacement_required=True,
            ride_together=[], segments=Segments(inbound_hours=2, outbound_hours=2))},
        cd_arm=CDArm(a_c_hours=3, c_d_hours=1, d_c_hours=1, c_a_hours=3),
        temporary_site=TemporarySite(
            d_stay_table={"small": {1: 12, 2: 18, 3: 24, 4: 30},
                          "large": {1: 18, 2: 24, 3: 30, 4: 36}},
            occupancy_max=None),
        passengers=[Passenger(id="P1", category="Cat1", weight="small"),
                    Passenger(id="P2", category="Cat1", weight="small"),
                    Passenger(id="P3", category="Cat1", weight="large"),
                    Passenger(id="P4", category="Cat1", weight="large")],
        passenger_rules={p: PassengerRule(allowed_sites=["B1"])
                         for p in ("P1", "P2", "P3", "P4")},
        initial_state=[InitialPassengerState(passenger_id="P1", location="B1",
                                             arrived_at=start),
                       InitialPassengerState(passenger_id="P2", location="A"),
                       InitialPassengerState(passenger_id="P3", location="A"),
                       InitialPassengerState(passenger_id="P4", location="A")],
    )


def test_weight_dependent_dstay_and_initial_pin():
    inst = _weighted_instance()
    mdl = FlowModel(inst)
    sol = mdl.solve(max_seconds=20)
    assert sol.ok, sol.summary()
    tl, occ, cat, overlap, alt = _decode_metrics(mdl, sol)
    assert occ == 0 and cat == 0 and overlap == 0 and alt
    # 初期 B 在室の P1 は t=0 から B に居る
    p1 = tl["P1"]
    assert p1 and p1[0]["kind"] == "B" and p1[0]["arrive"] == 0
    # weight で必要 D 滞在が異なることが表に反映されている
    ts = inst.temporary_site
    assert ts.required_hours("large", 1) > ts.required_hours("small", 1)
