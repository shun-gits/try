"""Parameter-file schema for the B-arm vertical slice.

spec.md の二層方針に対応するインスタンス側スキーマ。今回は B-arm（A→B→A の交代輸送）
に必要な要素のみを定義する。CD-arm（C/D, Backhaul）は次段階で追加する。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class PlanningHorizon(BaseModel):
    start: datetime
    end: datetime

    @property
    def hours(self) -> int:
        """計画期間の総時間数 H（1h 粒度, 切り捨て）。"""
        delta = self.end - self.start
        return int(delta.total_seconds() // 3600)


class Calendar(BaseModel):
    # 休日（その日の 00:00..翌 00:00 が全運休）。
    holidays: list[str] = Field(default_factory=list)


class Segments(BaseModel):
    inbound_hours: int
    outbound_hours: int


class Stay(BaseModel):
    min_hours: int
    max_hours: int


class StaffedSite(BaseModel):
    occupancy_min: int = 0
    category_requirements: dict[str, int] = Field(default_factory=dict)
    stay: Stay
    replacement_required: bool = True
    # together グループのリスト。各グループは「全員乗車 or 全員不在」。
    ride_together: list[list[str]] = Field(default_factory=list)
    segments: Segments


class CDArm(BaseModel):
    """A→C→D→C→A の各区間所要（spec §6）。"""
    a_c_hours: int
    c_d_hours: int
    d_c_hours: int
    c_a_hours: int

    @property
    def to_d_hours(self) -> int:      # A→(C)→D
        return self.a_c_hours + self.c_d_hours

    @property
    def from_d_hours(self) -> int:    # D→(C)→A
        return self.d_c_hours + self.c_a_hours

    @property
    def round_hours(self) -> int:
        return self.to_d_hours + self.from_d_hours

    @property
    def drive_hours(self) -> int:
        """車両が実際に占有する運転区間 A→C + C→A の合計（C↔D は徒歩のため除外）。"""
        return self.a_c_hours + self.c_a_hours

    @property
    def d_depart_offset(self) -> int:
        """CD トリップ出発 dep から、帰還者が D を発つ時刻までのオフセット。
        車両は dep+a_c に C へ着き即 C→A へ折り返す（C での同時積替）。帰還者は
        その便に C で乗るため d_c 前に D を発つ ⇒ d = dep + a_c - d_c。"""
        return self.a_c_hours - self.d_c_hours


class TemporarySite(BaseModel):
    """D（一時サイト, spec §16）。"""
    # 同乗人数 n -> 必要滞在h（min のみ, 上限なし）。n は 1..最大定員を列挙。
    d_stay_table: dict[int, int]
    occupancy_max: int | None = None


class VehicleType(BaseModel):
    capacity: int
    cost_per_hour: int
    rental_cost_per_hour: int


class OwnedVehicle(BaseModel):
    id: str
    type: str
    initial_location: str = "A"


class Rental(BaseModel):
    enabled: bool = True
    initial_location: str = "A"
    # 各車種ごとに確保しうるレンタル台数の上限。
    max_per_type: int = 0


class Fleet(BaseModel):
    owned: list[OwnedVehicle] = Field(default_factory=list)
    rental: Rental = Field(default_factory=Rental)


class Passenger(BaseModel):
    id: str
    category: str


class PassengerRule(BaseModel):
    allowed_sites: list[str] = Field(default_factory=list)


class InitialPassengerState(BaseModel):
    passenger_id: str
    location: str
    arrived_at: datetime | None = None
    # ローリングホライズン handoff 用: この時刻まで当該サイトを出られない（D の残り必要滞在等）。
    earliest_departure: datetime | None = None
    # handoff 用: A 待機者の「直前に完了した勤務種別」("B"/"D")。次の勤務はこれと交互でなければならない。
    last_duty: str | None = None


class SolverParams(BaseModel):
    # 訪問スロット上限 M、サイト別トリップ上限 J、レンタル上限などの規模パラメータ。
    max_visits_per_passenger: int = 4
    trips_per_site: int = 8
    trips_cd: int = 8
    max_seconds: float = 30.0
    # ローリングホライズン用: トリップを [0, commit_hours] のみに制限（lookahead と分離）。
    # None なら planning horizon 全体（=単一ウィンドウ）。
    commit_hours: int | None = None


class Instance(BaseModel):
    planning_horizon: PlanningHorizon
    time_granularity_hours: int = 1
    calendar: Calendar = Field(default_factory=Calendar)
    vehicle_types: dict[str, VehicleType]
    fleet: Fleet
    staffed_sites: dict[str, StaffedSite]
    cd_arm: CDArm | None = None
    temporary_site: TemporarySite | None = None
    passengers: list[Passenger]
    passenger_rules: dict[str, PassengerRule] = Field(default_factory=dict)
    initial_state: list[InitialPassengerState] = Field(default_factory=list)
    solver: SolverParams = Field(default_factory=SolverParams)

    @model_validator(mode="after")
    def _check(self) -> "Instance":
        if self.time_granularity_hours != 1:
            raise ValueError("time_granularity_hours は現状 1h のみ対応")
        pids = {p.id for p in self.passengers}
        for st in self.initial_state:
            if st.passenger_id not in pids:
                raise ValueError(f"initial_state の未知の乗客: {st.passenger_id}")
        for vt in (v.type for v in self.fleet.owned):
            if vt not in self.vehicle_types:
                raise ValueError(f"未知の車種: {vt}")
        for pid, rule in self.passenger_rules.items():
            if pid not in pids:
                raise ValueError(f"passenger_rules の未知の乗客: {pid}")
            for s in rule.allowed_sites:
                if s not in self.staffed_sites:
                    raise ValueError(f"allowed_sites の未知のサイト: {s}")
        return self

    # ---- 便利アクセサ ----
    def category_of(self, pid: str) -> str:
        for p in self.passengers:
            if p.id == pid:
                return p.category
        raise KeyError(pid)

    def allowed_sites_of(self, pid: str) -> list[str]:
        rule = self.passenger_rules.get(pid)
        return list(rule.allowed_sites) if rule else []

    def initial_of(self, pid: str) -> InitialPassengerState | None:
        for st in self.initial_state:
            if st.passenger_id == pid:
                return st
        return None
