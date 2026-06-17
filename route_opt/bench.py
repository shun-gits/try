"""スケール検証用ベンチ。

実規模に近い feasible インスタンスを生成し、モデル規模・ビルド時間・求解時間を測る。
構造を素直に保つため、島ごとに別カテゴリ（B{i}=Cat{i}）・together なし・各乗客は自島のみ適格、
とする（allowedB 絞り込みのミティゲーションが効いた状態）。

  python -m route_opt.bench
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from ortools.sat.python import cp_model

from .model import FullModel
from .schema import (
    CDArm,
    Calendar,
    Fleet,
    Instance,
    OwnedVehicle,
    Passenger,
    PassengerRule,
    PlanningHorizon,
    Rental,
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
        "minivan": VehicleType(capacity=4, cost_per_hour=100, rental_cost_per_hour=150),
        "truck": VehicleType(capacity=10, cost_per_hour=180, rental_cost_per_hour=250),
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
        fleet=Fleet(owned=owned, rental=Rental(enabled=True, max_per_type=2)),
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


def run_case(label: str, **kw) -> None:
    inst = make_instance(**kw)
    npax = len(inst.passengers)
    t0 = time.perf_counter()
    mdl = FullModel(inst)
    build = time.perf_counter() - t0
    proto = mdl.m.Proto()
    nvars = len(proto.variables)
    ncons = len(proto.constraints)
    sol = mdl.solve()
    name = sol.solver.StatusName(sol.status)
    obj = f"{sol.solver.ObjectiveValue():.0f}" if sol.ok else "-"
    print(f"{label:<26} pax={npax:<3} H={inst.planning_horizon.hours:<4} "
          f"vars={nvars:<7} cons={ncons:<7} build={build:5.1f}s "
          f"solve={sol.solver.WallTime():6.1f}s {name:<11} obj={obj}")


def main() -> None:
    print("=== scale sweep (per-solve time limit applies) ===")
    # 小 → 中 → 実規模へ段階的に
    run_case("tiny  1isl x2 x7d",  days=7,  islands=1, workers_per_island=2,
             vans=1, trucks=0, M=4, J=8, JCD=6, max_seconds=20)
    run_case("small 1isl x4 x14d", days=14, islands=1, workers_per_island=4,
             vans=1, trucks=0, M=6, J=14, JCD=12, max_seconds=30)
    run_case("med   3isl x4 x14d", days=14, islands=3, workers_per_island=4,
             vans=2, trucks=1, M=6, J=14, JCD=16, max_seconds=45)
    run_case("big   3isl x10 x30d", days=30, islands=3, workers_per_island=10,
             vans=3, trucks=1, M=8, J=24, JCD=40, max_seconds=60)


if __name__ == "__main__":
    main()
