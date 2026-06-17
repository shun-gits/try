"""ローリングホライズン分解のテスト。"""

from __future__ import annotations

from route_opt.bench import make_instance
from route_opt.report import build_stays
from route_opt.rolling import solve_rolling


def test_rolling_covers_full_horizon():
    # 単一ウィンドウでは不安定な ~10日でも、分解で全期間 feasible に解ける。
    inst = make_instance(days=10, islands=1, workers_per_island=4,
                         vans=2, trucks=0, M=4, J=99, JCD=99, max_seconds=20)
    r = solve_rolling(inst, window_days=6, step_days=5, verbose=False)
    assert r.ok, r.message
    # commit の合計が全 horizon を覆う
    covered = sum(w["commit_h"] for w in r.windows)
    assert covered == inst.planning_horizon.hours
    # 各ウィンドウは最低 feasible
    for w in r.windows:
        assert w["status"] in ("OPTIMAL", "FEASIBLE")


def test_rolling_tight_pool_uses_cd_and_handoff():
    # タイトプールでは D 再循環が必要 → CD-arm が使われ、D の earliest_departure handoff も働く。
    inst = make_instance(days=12, islands=1, workers_per_island=2,
                         vans=2, trucks=0, M=6, J=99, JCD=99, max_seconds=30)
    r = solve_rolling(inst, window_days=6, step_days=5, verbose=False)
    assert r.ok, r.message
    assert sum(w["cd_trips"] for w in r.windows) >= 1


def test_rolling_rotation_alternation_across_seams():
    # ローテーション交互順がウィンドウ境界をまたいでも保たれる（last_duty handoff）。
    inst = make_instance(days=18, islands=1, workers_per_island=2,
                         vans=2, trucks=0, M=6, J=99, JCD=99, max_seconds=30)
    r = solve_rolling(inst, window_days=6, step_days=5, verbose=False)
    assert r.ok, r.message
    stays = build_stays(r, inst)
    for p, g in stays.groupby("passenger"):
        seq = ["D" if s == "D" else "B" for s in g.sort_values("start_h")["site"]]
        assert all(a != b for a, b in zip(seq, seq[1:])), (p, seq)
