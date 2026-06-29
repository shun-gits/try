"""移動可視化（Run solver の解を時刻スライダーでアニメーション）の純粋ロジック。

Run solver の結果から「各乗客が時刻 t にどこに居るか」を求められる正規化区間
（segs[pid] = [(t0, t1, place_token), ...]）を作り、初期配置マップと同じサイクル
図の上に、各拠点の在籍人数と「移動中」の人（🚶徒歩 / 🚐fleet 便）を重ねて描く。

place_token の種別:
  ノード : "Await"(A待機) / "B{i}"(各 B 島) / "D"
  エッジ : "to_B{i}"(A→B島 往路) / "from_B{i}"(B島→A 復路)
           "AtoC"(A→C 便) / "CtoD" / "DtoC" / "CtoA"(C→A 便)
どのトークンにも該当しない時刻は "Await"（A で待機）とみなす。

Streamlit に依存しない（テスト可能）。描画(st.*)は instance_editor 側が担う。
"""
from __future__ import annotations

from route_opt.report import build_stays


def mm_esc(text: str) -> str:
    """mermaid ノードラベル用エスケープ（" は崩れるので ' に、改行は <br/>）。"""
    return str(text).replace('"', "'").replace("\n", "<br/>")


def is_node_token(tok: str) -> bool:
    """place_token がノード（拠点）か（エッジ＝移動中でないか）。"""
    return tok in ("Await", "D") or (tok.startswith("B") and tok[1:].isdigit())


def route_snapshot(inst) -> dict:
    """アニメ描画に必要なルート構造を解いた時点の Instance から抜き出す。

    解いた後にユーザーが Sites を編集しても表示がぶれないよう、図の構造（島の往復
    時間・CD 区間時間・計画開始時刻・horizon）をスナップショットとして固定する。
    """
    sites = [
        {"name": name, "inb": s.segments.inbound_hours, "out": s.segments.outbound_hours}
        for name, s in inst.staffed_sites.items()
    ]
    cd = inst.cd_arm
    cdd = None
    if cd is not None:
        cdd = {"a_c": cd.a_c_hours, "c_d": cd.c_d_hours,
               "d_c": cd.d_c_hours, "c_a": cd.c_a_hours}
    return {"sites": sites, "cd": cdd,
            "start": inst.planning_horizon.start.isoformat(),
            "H": inst.planning_horizon.hours}


def intervals_from_flow(inst, timeline: dict) -> dict[str, list[tuple]]:
    """Flow エンジンの decode() 出力を、乗客別の (t0, t1, token) 区間列へ変換。"""
    cd = inst.cd_arm
    a_c, _c_d, d_c, c_a = cd.a_c_hours, cd.c_d_hours, cd.d_c_hours, cd.c_a_hours
    from_d = d_c + c_a
    site_index = {name: i for i, name in enumerate(inst.staffed_sites)}

    segs: dict[str, list[tuple]] = {}
    for pid, acts in timeline.items():
        ivs: list[tuple] = []
        for a in acts:
            if a["kind"] == "B":
                i = site_index[a["site"]]
                seg = inst.staffed_sites[a["site"]].segments
                arrive, depart = a["arrive"], a["depart"]
                # A→島（往路・徒歩）。初期 B 在室者(arrive=0)は往路を描かない。
                if arrive - seg.inbound_hours >= 0 and seg.inbound_hours > 0:
                    ivs.append((arrive - seg.inbound_hours, arrive, f"to_B{i}"))
                ivs.append((arrive, depart, f"B{i}"))
                # 島→A（復路・徒歩）
                if seg.outbound_hours > 0:
                    ivs.append((depart, depart + seg.outbound_hours, f"from_B{i}"))
            else:  # D トリップ（A→C→D 滞在 D→C→A）
                board, arriveD, returnA = a["board"], a["arriveD"], a["returnA"]
                leaveD = max(arriveD, returnA - from_d)   # D を発つ時刻
                ivs.append((board, board + a_c, "AtoC"))          # A→C（便）
                ivs.append((board + a_c, arriveD, "CtoD"))         # C→D（徒歩）
                ivs.append((arriveD, leaveD, "D"))                 # D 滞在
                ivs.append((leaveD, returnA - c_a, "DtoC"))        # D→C（徒歩）
                ivs.append((returnA - c_a, returnA, "CtoA"))       # C→A（便）
        ivs.sort()
        segs[pid] = ivs
    return segs


