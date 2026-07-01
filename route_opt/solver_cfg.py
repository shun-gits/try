"""Solver configuration loader and theoretical parameter calculator.

configs/solver_config.yaml を読み込み、null 項目はインスタンスから
理論上限値を自動計算して SolverParams を組み立てる。
"""

from __future__ import annotations

import math
import pathlib

import yaml

from .schema import Instance, SolverParams

_CONFIG_PATH = pathlib.Path("configs/solver_config.yaml")


def load_solver_config() -> dict:
    """configs/solver_config.yaml を読み込む。ファイルがなければ空 dict を返す。"""
    if _CONFIG_PATH.exists():
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {}


def theoretical_params(inst: Instance) -> dict[str, int]:
    """インスタンスから M / J / JCD の理論上限値を計算して返す。

    各値の意味:
      M (max_visits_per_passenger): 1乗客が計画期間に行える最大訪問回数の上界。
          最短の往復サイクル（最も短い滞在・移動を持つサイト）で計画期間を割った値。
      J (trips_per_site): 各 B 島サイトへのトリップ数上界。
          計画期間をサイト最短滞在時間で割った値（全サイト中の最大）。
      JCD (trips_cd): CD-arm（A→C→D→C→A）トリップ数の上界。
          計画期間を CD 往復所要時間で割った値。
    """
    H = inst.planning_horizon.hours

    # --- M: max_visits_per_passenger ---
    # B-arm 最短サイクル = inbound + min_stay + outbound
    min_b_cycle: int | None = None
    for site in inst.staffed_sites.values():
        cycle = (site.segments.inbound_hours
                 + site.stay.min_hours
                 + site.segments.outbound_hours)
        if cycle > 0 and (min_b_cycle is None or cycle < min_b_cycle):
            min_b_cycle = cycle

    # CD-arm 最短サイクル = round_hours + min_d_stay
    min_d_cycle: int | None = None
    if inst.cd_arm is not None and inst.temporary_site is not None:
        d_stays: list[int] = []
        tbl = inst.temporary_site.d_stay_table
        if tbl and all(isinstance(v, dict) for v in tbl.values()):
            d_stays = [int(h) for sub in tbl.values()  # type: ignore[union-attr]
                       for h in sub.values()]
        elif tbl:
            d_stays = [int(v) for v in tbl.values()]  # type: ignore[union-attr]
        min_d_stay = min(d_stays) if d_stays else 0
        cd_cycle = inst.cd_arm.round_hours + min_d_stay
        if cd_cycle > 0:
            min_d_cycle = cd_cycle

    candidates = [c for c in [min_b_cycle, min_d_cycle] if c is not None]
    min_cycle = min(candidates) if candidates else 24
    M = max(1, math.ceil(H / min_cycle))

    # --- J: trips_per_site ---
    J = 1
    for site in inst.staffed_sites.values():
        min_stay = site.stay.min_hours
        j_site = math.ceil(H / min_stay) if min_stay > 0 else H
        J = max(J, j_site)

    # --- JCD: trips_cd ---
    JCD = 1
    if inst.cd_arm is not None:
        round_h = inst.cd_arm.round_hours
        JCD = max(1, math.ceil(H / round_h)) if round_h > 0 else H

    return {"max_visits_per_passenger": M, "trips_per_site": J, "trips_cd": JCD}


def solver_params_for(inst: Instance) -> SolverParams:
    """configs/solver_config.yaml と理論値を統合して SolverParams を返す。

    YAML で null の項目は理論上限値を自動採用する。
    max_seconds / commit_hours は YAML の値のみ使用（自動計算なし）。
    """
    cfg = load_solver_config()
    theory = theoretical_params(inst)

    return SolverParams(
        max_visits_per_passenger=(
            int(cfg["max_visits_per_passenger"])
            if cfg.get("max_visits_per_passenger") is not None
            else theory["max_visits_per_passenger"]
        ),
        trips_per_site=(
            int(cfg["trips_per_site"])
            if cfg.get("trips_per_site") is not None
            else theory["trips_per_site"]
        ),
        trips_cd=(
            int(cfg["trips_cd"])
            if cfg.get("trips_cd") is not None
            else theory["trips_cd"]
        ),
        max_seconds=float(cfg.get("max_seconds", 30.0)),
        relative_gap=float(cfg.get("relative_gap", 0.0) or 0.0),
        commit_hours=(int(cfg["commit_hours"]) if cfg.get("commit_hours") is not None else None),
    )
