"""移動可視化ロジック（apps/anim.py）のテスト。

Run solver の解（Flow decode）から作る「乗客別の時刻区間」と、
時刻 t のスナップショット（拠点在籍・移動中人数）が、解の占有を保ち矛盾なく
復元できることを検証する。
"""
from __future__ import annotations

import pytest

from apps import anim
from route_opt.bench import make_instance
from route_opt.flow import FlowModel

DEP = [0, 6, 12, 18]


def _tt(days, islands, w, **kw):
    inst = make_instance(days=days, islands=islands, workers_per_island=w,
                         vans=2, trucks=0, M=6, J=60, JCD=60, max_seconds=15, **kw)
    inst.fleet = inst.fleet.model_copy(update={"owned": [
        ov.model_copy(update={"a_c_departures": DEP}) for ov in inst.fleet.owned]})
    return inst


def _all_tokens(segs):
    return {tok for ivs in segs.values() for _t0, _t1, tok in ivs}


def _check_partition(inst, snap, segs):
    """各時刻で「全乗客がちょうど1か所（ノード or エッジ）に居る」ことを確認。"""
    H = snap["H"]
    pids = {p.id for p in inst.passengers}
    for t in range(0, H + 1):
        node, edge = anim.positions_at(segs, t)
        seen = [pid for v in node.values() for pid in v]
        seen += [pid for v in edge.values() for pid in v]
        assert sorted(seen) == sorted(pids), f"t={t}: 乗客の重複/欠落"


def _occupancy_ok(inst, snap, segs):
    """サイクル図に出る B 在籍人数が各島の occupancy_min を満たすか
    （Flow 解は占有を保つので、区間→在籍の復元が正しければ満たされる）。"""
    site_tokens = {f"B{i}": name for i, name in enumerate(inst.staffed_sites)}
    H = snap["H"]
    commit = inst.solver.commit_hours or H
    for t in range(0, commit):
        node, _edge = anim.positions_at(segs, t)
        for tok, name in site_tokens.items():
            occ_min = inst.staffed_sites[name].occupancy_min
            if occ_min:
                assert len(node.get(tok, [])) >= occ_min, \
                    f"t={t} site={name}: 在籍 {len(node.get(tok, []))} < {occ_min}"


# ---------------------------------------------------------------------------
def test_flow_intervals_partition_and_occupancy():
    inst = _tt(14, 1, 4)
    sol = FlowModel(inst).solve(max_seconds=20)
    assert sol.ok, sol.summary()
    snap = anim.route_snapshot(inst)
    segs = anim.intervals_from_flow(inst, sol.decode())
    assert set(segs) == {p.id for p in inst.passengers}
    _check_partition(inst, snap, segs)
    _occupancy_ok(inst, snap, segs)


def test_flow_multi_island_tokens_and_partition():
    inst = _tt(14, 2, 4)
    sol = FlowModel(inst).solve(max_seconds=20)
    assert sol.ok, sol.summary()
    snap = anim.route_snapshot(inst)
    segs = anim.intervals_from_flow(inst, sol.decode())
    # 2 島ぶんの B トークンが（誰かが訪れていれば）現れうる。少なくとも矛盾なく分割。
    _check_partition(inst, snap, segs)
    _occupancy_ok(inst, snap, segs)


def test_intervals_non_negative_and_ordered():
    inst = _tt(14, 1, 4)
    sol = FlowModel(inst).solve(max_seconds=20)
    assert sol.ok
    segs = anim.intervals_from_flow(inst, sol.decode())
    for ivs in segs.values():
        for t0, t1, _tok in ivs:
            assert 0 <= t0 <= t1, (t0, t1)
        # ソート済み（positions_at の後勝ちが意味を持つ前提）
        assert ivs == sorted(ivs)


def test_mermaid_renders_tokens_and_icons():
    inst = _tt(10, 1, 4)
    sol = FlowModel(inst).solve(max_seconds=15)
    assert sol.ok
    snap = anim.route_snapshot(inst)
    segs = anim.intervals_from_flow(inst, sol.decode())
    # 移動が発生している時刻を1つ見つけて、図に徒歩/便アイコンが載ることを確認。
    H = snap["H"]
    found = False
    for t in range(H + 1):
        node, edge = anim.positions_at(segs, t)
        code = anim.anim_mermaid(snap, node, edge)
        assert code.startswith("graph TD")
        assert "A 待機" in code
        if edge:
            assert ("🚶" in code) or ("🚐" in code)
            found = True
    assert found, "全時刻で移動が一切検出されなかった（区間構築の不具合の疑い）"


def test_place_label_human_readable():
    snap = {"sites": [{"name": "島A", "inb": 2, "out": 2}], "cd": None,
            "start": "2025-01-01T00:00:00", "H": 24}
    assert anim.place_label(snap, "Await") == "A 待機"
    assert anim.place_label(snap, "B0") == "島A 滞在"
    assert anim.place_label(snap, "to_B0").startswith("A→島A")
    assert anim.place_label(snap, "from_B0").startswith("島A→A")
    assert "🚐" in anim.place_label(snap, "AtoC")
    assert anim.place_label(snap, "D") == "D 滞在"


