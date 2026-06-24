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


# ---- vehicle_types  (name -> {capacity, cost_per_hour}) ----
def vehicle_types_to_rows(vt: dict[str, dict]) -> list[dict]:
    return [
        {"name": name, "capacity": v["capacity"], "cost_per_hour": v["cost_per_hour"]}
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


# ---- d_stay_table (flat {n->h} または per-weight {weight->{n->h}}) ----
def _is_per_weight(table: dict) -> bool:
    return bool(table) and all(isinstance(v, dict) for v in table.values())


def d_stay_hours(table: dict) -> list[int]:
    """flat / per-weight どちらの d_stay_table からも滞在h値を平坦に列挙（図表示用）。"""
    if not table:
        return []
    if _is_per_weight(table):
        return [int(h) for sub in table.values() for h in sub.values()]
    return [int(v) for v in table.values()]


def d_stay_table_to_rows(table: dict) -> list[dict]:
    """d_stay_table -> (weight, n, hours) 行。flat は weight='*'（全カテゴリ共通）。"""
    rows: list[dict] = []
    if not table:
        return rows
    if _is_per_weight(table):
        for w, sub in table.items():
            for n, h in sub.items():
                rows.append({"weight": str(w), "n": int(n), "hours": int(h)})
    else:
        for n, h in table.items():
            rows.append({"weight": "*", "n": int(n), "hours": int(h)})
    return rows


def rows_to_d_stay_table(rows: list[dict]) -> dict:
    """(weight, n, hours) 行 -> d_stay_table。weight が全て '*'/空なら flat、
    体重カテゴリ指定があれば per-weight（その場合 '*' 行は無視）。"""
    flat: dict[int, int] = {}
    per: dict[str, dict[int, int]] = {}
    for r in rows:
        n_raw, h_raw = r.get("n"), r.get("hours")
        if n_raw is None or h_raw is None:
            continue
        if str(n_raw).strip() == "" or str(h_raw).strip() == "":
            continue
        try:
            n, h = int(n_raw), int(h_raw)
        except (TypeError, ValueError):
            continue
        w = str(r.get("weight", "")).strip()
        if w in ("", "*"):
            flat[n] = h
        else:
            per.setdefault(w, {})[n] = h
    return per if per else flat


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
