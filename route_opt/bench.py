"""検証用インスタンス生成。

実規模に近い feasible インスタンスを生成する（GUI のサンプル生成・テストで使用）。
構造を素直に保つため、島ごとに別カテゴリ（B{i}=Cat{i}）・together なし・各乗客は自島のみ適格、
とする（allowedB 絞り込みのミティゲーションが効いた状態）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .schema import (
    CDArm,
    Calendar,
    Fleet,
    Instance,
    OwnedVehicle,
    Passenger,
    PassengerRule,
    PlanningHorizon,
    Segments,
    SolverParams,
    StaffedSite,
    Stay,
    TemporarySite,
    VehicleType,
    InitialPassengerState,
)

START = datetime(2026, 1, 1)


def make_instance(*, days: int, islands: int, workers_per_island: int,
                  vans: int, trucks: int, M: int, J: int, JCD: int,
                  max_seconds: float) -> Instance:
    end = START + timedelta(days=days)
    vehicle_types = {
        "minivan": VehicleType(capacity=4, cost_per_hour=100),
        "truck": VehicleType(capacity=10, cost_per_hour=180),
    }
    owned = [OwnedVehicle(id=f"VAN{i+1}", type="minivan") for i in range(vans)]
    owned += [OwnedVehicle(id=f"TRK{i+1}", type="truck") for i in range(trucks)]

    staffed = {}
    passengers: list[Passenger] = []
    rules: dict[str, PassengerRule] = {}
    init: list[InitialPassengerState] = []
    for i in range(1, islands + 1):
        cat = f"Category{i}"
        site = f"B{i}"
        staffed[site] = StaffedSite(
            occupancy_min=1,
            category_requirements={cat: 1},
            stay=Stay(min_hours=24, max_hours=48),
            replacement_required=True,
            ride_together=[],
            segments=Segments(inbound_hours=2, outbound_hours=2),
        )
        for w in range(workers_per_island):
            pid = f"P{i}_{w}"
            passengers.append(Passenger(id=pid, category=cat))
            rules[pid] = PassengerRule(allowed_sites=[site])
            if w == 0:
                init.append(InitialPassengerState(passenger_id=pid, location=site,
                                                  arrived_at=START))
            else:
                init.append(InitialPassengerState(passenger_id=pid, location="A"))

    return Instance(
        planning_horizon=PlanningHorizon(start=START, end=end),
        calendar=Calendar(holidays=[]),
        vehicle_types=vehicle_types,
        fleet=Fleet(owned=owned),
        staffed_sites=staffed,
        cd_arm=CDArm(a_c_hours=3, c_d_hours=1, d_c_hours=1, c_a_hours=3),
        temporary_site=TemporarySite(d_stay_table={1: 12, 2: 18, 3: 24, 4: 30},
                                     occupancy_max=None),
        passengers=passengers,
        passenger_rules=rules,
        initial_state=init,
        solver=SolverParams(max_visits_per_passenger=M, trips_per_site=J,
                            trips_cd=JCD, max_seconds=max_seconds),
    )


