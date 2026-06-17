"""GUI 用の純粋ヘルパ（streamlit 非依存）。

UI 側はテーブル/文字列で編集し、モデル側は入れ子 dict（Instance スキーマ）で扱う。
その相互変換を round-trip 可能な純関数として提供し、単体テスト可能にする。

作業状態（doc）は Instance.model_dump(mode="json") の JSON-able dict として持つ。
"""

from __future__ import annotations

from typing import Any

import yaml

from .schema import Instance


# ---- doc <-> Instance / YAML ----------------------------------------------
def doc_from_instance(inst: Instance) -> dict[str, Any]:
    """Instance -> 編集用 dict（datetime は ISO 文字列、None も保持）。"""
    return inst.model_dump(mode="json")


def instance_from_doc(doc: dict[str, Any]) -> Instance:
    """編集用 dict -> Instance（検証。失敗時 pydantic ValidationError）。"""
    return Instance.model_validate(doc)


def yaml_from_doc(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def doc_from_yaml(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("YAML のトップレベルはマッピングである必要があります")
    return data


# ---- vehicle_types  (name -> {capacity, cost_per_hour, rental_cost_per_hour}) ----
def vehicle_types_to_rows(vt: dict[str, dict]) -> list[dict]:
    return [
        {"name": name, "capacity": v["capacity"],
         "cost_per_hour": v["cost_per_hour"], "rental_cost_per_hour": v["rental_cost_per_hour"]}
        for name, v in vt.items()
    ]


def rows_to_vehicle_types(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        out[name] = {
            "capacity": int(r["capacity"]),
            "cost_per_hour": int(r["cost_per_hour"]),
            "rental_cost_per_hour": int(r["rental_cost_per_hour"]),
        }
    return out


# ---- category_requirements (category -> min) ----
def intmap_to_rows(m: dict, key: str, val: str) -> list[dict]:
    return [{key: k, val: v} for k, v in m.items()]


def rows_to_intmap(rows: list[dict], key: str, val: str, int_key: bool = False) -> dict:
    out: dict = {}
    for r in rows:
        k = r.get(key)
        if k is None or str(k).strip() == "":
            continue
        k = int(k) if int_key else str(k).strip()
        out[k] = int(r[val])
    return out


# ---- ride_together (list[list[str]] <-> "Cat1,Cat2; Cat3,Cat4") ----
def ride_together_to_str(groups: list[list[str]]) -> str:
    return "; ".join(", ".join(g) for g in groups)


def str_to_ride_together(s: str) -> list[list[str]]:
    groups: list[list[str]] = []
    for grp in s.split(";"):
        cats = [c.strip() for c in grp.split(",") if c.strip()]
        if cats:
            groups.append(cats)
    return groups


# ---- passenger_rules (pid -> {allowed_sites:[...]}) <-> rows(passenger, allowed_sites_csv) ----
def passenger_rules_to_rows(rules: dict[str, dict]) -> list[dict]:
    return [
        {"passenger": pid, "allowed_sites": ", ".join(r.get("allowed_sites", []))}
        for pid, r in rules.items()
    ]


def rows_to_passenger_rules(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        pid = str(r.get("passenger", "")).strip()
        if not pid:
            continue
        sites = [s.strip() for s in str(r.get("allowed_sites", "")).split(",") if s.strip()]
        out[pid] = {"allowed_sites": sites}
    return out