def intervals_from_rolling(inst, result) -> dict[str, list[tuple]]:
    """RollingResult（trips/boardings）を乗客別の (t0, t1, token) 区間列へ変換。

    拠点での滞在区間は report.build_stays で再構成し、移動中（エッジ）の区間は
    trips の出発・到着時刻から組み立てる。両者の隙間は A 待機とみなす。
    """
    site_index = {name: i for i, name in enumerate(inst.staffed_sites)}
    cd = inst.cd_arm
    a_c = cd.a_c_hours if cd else 0
    c_a = cd.c_a_hours if cd else 0
    d_c = cd.d_c_hours if cd else 0

    segs: dict[str, list[tuple]] = {p.id: [] for p in inst.passengers}

    # 1) 拠点滞在（B 島 / D）。build_stays が乗客別 site 滞在区間を返す。
    stays = build_stays(result, inst)
    for _, r in stays.iterrows():
        site = r["site"]
        tok = "D" if site == "D" else f"B{site_index[site]}"
        segs.setdefault(r["passenger"], []).append(
            (int(r["start_h"]), int(r["end_h"]), tok))

    # 2) 移動中（エッジ）。trip の出発/到着時刻から往復の各レグを起こす。
    for t in result.trips:
        if t["kind"] == "B":
            i = site_index[t["site"]]
            for p in t["in"]:    # A→島（往路）
                segs.setdefault(p, []).append((t["depart_A"], t["arrive_site"], f"to_B{i}"))
            for p in t["out"]:   # 島→A（復路）
                segs.setdefault(p, []).append((t["arrive_site"], t["return_A"], f"from_B{i}"))
        else:  # CD トリップ
            for p in t["in"]:    # A→C→D
                segs.setdefault(p, []).append((t["depart_A"], t["depart_A"] + a_c, "AtoC"))
                segs.setdefault(p, []).append((t["depart_A"] + a_c, t["arrive_site"], "CtoD"))
            for p in t["out"]:   # D→C→A
                depD = t["depart_A"] + (cd.d_depart_offset if cd else 0)
                segs.setdefault(p, []).append((depD, depD + d_c, "DtoC"))
                segs.setdefault(p, []).append((t["return_A"] - c_a, t["return_A"], "CtoA"))

    for pid in segs:
        segs[pid].sort()
    return segs


def positions_at(segs: dict[str, list[tuple]], t: int) -> tuple[dict, dict]:
    """時刻 t における各拠点・各エッジの在籍乗客を集計して返す。

    返り値 (node_members, edge_members): いずれも token -> ソート済み乗客 id リスト。
    どの区間にも該当しない乗客は "Await"（A 待機）に入れる。
    """
    node_members: dict[str, list[str]] = {}
    edge_members: dict[str, list[str]] = {}
    for pid, ivs in segs.items():
        tok = "Await"
        for t0, t1, token in ivs:
            if t0 <= t < t1:
                tok = token  # 後勝ち（境界では次のレグを優先）
        bucket = node_members if is_node_token(tok) else edge_members
        bucket.setdefault(tok, []).append(pid)
    for d in (node_members, edge_members):
        for k in d:
            d[k].sort()
    return node_members, edge_members


def _roster_label(members: list[str]) -> str:
    """ノードラベル末尾の在籍表記（🧽アイコン＋人数）。改行は mm_esc で <br/> 化。"""
    n = len(members)
    if n == 0:
        return "（不在）"
    icon, cap = "🧽", 12
    icons = icon * min(n, cap) + (f"＋{n - cap}" if n > cap else "")
    return f"{icons}\n{n}名"


def anim_mermaid(snap: dict, node_members: dict, edge_members: dict) -> str:
    """スナップショット構造＋時刻 t の在籍状況から、サイクル図(mermaid)を生成。

    拠点ノードに在籍人数、エッジラベルに移動中人数（🚶徒歩 / 🚐fleet 便）を載せる。
    """
    sites = snap["sites"]
    cd = snap["cd"]

    def walk(tok: str) -> str:
        n = len(edge_members.get(tok, []))
        return f" 🚶{n}" if n else ""

    def ride(tok: str) -> str:
        n = len(edge_members.get(tok, []))
        return f" 🚐{n}" if n else ""

    lines = ["graph TD"]
    lines.append(
        f'  Await(("{mm_esc("A 待機" + chr(10) + _roster_label(node_members.get("Await", [])))}")):::anode')
    lines.append('  Aout(("A 復帰")):::anode')

    if sites:
        for i, s in enumerate(sites):
            nid = f"B{i}"
            label = mm_esc(f"{s['name']}\n{_roster_label(node_members.get(nid, []))}")
            lines.append(f'  {nid}["{label}"]:::bnode')
            lines.append(f"  Await -->|往 {s['inb']}h{walk(f'to_B{i}')}| {nid}")
            lines.append(f"  {nid} -->|復 {s['out']}h{walk(f'from_B{i}')}| Aout")
    else:
        lines.append('  Bnone["B 島 未定義"]:::bnode')
        lines.append("  Await --> Bnone --> Aout")

    if cd is not None:
        lines.append('  Cwait(("C 往")):::cnode')
        lines.append('  Cout(("C 復")):::cnode')
        lines.append(
            f'  D["{mm_esc("D" + chr(10) + _roster_label(node_members.get("D", [])))}"]:::dnode')
        lines.append(f"  Aout -->|A→C {cd['a_c']}h{ride('AtoC')}| Cwait")
        lines.append(f"  Cwait -->|C→D {cd['c_d']}h{walk('CtoD')}| D")
        lines.append(f"  D -->|D→C {cd['d_c']}h{walk('DtoC')}| Cout")
        lines.append(f"  Cout -->|C→A {cd['c_a']}h{ride('CtoA')}| Await")

    lines.append("  classDef anode fill:#4C72B0,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef bnode fill:#55A868,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef cnode fill:#C44E52,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef dnode fill:#DD8452,color:#fff,stroke:#fff,stroke-width:2px;")
    return "\n".join(lines)


