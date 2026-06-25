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


def expand_departures(departures: list[int], commit_h: int, round_h: int) -> list[int]:
    """1日の出発時オフセット列を [0, commit_h] 全日に展開して返す。
    各時オフセット τ を日ごとに d*24+τ と並べ、論理往復 round_h が commit 内に
    収まる便のみ採用する。空なら空（=ダイヤ未設定）。"""
    if not departures:
        return []
    times = sorted(set(departures))
    out: list[int] = []
    d = 0
    while d * 24 + min(times) + round_h <= commit_h:
        for tau in times:
            t = d * 24 + tau
            if t >= 0 and t + round_h <= commit_h:
                out.append(t)
        d += 1
    return sorted(set(out))


class TemporarySite(BaseModel):
    """D（一時サイト, spec §16）。"""
    # 必要滞在h（min のみ, 上限なし）。n は 1..最大定員を列挙。
    # - 体重を区別しない場合（従来形）: {同乗人数 n -> h}        例 {1: 12, 2: 18}
    # - 体重カテゴリ別に設定する場合 : {weight -> {n -> h}}      例
    #     {"small": {1: 12, 2: 18}, "large": {1: 16, 2: 24}}
    #   引きは「乗客の体重カテゴリ × その便の同乗総人数 n」。
    d_stay_table: dict[str, dict[int, int]] | dict[int, int]
    occupancy_max: int | None = None

    @property
    def per_weight(self) -> bool:
        """体重カテゴリ別テーブルか（値が dict なら per-weight, int なら全カテゴリ共通）。"""
        return bool(self.d_stay_table) and all(
            isinstance(v, dict) for v in self.d_stay_table.values()
        )

    def stay_table_for(self, weight: str) -> dict[int, int]:
        """指定体重カテゴリの {n -> h} 表。per-weight でなければ全カテゴリ共通表。"""
        if self.per_weight:
            tbl = self.d_stay_table
            if weight not in tbl:
                raise KeyError(f"d_stay_table に体重カテゴリ '{weight}' の定義がありません")
            return tbl[weight]  # type: ignore[return-value]
        return self.d_stay_table  # type: ignore[return-value]

    def required_hours(self, weight: str, n: int) -> int:
        """体重カテゴリ weight・同乗総人数 n のときの必要最低滞在h（未定義 n は 0）。"""
        return self.stay_table_for(weight).get(n, 0)


class VehicleType(BaseModel):
    capacity: int
    cost_per_hour: int


class OwnedVehicle(BaseModel):
    id: str
    type: str
    initial_location: str = "A"
    # 固定ダイヤ: この車両の A→C 便（往路フェリー Aout→Cwait）の「1日の出発時刻」。
    # planning_horizon.start を 0 とした時オフセット（0..23 を想定）のリスト。
    # 指定すると当該車両はこの時刻にのみ A→C を発し、ソルバは時刻を最適化せず
    # 「各便を出す/出さない（積載）」のみを選ぶ。復路 C→A は折り返しで自動決定。
    # 便ダイヤは全保有車両の和、各時刻スロットの提供定員は「その時刻に出発する
    # 保有車両」で決まる。空（既定）なら当該車両はダイヤを持たない。
    a_c_departures: list[int] = Field(default_factory=list)

    def scheduled_departures(self, commit_h: int, round_h: int) -> list[int]:
        """この車両の出発時刻を [0, commit_h] 全日に展開して返す。"""
        return expand_departures(self.a_c_departures, commit_h, round_h)


class Fleet(BaseModel):
    owned: list[OwnedVehicle] = Field(default_factory=list)

    def has_timetable(self) -> bool:
        """いずれかの保有車両に固定ダイヤが設定されているか。"""
        return any(v.a_c_departures for v in self.owned)


class Masters(BaseModel):
    """category / weight のマスタ（選択肢の単一の源）。

    GUI エディタは passengers の category / weight をここからの選択式にする。
    空（未定義）なら検証は緩く、既存インスタンスとの後方互換を保つ。非空なら
    passengers の category / weight が必ずマスタに含まれることを検証する。
    """
    categories: list[str] = Field(default_factory=list)
    weights: list[str] = Field(default_factory=list)


class Passenger(BaseModel):
    id: str
    category: str
    # 体重区分（"small"=小 / "large"=大）。per-weight な d_stay_table 利用時は
    # 「乗客の体重カテゴリ × 同乗総人数」で D 必要滞在を引くため最適化制約に影響する
    # （従来形 table[n] のときは全カテゴリ共通＝weight 非依存）。
    weight: str = "small"


class PassengerRule(BaseModel):
    allowed_sites: list[str] = Field(default_factory=list)


