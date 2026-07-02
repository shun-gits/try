"""TemporarySite の体重カテゴリ別 d_stay_table のスキーマ挙動テスト。"""

from __future__ import annotations

import pytest

from route_opt.loader import load_instance
from route_opt.schema import TemporarySite


def test_flat_table_backward_compat():
    ts = TemporarySite(d_stay_table={1: 12, 2: 18, 3: 24})
    assert not ts.per_weight
    # 体重を問わず同じ表を引く。
    assert ts.stay_table_for("small") == {1: 12, 2: 18, 3: 24}
    assert ts.stay_table_for("large") == {1: 12, 2: 18, 3: 24}
    assert ts.required_hours("large", 2) == 18
    assert ts.required_hours("small", 99) == 0  # 未定義 n は 0


def test_per_weight_table():
    ts = TemporarySite(
        d_stay_table={"small": {1: 12, 2: 18}, "large": {1: 16, 2: 24}}
    )
    assert ts.per_weight
    assert ts.required_hours("small", 2) == 18
    assert ts.required_hours("large", 2) == 24
    with pytest.raises(KeyError):
        ts.stay_table_for("medium")


def test_occupancy_max_int_backward_compat():
    ts = TemporarySite(d_stay_table={1: 12}, occupancy_max=5)
    assert not ts.per_weight_occupancy
    # int 形式は全カテゴリ共通の総数上限。
    assert ts.occupancy_max_for("small") == 5
    assert ts.occupancy_max_for("large") == 5


def test_occupancy_max_per_weight():
    ts = TemporarySite(d_stay_table={1: 12}, occupancy_max={"small": 2, "large": 3})
    assert ts.per_weight_occupancy
    assert ts.occupancy_max_for("small") == 2
    assert ts.occupancy_max_for("large") == 3
    assert ts.occupancy_max_for("medium") is None  # 未指定 weight は無制限


def test_instance_rejects_unknown_occupancy_weight():
    inst = load_instance("instances/full_small.yaml")
    doc = inst.model_dump(mode="json")
    doc["temporary_site"]["occupancy_max"] = {"huge": 3}
    from route_opt.schema import Instance
    with pytest.raises(ValueError, match="huge"):
        Instance.model_validate(doc)


def test_transit_leg_property():
    from route_opt.schema import InitialPassengerState
    assert InitialPassengerState(passenger_id="P", location="A->C").transit_leg == ("A", "C")
    assert InitialPassengerState(passenger_id="P", location="C->A").transit_leg == ("C", "A")
    assert InitialPassengerState(passenger_id="P", location="D").transit_leg is None


def test_instance_rejects_unknown_location():
    inst = load_instance("instances/full_cd.yaml")
    doc = inst.model_dump(mode="json")
    doc["initial_state"][1]["location"] = "Xyz"
    from route_opt.schema import Instance
    with pytest.raises(ValueError, match="未知の location"):
        Instance.model_validate(doc)


def test_transit_requires_cd_arm():
    # cd_arm の無いインスタンスでは島間移動中トークンは不可。
    inst = load_instance("instances/full_cd.yaml")
    doc = inst.model_dump(mode="json")
    doc["cd_arm"] = None
    doc["initial_state"][1]["location"] = "A->C"
    doc["initial_state"][1]["arrived_at"] = "2026-01-01T05:00:00"
    from route_opt.schema import Instance
    with pytest.raises(ValueError, match="cd_arm が必要"):
        Instance.model_validate(doc)


def test_transit_requires_arrived_at():
    inst = load_instance("instances/full_cd.yaml")
    doc = inst.model_dump(mode="json")
    doc["initial_state"][1]["location"] = "C->A"
    doc["initial_state"][1].pop("arrived_at", None)
    from route_opt.schema import Instance
    with pytest.raises(ValueError, match="arrived_at"):
        Instance.model_validate(doc)


def test_instance_rejects_uncovered_weight():
    # full_small は per-weight（small/large）。未知体重の乗客を足すと検証で弾かれる。
    inst = load_instance("instances/full_small.yaml")
    assert inst.temporary_site.per_weight
    doc = inst.model_dump(mode="json")
    doc["passengers"].append({"id": "P999", "category": "Category1", "weight": "huge"})
    from route_opt.schema import Instance
    with pytest.raises(ValueError, match="huge"):
        Instance.model_validate(doc)