def occupancy_series(snap: dict, node_members: dict) -> list[tuple[str, int, str]]:
    """時刻 t の各拠点の滞在人数を (ラベル, 人数, 種別) の並びで返す（棒グラフ用）。

    並びは「各 B 島 → D → A 待機」。種別は色分け用に "B" / "D" / "A" を返す。
    移動中（エッジ）の人は含めない（node_members は拠点滞在者のみ）。
    """
    series: list[tuple[str, int, str]] = []
    for i, s in enumerate(snap["sites"]):
        series.append((s["name"], len(node_members.get(f"B{i}", [])), "B"))
    if snap["cd"] is not None:
        series.append(("D", len(node_members.get("D", [])), "D"))
    series.append(("A 待機", len(node_members.get("Await", [])), "A"))
    return series


def token_category(tok: str) -> str:
    """place_token を色分け用カテゴリへ畳む（ガント / 凡例で状態をまとめて見せる）。

    返す値: "A 待機" / "島 滞在" / "D 滞在" / "fleet 便" / "徒歩移動"。
    便（🚐）は AtoC / CtoA、それ以外の移動レグ（to_B/from_B/CtoD/DtoC）は徒歩扱い。
    """
    if tok == "Await":
        return "A 待機"
    if tok == "D":
        return "D 滞在"
    if tok.startswith("B") and tok[1:].isdigit():
        return "島 滞在"
    if tok in ("AtoC", "CtoA"):
        return "fleet 便"
    return "徒歩移動"


def _token_at(ivs: list[tuple], t: int) -> str:
    """単一乗客の区間列 ivs における時刻 t の place_token（positions_at と同じ後勝ち）。"""
    tok = "Await"
    for t0, t1, token in ivs:
        if t0 <= t < t1:
            tok = token
    return tok


def gantt_rows(snap: dict, segs: dict[str, list[tuple]]) -> list[dict]:
    """乗客別の区間列を、ガントチャート用の行へ整形して返す。

    横軸＝時間・縦軸＝乗客で在不在を一望できるよう、各乗客の 0..H を 1h 刻みで
    サンプリング（positions_at と同じ後勝ち規則）し、連続して同じ場所の時間帯を
    1 本のバーへ連結する。これにより各乗客は 0..H を隙間・重なりなく埋める。

    各行: {"passenger", "start_h", "end_h", "place", "category"}。
    place は人間可読な現在地（place_label）、category は色分け用（token_category）。
    """
    H = max(int(snap["H"]), 1)
    rows: list[dict] = []
    for pid in sorted(segs):
        ivs = sorted(segs[pid])

        def _flush(start: int, end: int, tok: str) -> None:
            rows.append({"passenger": pid, "start_h": start, "end_h": end,
                         "place": place_label(snap, tok), "category": token_category(tok)})

        run_start, run_tok = 0, _token_at(ivs, 0)
        for t in range(1, H):
            tok = _token_at(ivs, t)
            if tok != run_tok:
                _flush(run_start, t, run_tok)
                run_start, run_tok = t, tok
        _flush(run_start, H, run_tok)
    return rows


def place_label(snap: dict, tok: str) -> str:
    """place_token を人間可読な現在地表記に変換する（状態テーブル用）。"""
    sites = snap["sites"]
    if tok == "Await":
        return "A 待機"
    if tok == "D":
        return "D 滞在"
    if tok.startswith("B") and tok[1:].isdigit():
        return f"{sites[int(tok[1:])]['name']} 滞在"
    if tok.startswith("to_B"):
        return f"A→{sites[int(tok[4:])]['name']}（移動中 🚶）"
    if tok.startswith("from_B"):
        return f"{sites[int(tok[6:])]['name']}→A（移動中 🚶）"
    return {
        "AtoC": "A→C（便 🚐）", "CtoD": "C→D（移動中 🚶）",
        "DtoC": "D→C（移動中 🚶）", "CtoA": "C→A（便 🚐）",
    }.get(tok, tok)
