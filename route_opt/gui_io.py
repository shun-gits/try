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


# ---- masters (category / weight の選択肢の源) -----------------------------
DEFAULT_WEIGHTS = ["small", "large"]


def derive_categories(doc: dict[str, Any]) -> list[str]:
    """doc の既存データから category を収集（passengers / category_requirements /
    ride_together）。マスタ未定義の既存インスタンスを開いたときの自動補完に使う。"""
    cats: set[str] = set()
    for p in doc.get("passengers", []):
        c = str(p.get("category", "")).strip()
        if c:
            cats.add(c)
    for s in (doc.get("staffed_sites") or {}).values():
        for k in (s.get("category_requirements") or {}):
            if str(k).strip():
                cats.add(str(k).strip())
        for grp in s.get("ride_together", []) or []:
            for c in grp:
                if str(c).strip():
                    cats.add(str(c).strip())
    return sorted(cats)


def derive_weights(doc: dict[str, Any]) -> list[str]:
    """doc の既存データから weight を収集（small/large + passengers + d_stay_table）。"""
    ws: list[str] = list(DEFAULT_WEIGHTS)
    extra: set[str] = set()
    for p in doc.get("passengers", []):
        w = str(p.get("weight", "")).strip()
        if w:
            extra.add(w)
    table = (doc.get("temporary_site") or {}).get("d_stay_table") or {}
    if _is_per_weight(table):
        for w in table:
            if str(w).strip():
                extra.add(str(w).strip())
    return ws + sorted(extra - set(ws))


def ensure_masters(doc: dict[str, Any]) -> dict[str, list[str]]:
    """doc['masters'] を正規化して返す（無ければ既存データから自動補完）。

    masters は category / weight 選択肢の単一の源。空のまま保存されると検証が
    緩くなるため、初回アクセス時に既存利用値で seed して single source of truth を作る。
    """
    m = doc.get("masters")
    if not isinstance(m, dict):
        m = {}
    cats = [str(c).strip() for c in m.get("categories", []) if str(c).strip()]
    weights = [str(w).strip() for w in m.get("weights", []) if str(w).strip()]
    if not cats:
        cats = derive_categories(doc)
    if not weights:
        weights = derive_weights(doc)
    doc["masters"] = {"categories": cats, "weights": weights}
    return doc["masters"]


def _union_keep_order(primary: list[str], extra: list[str]) -> list[str]:
    out = list(primary)
    for x in extra:
        if x not in out:
            out.append(x)
    return out


def category_options(doc: dict[str, Any]) -> list[str]:
    """passengers の category ドロップダウン候補（マスタ ∪ 既存利用値）。

    マスタから外れた既存値があってもドロップダウンが空欄化しないよう union する。"""
    m = ensure_masters(doc)
    return _union_keep_order(m["categories"], derive_categories(doc))


def weight_options(doc: dict[str, Any]) -> list[str]:
    """passengers の weight ドロップダウン候補（マスタ ∪ small/large ∪ 既存利用値）。"""
    m = ensure_masters(doc)
    return _union_keep_order(m["weights"], derive_weights(doc))


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


# ---- temporary_site.occupancy_max (int / per-weight {weight->max}) ----
def occupancy_max_to_rows(v) -> list[dict]:
    """occupancy_max -> (weight, max) 行。int は weight='*'（全カテゴリ合算の総数上限）、
    None（無制限）は行なし。"""
    if v is None:
        return []
    if isinstance(v, dict):
        return [{"weight": str(w), "max": int(n)} for w, n in v.items()]
    return [{"weight": "*", "max": int(v)}]


def rows_to_occupancy_max(rows: list[dict]):
    """(weight, max) 行 -> occupancy_max。weight が全て '*'/空なら int（総数上限）、
    体重カテゴリ指定があれば per-weight（その場合 '*' 行は無視）。行なしは None（無制限）。"""
    flat: int | None = None
    per: dict[str, int] = {}
    for r in rows:
        raw = r.get("max")
        if raw is None or str(raw).strip() == "":
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        w = str(r.get("weight", "")).strip()
        if w in ("", "*"):
            flat = n
        else:
            per[w] = n
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


# ---- initial_state（GUI で編集しない handoff フィールドの保持） ----
# Passengers タブの initial_state エディタは location / arrived_at だけを編集し、
# 各レコードを作り直す。そのため UI を持たないスキーマ項目（earliest_departure /
# last_duty などローリング handoff 用。model.py の制約に影響する）が再構築で
# 失われてしまう。読み込んだ既存値を引き継ぎ、save/load の round-trip で
# 失わないようにする。新規 InitialPassengerState 項目もここを通せば自動で残る。
_INIT_STATE_EDITED_KEYS = ("passenger_id", "location", "arrived_at")


def merge_initial_state(prev: dict, pid: str, location: str,
                        arrived_at: str | None) -> dict:
    """UI で編集した値（pid/location/arrived_at）と、編集 UI を持たない既存
    フィールド（prev に入っている earliest_departure / last_duty 等）を統合した
    initial_state レコードを返す。arrived_at が空なら省略（＝計画開始時刻扱い）。"""
    rec: dict = {"passenger_id": pid, "location": location}
    if arrived_at:
        rec["arrived_at"] = arrived_at
    for k, v in prev.items():
        if k not in _INIT_STATE_EDITED_KEYS:
            rec[k] = v
    return rec
