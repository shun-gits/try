"""YAML → Instance ローダ＋派生値（休日時間帯など）の計算。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .schema import Instance


def load_instance(path: str | Path) -> Instance:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Instance.model_validate(data)


def date_hour_range(d: str | datetime, start: datetime) -> tuple[int, int]:
    """日付 1 日分を [start_hour, end_hour) の時間帯（horizon start からの相対時）に変換。"""
    day = datetime.fromisoformat(d) if isinstance(d, str) else d
    hs = int((day - start).total_seconds() // 3600)
    he = int((day + timedelta(days=1) - start).total_seconds() // 3600)
    return hs, he


def holiday_hour_intervals(inst: Instance) -> list[tuple[int, int]]:
    """休日を [start_hour, end_hour) の時間帯（horizon start からの相対時）に変換。"""
    start = inst.planning_horizon.start
    return [date_hour_range(d, start) for d in inst.calendar.holidays]


def entity_holiday_hours(dates: list[str], start: datetime) -> set[int]:
    """車両・サイト等、個別エンティティの非稼働待機日（YYYY-MM-DD の並び）を
    horizon start からの相対時オフセットの集合に変換する（各日 24h 分）。"""
    hours: set[int] = set()
    for d in dates:
        hs, he = date_hour_range(d, start)
        hours.update(range(max(hs, 0), he))
    return hours


def hour_offset(inst: Instance, when: datetime) -> int:
    """horizon start を 0 とした相対時刻（時間）。"""
    return int((when - inst.planning_horizon.start).total_seconds() // 3600)
