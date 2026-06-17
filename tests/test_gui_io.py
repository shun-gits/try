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
    vt = {"minivan": {"capacity": 4, "cost_per_hour": 100, "rental_cost_per_hour": 150}}
    rows = gui_io.vehicle_types_to_rows(vt)
    assert gui_io.rows_to_vehicle_types(rows) == vt


def test_intmap_roundtrip_str_and_int_keys():
    catreq = {"Category1": 1, "Category2": 2}
    rows = gui_io.intmap_to_rows(catreq, "category", "min")
    assert gui_io.rows_to_intmap(rows, "category", "min") == catreq

    dtable = {1: 12, 2: 18, 3: 24}
    rows = gui_io.intmap_to_rows(dtable, "n", "hours")
    assert gui_io.rows_to_intmap(rows, "n", "hours", int_key=True) == dtable


def test_ride_together_roundtrip():
    groups = [["Category1", "Category2"], ["Category3", "Category4"]]
    s = gui_io.ride_together_to_str(groups)
    assert gui_io.str_to_ride_together(s) == groups
    assert gui_io.str_to_ride_together("") == []


def test_passenger_rules_roundtrip():
    rules = {"P001": {"allowed_sites": ["B1"]}, "P002": {"allowed_sites": ["B1", "B2"]}}
    rows = gui_io.passenger_rules_to_rows(rules)
    assert gui_io.rows_to_passenger_rules(rows) == rules