# 島間移動中（CD-arm の車両区間 A↔C 上）を表す location トークン。
#   "A->C": A を発ち D へ向かう途中（往路便上）。到着先＝D。
#   "C->A": D から A へ戻る途中（復路便上）。到着先＝A。
TRANSIT_LEGS: dict[str, tuple[str, str]] = {"A->C": ("A", "C"), "C->A": ("C", "A")}


class InitialPassengerState(BaseModel):
    passenger_id: str
    location: str
    arrived_at: datetime | None = None
    # ローリングホライズン handoff 用: この時刻まで当該サイトを出られない（D の残り必要滞在等）。
    earliest_departure: datetime | None = None
    # handoff 用: A 待機者の「直前に完了した勤務種別」("B"/"D")。次の勤務はこれと交互でなければならない。
    last_duty: str | None = None

    @property
    def transit_leg(self) -> tuple[str, str] | None:
        """location が島間移動中トークンなら (from, to) を返す。そうでなければ None。"""
        return TRANSIT_LEGS.get(self.location)


class SolverParams(BaseModel):
    # 訪問スロット上限 M、サイト別トリップ上限 J などの規模パラメータ。
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
    masters: Masters = Field(default_factory=Masters)
    passengers: list[Passenger]
    passenger_rules: dict[str, PassengerRule] = Field(default_factory=dict)
    initial_state: list[InitialPassengerState] = Field(default_factory=list)
    solver: SolverParams = Field(default_factory=SolverParams)

    @model_validator(mode="after")
    def _check(self) -> "Instance":
        if self.time_granularity_hours != 1:
            raise ValueError("time_granularity_hours は現状 1h のみ対応")
        # masters が定義されていれば passengers の category / weight を制約する
        # （空なら後方互換のため検証しない）。
        if self.masters.categories:
            allowed_cats = set(self.masters.categories)
            for p in self.passengers:
                if p.category not in allowed_cats:
                    raise ValueError(
                        f"passenger {p.id} の category '{p.category}' は "
                        f"masters.categories に未定義です"
                    )
        if self.masters.weights:
            allowed_w = set(self.masters.weights)
            for p in self.passengers:
                if p.weight not in allowed_w:
                    raise ValueError(
                        f"passenger {p.id} の weight '{p.weight}' は "
                        f"masters.weights に未定義です"
                    )
        pids = {p.id for p in self.passengers}
        allowed_loc = {"A", "D"} | set(self.staffed_sites) | set(TRANSIT_LEGS)
        for st in self.initial_state:
            if st.passenger_id not in pids:
                raise ValueError(f"initial_state の未知の乗客: {st.passenger_id}")
            if st.location not in allowed_loc:
                raise ValueError(
                    f"initial_state の未知の location '{st.location}'"
                    f"（許可: {sorted(allowed_loc)}）"
                )
            if st.transit_leg is not None:
                # 島間移動中（A↔C）は CD-arm が定義されていること・到着時刻が必須。
                if self.cd_arm is None:
                    raise ValueError(
                        f"location '{st.location}' は cd_arm が必要（A↔C 車両区間）"
                    )
                if st.arrived_at is None:
                    raise ValueError(
                        f"location '{st.location}' は arrived_at（到着時刻）が必須"
                    )
        for vt in (v.type for v in self.fleet.owned):
            if vt not in self.vehicle_types:
                raise ValueError(f"未知の車種: {vt}")
        for pid, rule in self.passenger_rules.items():
            if pid not in pids:
                raise ValueError(f"passenger_rules の未知の乗客: {pid}")
            for s in rule.allowed_sites:
                if s not in self.staffed_sites:
                    raise ValueError(f"allowed_sites の未知のサイト: {s}")
        if self.temporary_site is not None and self.temporary_site.per_weight:
            for w in {p.weight for p in self.passengers}:
                if w not in self.temporary_site.d_stay_table:
                    raise ValueError(
                        f"temporary_site.d_stay_table に体重カテゴリ '{w}' の定義がありません"
                    )
        return self

    # ---- 便利アクセサ ----
    def category_of(self, pid: str) -> str:
        for p in self.passengers:
            if p.id == pid:
                return p.category
        raise KeyError(pid)

    def weight_of(self, pid: str) -> str:
        for p in self.passengers:
            if p.id == pid:
                return p.weight
        raise KeyError(pid)

    def allowed_sites_of(self, pid: str) -> list[str]:
        rule = self.passenger_rules.get(pid)
        return list(rule.allowed_sites) if rule else []

    def initial_of(self, pid: str) -> InitialPassengerState | None:
        for st in self.initial_state:
            if st.passenger_id == pid:
                return st
        return None
