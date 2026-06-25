"""gui_io の純関数 round-trip テスト。"""

from __future__ import annotations

from route_opt import gui_io
from route_opt.loader import load_instance


def test_doc_instance_roundtrip():
    inst = load_instance("instances/full_cd.yaml")
    doc = gui_io.doc_from_instance(inst)
    inst2 = gui_io.instance_from_doc(doc)
    # 再 dump して一致
    assert gui_io.doc_from_instance(inst2) == doc


def test_yaml_roundtrip():
    inst = load_instance("instances/full_cd.yaml")
    doc = gui_io.doc_from_instance(inst)
    text = gui_io.yaml_from_doc(doc)
    assert gui_io.doc_from_yaml(text) == doc


def test_vehicle_types_roundtrip():
    vt = {"minivan": {"capacity": 4, "cost_per_hour": 100}}
    rows = gui_io.vehicle_types_to_rows(vt)
    assert gui_io.rows_to_vehicle_types(rows) == vt


def test_intmap_roundtrip_str_and_int_keys():
    catreq = {"Category1": 1, "Category2": 2}
    rows = gui_io.intmap_to_rows(catreq, "category", "min")
    assert gui_io.rows_to_intmap(rows, "category", "min") == catreq

    dtable = {1: 12, 2: 18, 3: 24}
    rows = gui_io.intmap_to_rows(dtable, "n", "hours")
    assert gui_io.rows_to_intmap(rows, "n", "hours", int_key=True) == dtable


def test_d_stay_table_flat_roundtrip():
    flat = {1: 12, 2: 18, 3: 24}
    rows = gui_io.d_stay_table_to_rows(flat)
    assert all(r["weight"] == "*" for r in rows)
    assert gui_io.rows_to_d_stay_table(rows) == flat
    assert sorted(gui_io.d_stay_hours(flat)) == [12, 18, 24]


def test_d_stay_table_per_weight_roundtrip():
    per = {"small": {1: 12, 2: 18}, "large": {1: 16, 2: 24}}
    rows = gui_io.d_stay_table_to_rows(per)
    assert gui_io.rows_to_d_stay_table(rows) == per
    assert sorted(gui_io.d_stay_hours(per)) == [12, 16, 18, 24]


def test_ride_together_roundtrip():
    groups = [["Category1", "Category2"], ["Category3", "Category4"]]
    s = gui_io.ride_together_to_str(groups)
    assert gui_io.str_to_ride_together(s) == groups
    assert gui_io.str_to_ride_together("") == []


def test_passenger_rules_roundtrip():
    rules = {"P001": {"allowed_sites": ["B1"]}, "P002": {"allowed_sites": ["B1", "B2"]}}
    rows = gui_io.passenger_rules_to_rows(rules)
    assert gui_io.rows_to_passenger_rules(rows) == rules


def test_ensure_masters_seeds_from_existing_data():
    doc = {
        "passengers": [
            {"id": "P001", "category": "Cat1", "weight": "small"},
            {"id": "P002", "category": "Cat2", "weight": "large"},
        ],
        "staffed_sites": {
            "B1": {"category_requirements": {"Cat1": 1, "Cat3": 1},
                   "ride_together": [["Cat2", "Cat4"]]},
        },
        "temporary_site": {"d_stay_table": {"small": {1: 12}, "heavy": {1: 16}}},
    }
    m = gui_io.ensure_masters(doc)
    assert m["categories"] == ["Cat1", "Cat2", "Cat3", "Cat4"]
    # small/large は既定、d_stay_table の per-weight キー heavy も含む
    assert m["weights"] == ["small", "large", "heavy"]
    # doc に書き戻されている（single source of truth として永続化される）
    assert doc["masters"] == m


def test_ensure_masters_keeps_explicit_definition():
    doc = {"masters": {"categories": ["A", "B"], "weights": ["small"]},
           "passengers": [{"id": "P1", "category": "A", "weight": "small"}]}
    m = gui_io.ensure_masters(doc)
    assert m == {"categories": ["A", "B"], "weights": ["small"]}


def test_category_options_unions_master_and_used():
    doc = {"masters": {"categories": ["A"], "weights": ["small"]},
           "passengers": [{"id": "P1", "category": "Z", "weight": "small"}]}
    # マスタ外の既存値 Z もドロップダウンが空欄化しないよう候補に含む
    assert gui_io.category_options(doc) == ["A", "Z"]


def test_weight_options_keeps_small_large_first():
    doc = {"masters": {"weights": ["large", "small"]}, "passengers": []}
    opts = gui_io.weight_options(doc)
    assert opts[:2] == ["large", "small"]


def test_merge_initial_state_preserves_handoff_fields():
    # GUI で編集しない handoff 用フィールドは保持される。
    prev = {
        "passenger_id": "P001",
        "location": "A",
        "arrived_at": "2026-01-01T00:00:00",
        "earliest_departure": "2026-01-02T12:00:00",
        "last_duty": "D",
    }
    rec = gui_io.merge_initial_state(prev, "P001", "B1", "2026-01-03T00:00:00")
    assert rec == {
        "passenger_id": "P001",
        "location": "B1",
        "arrived_at": "2026-01-03T00:00:00",
        "earliest_departure": "2026-01-02T12:00:00",
        "last_duty": "D",
    }


def test_merge_initial_state_clears_arrived_at_when_blank():
    # arrived_at を空にしたら省略され、handoff フィールドは残る。
    prev = {"passenger_id": "P003", "location": "B1",
            "arrived_at": "2026-01-01T00:00:00", "last_duty": "B"}
    rec = gui_io.merge_initial_state(prev, "P003", "A", "")
    assert rec == {"passenger_id": "P003", "location": "A", "last_duty": "B"}


def test_merge_initial_state_new_passenger_has_no_extra_keys():
    rec = gui_io.merge_initial_state({}, "P009", "A", None)
    assert rec == {"passenger_id": "P009", "location": "A"}