def test_occupancy_series_counts_and_order():
    snap = {"sites": [{"name": "島A", "inb": 2, "out": 2},
                      {"name": "島B", "inb": 1, "out": 1}],
            "cd": {"a_c": 3, "c_d": 1, "d_c": 1, "c_a": 3},
            "start": "2025-01-01T00:00:00", "H": 24}
    node_members = {"B0": ["P1", "P2"], "B1": [], "D": ["P3"], "Await": ["P4", "P5", "P6"]}
    series = anim.occupancy_series(snap, node_members)
    # 並び: 各 B 島 → D → A 復帰 → A 待機
    assert [s[0] for s in series] == ["島A", "島B", "D", "A 復帰", "A 待機"]
    assert [s[1] for s in series] == [2, 0, 1, 0, 3]
    assert [s[2] for s in series] == ["B", "B", "D", "A_RET", "A"]


def test_occupancy_series_excludes_transit_and_matches_total():
    """各時刻で「滞在人数の合計 + 移動中人数 = 乗客総数」が保たれる。"""
    inst = _tt(12, 2, 4)
    sol = FlowModel(inst).solve(max_seconds=20)
    assert sol.ok, sol.summary()
    snap = anim.route_snapshot(inst)
    segs = anim.intervals_from_flow(inst, sol.decode())
    total = len(inst.passengers)
    for t in range(0, snap["H"] + 1):
        node, edge = anim.positions_at(segs, t)
        staying = sum(c for _label, c, _kind in anim.occupancy_series(snap, node))
        moving = sum(len(v) for v in edge.values())
        assert staying + moving == total, f"t={t}: {staying}+{moving}!={total}"


def test_token_category_mapping():
    assert anim.token_category("Await") == "A 待機"
    assert anim.token_category("D") == "D 滞在"
    assert anim.token_category("B0") == "島 滞在"
    assert anim.token_category("B12") == "島 滞在"
    assert anim.token_category("AtoC") == "fleet 便"
    assert anim.token_category("CtoA") == "fleet 便"
    for walk in ("to_B0", "from_B1", "CtoD", "DtoC"):
        assert anim.token_category(walk) == "徒歩移動"


def test_gantt_rows_cover_full_horizon_without_gaps():
    """各乗客のガント行が 0..H を隙間・重なりなく連結し、positions_at と一致する。"""
    inst = _tt(12, 2, 4)
    sol = FlowModel(inst).solve(max_seconds=20)
    assert sol.ok, sol.summary()
    snap = anim.route_snapshot(inst)
    segs = anim.intervals_from_flow(inst, sol.decode())
    H = max(int(snap["H"]), 1)
    rows = anim.gantt_rows(snap, segs)
    by_pid: dict[str, list[dict]] = {}
    for r in rows:
        by_pid.setdefault(r["passenger"], []).append(r)
    assert set(by_pid) == {p.id for p in inst.passengers}
    for pid, rs in by_pid.items():
        rs = sorted(rs, key=lambda r: r["start_h"])
        # 0 から H まで隙間なく連結し、隣接バーは別カテゴリ（連結漏れがない）。
        assert rs[0]["start_h"] == 0
        assert rs[-1]["end_h"] == H
        for a, b in zip(rs, rs[1:]):
            assert a["end_h"] == b["start_h"]
            assert a["start_h"] < a["end_h"]
        # 各時間帯の場所が positions_at の後勝ち結果と一致する。
        for r in rs:
            for t in range(r["start_h"], r["end_h"]):
                node, edge = anim.positions_at(segs, t)
                here = next(tok for tok, pids in {**node, **edge}.items() if pid in pids)
                assert anim.place_label(snap, here) == r["place"]


def test_gantt_rows_empty_segments_fill_await():
    """区間ゼロの乗客は全期間 A 待機の 1 本のバーになる。"""
    snap = {"sites": [{"name": "島A", "inb": 2, "out": 2}], "cd": None,
            "start": "2025-01-01T00:00:00", "H": 10}
    rows = anim.gantt_rows(snap, {"P1": []})
    assert rows == [{"passenger": "P1", "start_h": 0, "end_h": 10,
                     "place": "A 待機", "category": "A 待機"}]


def test_fleet_trip_times_dedupes_same_departure_and_matches_tokens():
    """同時刻に出発する複数乗客は同一便として1本にまとめられ、区間の開始時刻と一致する。"""
    inst = _tt(12, 1, 4)
    sol = FlowModel(inst).solve(max_seconds=20)
    assert sol.ok, sol.summary()
    segs = anim.intervals_from_flow(inst, sol.decode())
    trips = anim.fleet_trip_times(segs)
    assert set(trips) == {"AtoC", "CtoA"}
    for tok, times in trips.items():
        assert times == sorted(set(times))  # 重複除去済み・昇順
        expected = {t0 for ivs in segs.values() for t0, _t1, t in ivs if t == tok}
        assert set(times) == expected


def test_fleet_trip_times_empty_when_no_cd_segments():
    assert anim.fleet_trip_times({"P1": [(0, 5, "Await")]}) == {"AtoC": [], "CtoA": []}


def test_is_node_token():
    assert anim.is_node_token("Await")
    assert anim.is_node_token("D")
    assert anim.is_node_token("B0")
    assert anim.is_node_token("B12")
    assert not anim.is_node_token("to_B0")
    assert not anim.is_node_token("AtoC")
    assert not anim.is_node_token("from_B1")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
