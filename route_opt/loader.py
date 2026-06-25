"""YAML → Instance ローダ＋派生値（休日時間帯など）の計算。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .schema import Instance


def load_instance(path: str | Path) -> Instance:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Instance.model_validate(data)


def holiday_hour_intervals(inst: Instance) -> list[tuple[int, int]]:
    """休日を [start_hour, end_hour) の時間帯（horizon start からの相対時）に変換。"""
    start = inst.planning_horizon.start
    intervals: list[tuple[int, int]] = []
    for d in inst.calendar.holidays:
        day = datetime.fromisoformat(d) if isinstance(d, str) else d
        hs = int((day - start).total_seconds() // 3600)
        he = int((day + timedelta(days=1) - start).total_seconds() // 3600)
        intervals.append((hs, he))
    return intervals


def hour_offset(inst: Instance, when: datetime) -> int:
    """horizon start を 0 とした相対時刻（時間）。"""
    return int((when - inst.planning_horizon.start).total_seconds() // 3600)
