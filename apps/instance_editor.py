"""Instance YAML editor (Streamlit GUI).

固定ルート人員ローテーション最適化のパラメータ YAML を GUI で編集・検証・保存し、
ソルバー実行（単一ウィンドウ / ローリング）と Gantt/CSV 可視化まで行う。

起動: リポジトリルートで
    streamlit run apps/instance_editor.py
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import altair as alt  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

# matplotlib 図中の日本語が文字化け（豆腐 □）するのを防ぐ。
# japanize_matplotlib を import するだけで rcParams のフォントが日本語対応になる。
try:
    import japanize_matplotlib  # noqa: F401,E402
except Exception:  # 未インストール環境でもアプリ自体は起動できるようにする
    pass

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from apps import anim as anim_mod  # noqa: E402
from route_opt import gui_io  # noqa: E402
from route_opt.bench import make_instance  # noqa: E402
from route_opt.flow import FlowModel, FlowUnsupported  # noqa: E402
from route_opt.loader import load_instance  # noqa: E402
from route_opt.model import FullModel, SolutionRecorder  # noqa: E402
from route_opt.report import plot_gantt, trips_df, write_csv  # noqa: E402
from route_opt.rolling import solve_rolling  # noqa: E402
from route_opt.solver_cfg import solver_params_for  # noqa: E402

INSTANCES = pathlib.Path("instances")
OUTDIR = pathlib.Path("out")


# --------------------------------------------------------------------------
def get_doc() -> dict:
    return st.session_state.setdefault("doc", _default_doc())


def set_doc(doc: dict) -> None:
    st.session_state["doc"] = doc
    # ドキュメントを差し替えたら、各 data_editor の編集キャンバスを作り直させる
    # ためにバージョンを進める（_editor 参照）。
    st.session_state["doc_version"] = st.session_state.get("doc_version", 0) + 1


def _default_doc() -> dict:
    f = INSTANCES / "full_cd.yaml"
    if f.exists():
        return gui_io.doc_from_instance(load_instance(f))
    return gui_io.doc_from_instance(
        make_instance(days=5, islands=1, workers_per_island=2, vans=1, trucks=0,
                      M=4, J=8, JCD=6, max_seconds=20)
    )


def _editor(rows: list[dict], cols, key: str, column_config=None) -> list[dict]:
    # st.data_editor に毎回新しい DataFrame を渡すと、（セル確定のたびに走る）
    # 再実行で frontend の編集状態がリセットされ、高速入力時に「まだ確定していない
    # セルの入力が消える」現象が起きる。これは Streamlit 固有の挙動。
    #
    # 対策として、編集キャンバスとなる DataFrame を session_state に一度だけ作り、
    # 以降の再実行では同一オブジェクトを渡して frontend 状態を保持する。実際の編集
    # 差分はウィジェット（key）側に蓄積され、戻り値の DataFrame に反映される。
    # ドキュメント差し替え（doc_version の変化）時だけ、キャンバスと旧差分を破棄して
    # 作り直す。
    ver = st.session_state.get("doc_version", 0)
    base_key = f"_editor_base::{key}"
    stamp_key = f"_editor_ver::{key}"
    if st.session_state.get(stamp_key) != ver:
        st.session_state[base_key] = (
            pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
        )
        st.session_state.pop(key, None)  # 旧ドキュメントの編集差分を破棄
        st.session_state[stamp_key] = ver
    # column_config は毎回ライブで渡す（キャッシュした base とは別に評価されるため、
    # SelectboxColumn の選択肢などはマスタ編集に即追従する）。
    edited = st.data_editor(
        st.session_state[base_key], num_rows="dynamic", width="stretch", key=key,
        column_config=column_config,
    )
    return edited.to_dict("records")


def _dedup_strip(values) -> list[str]:
    """文字列イテラブルを strip し、空と重複を除いて順序を保って返す（マスタ用）。"""
    out: list[str] = []
    for v in values:
        s = str(v or "").strip()
        if s and s not in out:
            out.append(s)
    return out


# --------------------------------------------------------------------------
def tab_load():
    st.subheader("Load / New")
    st.caption("既存の YAML インスタンスを読み込むか、サンプル生成器で雛形を作成して編集を開始します。")
    files = sorted(p.name for p in INSTANCES.glob("*.yaml")) if INSTANCES.exists() else []
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("instances/ から読み込み", files or ["(なし)"],
                        help="instances/ フォルダに保存済みの YAML ファイルを選択して読み込みます。")
    if c2.button("Load", disabled=not files):
        set_doc(gui_io.doc_from_instance(load_instance(INSTANCES / pick)))
        st.success(f"loaded {pick}")

    up = st.file_uploader("YAML をアップロード", type=["yaml", "yml"],
                          help="ローカルの YAML ファイルをアップロードして現在の編集対象に設定します。")
    if up is not None and st.button("Use uploaded"):
        set_doc(gui_io.doc_from_yaml(up.getvalue().decode("utf-8")))
        st.success("uploaded YAML を読み込みました")

    st.divider()
    st.markdown("**サンプル生成器**（雛形インスタンスを生成して編集開始）")
    st.caption("パラメータを指定してランダムな雛形インスタンスを生成します。生成後は各タブで細かく編集できます。")
    g = st.columns(5)
    days = g[0].number_input("days", 1, 60, 14,
                             help="計画期間の総日数。")
    islands = g[1].number_input("islands", 1, 5, 2,
                                help="B 島（有人サイト）の数。")
    workers = g[2].number_input("workers/island", 1, 20, 4,
                                help="各 B 島に配置する作業員数。")
    vans = g[3].number_input("minivans", 0, 10, 2,
                             help="自社保有ミニバンの台数。")
    trucks = g[4].number_input("trucks", 0, 10, 1,
                               help="自社保有トラックの台数。")
    if st.button("サンプル生成"):
        inst = make_instance(days=int(days), islands=int(islands),
                             workers_per_island=int(workers), vans=int(vans),
                             trucks=int(trucks), M=5, J=99, JCD=99, max_seconds=30)
        set_doc(gui_io.doc_from_instance(inst))
        st.success("サンプルを生成しました")


def tab_general():
    doc = get_doc()
    st.subheader("General")
    st.caption("計画期間とカレンダーを設定します。")
    ph = doc["planning_horizon"]
    ph["start"] = st.text_input("planning_horizon.start (ISO 8601)", ph["start"],
                                help="計画の開始日時。例: 2025-01-01T00:00:00")
    ph["end"] = st.text_input("planning_horizon.end (ISO 8601)", ph["end"],
                              help="計画の終了日時。例: 2025-01-15T00:00:00")
    st.caption(f"time_granularity_hours = {doc.get('time_granularity_hours', 1)}（1h 固定）")
    st.markdown("**Calendar — holidays（YYYY-MM-DD）**")
    st.caption("ソルバーが考慮する休日を追加します。休日は輸送コストや制約に影響します。")
    rows = [{"date": d} for d in doc.get("calendar", {}).get("holidays", [])]
    rows = _editor(rows, ["date"], "holidays")
    doc.setdefault("calendar", {})["holidays"] = [
        str(r["date"]).strip() for r in rows if str(r.get("date", "")).strip()
    ]


def tab_vehicles():
    doc = get_doc()
    st.subheader("Vehicle types")
    st.caption(
        "使用する車両タイプを定義します。"
        "各タイプの定員・コストを設定し、Fleet で実際の車両に割り当てます。"
        "列: **name**=タイプ名、**capacity**=乗車定員、"
        "**cost_per_hour**=自社車の時間コスト。"
    )
    rows = _editor(gui_io.vehicle_types_to_rows(doc["vehicle_types"]),
                   ["name", "capacity", "cost_per_hour"], "vt")
    try:
        doc["vehicle_types"] = gui_io.rows_to_vehicle_types(rows)
    except (ValueError, KeyError, TypeError):
        st.warning("vehicle_types の数値を確認してください")

    st.subheader("Fleet — owned")
    st.caption(
        "自社保有車両の一覧です。"
        "列: **id**=車両固有 ID、**type**=上で定義した vehicle_types の名前、"
        "**initial_location**=計画開始時の初期位置（例: A）、"
        "**a_c_departures**=その車両の A→C 便の固定ダイヤ（1日の出発時刻・時。例: 6, 14）。"
        "便ダイヤは全車の和、各時刻の定員は「その時刻に出発する車両」で決まります"
        "（同じ時刻に2台置けば定員2台分）。空欄ならその車両はダイヤを持ちません。"
    )
    fleet = doc.setdefault("fleet", {"owned": []})
    orows = _editor(
        [{"id": v.get("id", ""), "type": v.get("type", ""),
          "initial_location": v.get("initial_location", "A"),
          "a_c_departures": ", ".join(str(t) for t in v.get("a_c_departures", []))}
         for v in fleet.get("owned", [])],
        ["id", "type", "initial_location", "a_c_departures"], "owned")
    new_owned = []
    for r in orows:
        vid = str(r.get("id", "")).strip()
        if not vid:
            continue
        deps: list[int] = []
        for tok in str(r.get("a_c_departures", "") or "").replace("、", ",").split(","):
            tok = tok.strip()
            if not tok:
                continue
            try:
                deps.append(int(tok))
            except ValueError:
                st.warning(f"a_c_departures: 整数で指定してください（無効な値: {tok}）")
        ov = {"id": vid, "type": str(r.get("type", "")).strip(),
              "initial_location": str(r.get("initial_location") or "A").strip()}
        if deps:
            ov["a_c_departures"] = sorted(set(deps))
        new_owned.append(ov)
    fleet["owned"] = new_owned
    fleet.pop("rental", None)   # レンタル機能は廃止


def _mm_esc(text: str) -> str:
    """mermaid ノードラベル用エスケープ（"" は崩れるので ' に、改行は <br/>）。"""
    return str(text).replace('"', "'").replace("\n", "<br/>")


def _sites_mermaid(doc: dict) -> str:
    """固定ルート Await→Bx→Aout→C→D→C→Await のサイクル図(mermaid)を生成。

    ルートは常に A→B→A→C→D→C→A で固定（CD-arm の有効/無効という概念はない）。
    ノードに人数・滞在レンジ、エッジに各区間の移動時間を載せる。B 島ノードには
    `click ... call __nodeClicked()` を付け、双方向コンポーネント側でクリックを拾う。
    """
    sites = doc.get("staffed_sites", {})
    cd = doc.get("cd_arm") or {}
    tmp = doc.get("temporary_site") or {}

    lines = ["graph TD"]
    lines.append('  Await(("A 待機")):::anode')
    lines.append('  Aout(("A 復帰")):::anode')

    if sites:
        for i, (name, s) in enumerate(sites.items()):
            seg = s.get("segments", {})
            inb = seg.get("inbound_hours", "?")
            out = seg.get("outbound_hours", "?")
            stay = s.get("stay", {})
            lo = stay.get("min_hours", 0)
            hi = stay.get("max_hours", lo)
            occ = s.get("occupancy_min", 0)
            nid = f"B{i}"
            label = _mm_esc(f"{name}\n占有≥{occ}・滞在{lo}〜{hi}h")
            lines.append(f'  {nid}["{label}"]:::bnode')
            lines.append(f"  Await -->|往 {inb}h| {nid}")
            lines.append(f"  {nid} -->|復 {out}h| Aout")
            lines.append(f"  click {nid} call __nodeClicked()")
    else:
        lines.append('  Bnone["B 島 未定義"]:::bnode')
        lines.append("  Await --> Bnone --> Aout")

    # CD-arm / D は固定ルートの一部として常に描画する。
    ac = cd.get("a_c_hours", "?")
    cdh = cd.get("c_d_hours", "?")
    dc = cd.get("d_c_hours", "?")
    ca = cd.get("c_a_hours", "?")
    hrs = gui_io.d_stay_hours(tmp.get("d_stay_table", {}))
    d_sub = f"滞在{min(hrs)}〜{max(hrs)}h" if hrs else "一時サイト"
    lines.append('  Cwait(("C 往")):::cnode')
    lines.append('  Cout(("C 復")):::cnode')
    lines.append(f'  D["{_mm_esc("D" + chr(10) + d_sub)}"]:::dnode')
    lines.append(f"  Aout -->|A→C {ac}h| Cwait")
    lines.append(f"  Cwait -->|C→D {cdh}h| D")
    lines.append(f"  D -->|D→C {dc}h| Cout")
    lines.append(f"  Cout -->|C→A {ca}h| Await")

    lines.append("  classDef anode fill:#4C72B0,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef bnode fill:#55A868,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef cnode fill:#C44E52,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef dnode fill:#DD8452,color:#fff,stroke:#fff,stroke-width:2px;")
    return "\n".join(lines)


# クリックを拾える双方向 mermaid コンポーネント（apps/components/mermaid_click/index.html）。
_MERMAID_DIR = pathlib.Path(__file__).resolve().parent / "components" / "mermaid_click"
_mermaid_click_component = components.declare_component(
    "mermaid_click", path=str(_MERMAID_DIR))


def mermaid_click(code: str, selected: str | None = None, key: str = "sites_graph") -> dict | None:
    """mermaid 図を描画し、クリックされたノード情報 {node, ts} を返す（未クリックは None）。

    selected: 太枠で強調表示するノード id（例: "B0"）。
    """
    return _mermaid_click_component(code=code, selected=selected, key=key, default=None)


def tab_sites():
    doc = get_doc()
    sites = doc.setdefault("staffed_sites", {})

    # 上のサイクル図でクリックされたノード（"B{i}"）→ 対象サイト名を解決。
    # 双方向コンポーネントの返り値は session_state["sites_graph"] に入る。
    site_names = list(sites.keys())
    selected_name: str | None = None
    clicked = st.session_state.get("sites_graph")
    if isinstance(clicked, dict):
        node = str(clicked.get("node", ""))
        if node.startswith("B") and node[1:].isdigit():
            idx = int(node[1:])
            if 0 <= idx < len(site_names):
                selected_name = site_names[idx]

    # 左＝サイクル図（クリック可）／右＝パラメータ設定 の 2 カラム構成。
    left, right = st.columns([5, 6], gap="large")

    with left:
        st.subheader("ルート構造（サイクル図）")
        st.caption(
            "A 待機 → B 島（いずれか1島）→ A 復帰 → C → D → C → A 待機 の固定サイクルです。"
            "B 島ノードをクリックすると右の編集パネルが開きます。値は右の編集を即時反映します。"
        )
        diagram_slot = st.container()  # 図は左カラム・値は右の編集後に描画

    with right:
        st.subheader("Staffed sites (B islands)")
        st.caption(
            "有人配置が必要な B 島サイトを管理します。"
            "サイトごとに必要人数・滞在時間・移動時間・カテゴリ要件などを設定できます。"
        )

        add = st.columns([3, 1])
        new_name = add[0].text_input("新規サイト名", key="new_site",
                                     help="追加する B 島サイトの名前を入力して「サイト追加」を押してください。")
        if add[1].button("サイト追加") and new_name.strip():
            sites.setdefault(new_name.strip(), {
                "occupancy_min": 1, "category_requirements": {},
                "stay": {"min_hours": 24, "max_hours": 48},
                "replacement_required": True, "ride_together": [],
                "segments": {"inbound_hours": 2, "outbound_hours": 2},
            })

        for name in list(sites.keys()):
            s = sites[name]
            with st.expander(f"site: {name}", expanded=(name == selected_name)):
                if st.button(f"削除 {name}", key=f"del_{name}"):
                    del sites[name]
                    st.rerun()
                c = st.columns(2)
                s["occupancy_min"] = int(c[0].number_input("occupancy_min", 0, 50,
                                         int(s.get("occupancy_min", 0)), key=f"occ_{name}",
                                         help="このサイトに常に駐在していなければならない最低人数。"))
                s["replacement_required"] = c[1].checkbox("replacement_required",
                                              bool(s.get("replacement_required", True)), key=f"rep_{name}",
                                              help="ON: 現在の駐在員が帰る前に交代要員の到着が必須。OFF: 空席を許容。")
                s.setdefault("stay", {})
                cs = st.columns(2)
                s["stay"]["min_hours"] = int(cs[0].number_input("stay.min_hours", 0, 1000,
                                              int(s["stay"].get("min_hours", 24)), key=f"smin_{name}",
                                              help="作業員がこのサイトに滞在しなければならない最短時間（時間）。"))
                s["stay"]["max_hours"] = int(cs[1].number_input("stay.max_hours", 0, 1000,
                                              int(s["stay"].get("max_hours", 48)), key=f"smax_{name}",
                                              help="作業員がこのサイトに滞在できる最長時間（時間）。"))
                sc = st.columns(2)
                s.setdefault("segments", {})
                s["segments"]["inbound_hours"] = int(sc[0].number_input("segments.inbound_hours", 0, 100,
                                                  int(s["segments"].get("inbound_hours", 2)), key=f"in_{name}",
                                                  help="A 拠点からこのサイトまでの片道移動時間（時間）。往路に消費されます。"))
                s["segments"]["outbound_hours"] = int(sc[1].number_input("segments.outbound_hours", 0, 100,
                                                  int(s["segments"].get("outbound_hours", 2)), key=f"out_{name}",
                                                  help="このサイトから A 拠点までの片道移動時間（時間）。復路に消費されます。"))
                st.markdown("category_requirements (category → min)")
                st.caption("このサイトに同時に駐在していなければならないカテゴリごとの最低人数を指定します。")
                rows = _editor(gui_io.intmap_to_rows(s.get("category_requirements", {}), "category", "min"),
                               ["category", "min"], f"catreq_{name}")
                s["category_requirements"] = gui_io.rows_to_intmap(rows, "category", "min")
                s["ride_together"] = gui_io.str_to_ride_together(
                    st.text_input("ride_together（例: Category1,Category2; Category3,Category4）",
                                  gui_io.ride_together_to_str(s.get("ride_together", [])), key=f"rt_{name}",
                                  help="同じ便に必ず同乗させるカテゴリグループ。グループ内はカンマ区切り、グループ間はセミコロン区切り。")
                )

        st.divider()
        st.subheader("CD-arm (A→C→D→C→A) — 区間移動時間")
        st.caption(
            "固定ルートの C 経由区間です（ルートは常に A→B→A→C→D→C→A）。"
            "各区間の片道移動時間（時間）を設定してください。"
        )
        cd = doc.get("cd_arm") or {"a_c_hours": 3, "c_d_hours": 1, "d_c_hours": 1, "c_a_hours": 3}
        cc = st.columns(4)
        labels = {
            "a_c_hours": "A→C の移動時間（時間）",
            "c_d_hours": "C→D の移動時間（時間）",
            "d_c_hours": "D→C の移動時間（時間）",
            "c_a_hours": "C→A の移動時間（時間）",
        }
        for i, k in enumerate(["a_c_hours", "c_d_hours", "d_c_hours", "c_a_hours"]):
            cd[k] = int(cc[i].number_input(k, 0, 100, int(cd.get(k, 1)), key=f"cd_{k}",
                                           help=labels[k]))
        # A→C 便の固定ダイヤは Vehicles & Fleet タブの owned（車両ごと）で設定します。
        st.caption("A→C 便の固定ダイヤ（出発時刻）は **Vehicles & Fleet** タブの "
                   "Fleet — owned で車両ごとに設定します。")
        cd.pop("a_c_departures", None)   # 旧スキーマの全車共有ダイヤは廃止
        doc["cd_arm"] = cd

        st.subheader("Temporary site D")
        st.caption(
            "固定ルート終点の一時滞在サイト D です。"
            "d_stay_table で「体重カテゴリ × 同乗総人数 n → 必要滞在時間」を設定します。"
        )
        d = doc.get("temporary_site") or {"d_stay_table": {1: 12}, "occupancy_max": None}
        st.caption(
            "列: **weight**=体重カテゴリ（small / large、`*` で全カテゴリ共通）、"
            "**n**=その便の同乗総人数、**hours**=必要な最低滞在時間。"
        )
        rows = _editor(gui_io.d_stay_table_to_rows(d.get("d_stay_table", {})),
                       ["weight", "n", "hours"], "dtable")
        d["d_stay_table"] = gui_io.rows_to_d_stay_table(rows)
        cap = st.text_input("occupancy_max（空欄=無制限）",
                            "" if d.get("occupancy_max") is None else str(d["occupancy_max"]),
                            help="D サイトに同時に滞在できる最大人数。空欄にすると上限なし。")
        d["occupancy_max"] = int(cap) if cap.strip() else None
        doc["temporary_site"] = d

    # 編集後の doc を使って左カラムのサイクル図を描画（双方向：B 島クリックで selected_name に反映）。
    sel_node = f"B{site_names.index(selected_name)}" if selected_name in site_names else None
    with diagram_slot:
        mermaid_click(_sites_mermaid(doc), selected=sel_node, key="sites_graph")


def tab_passengers():
    doc = get_doc()

    st.subheader("Masters（category / weight）")
    st.caption(
        "passengers の category / weight 選択肢の単一の源（マスタ）です。"
        "ここで定義した値だけを下の Passengers で選択できます。"
        "既存インスタンスを読み込んだ場合は利用中の値で自動補完されます。"
        "新しいカテゴリ・体重区分を使いたいときは、まずここに追加してください。"
    )
    masters = gui_io.ensure_masters(doc)
    mc = st.columns(2)
    with mc[0]:
        st.markdown("**categories**")
        crows = _editor([{"category": c} for c in masters["categories"]],
                        ["category"], "master_cat")
        masters["categories"] = _dedup_strip(r.get("category") for r in crows)
    with mc[1]:
        st.markdown("**weights**")
        st.caption("体重区分（例: small / large）。d_stay_table の per-weight キーと揃えてください。")
        wrows = _editor([{"weight": w} for w in masters["weights"]],
                        ["weight"], "master_weight")
        masters["weights"] = _dedup_strip(r.get("weight") for r in wrows)
    doc["masters"] = masters

    st.subheader("Passengers")
    st.caption(
        "乗客（作業員）のマスタです。"
        "列: **id**=乗客固有 ID、**category**=乗客カテゴリ（上の Masters から選択）、"
        "**weight**=体重区分（上の Masters から選択）。"
    )
    cat_opts = gui_io.category_options(doc)
    w_opts = gui_io.weight_options(doc)
    pax_cfg = {
        "category": st.column_config.SelectboxColumn("category", options=cat_opts,
                                                     help="Masters の categories から選択。"),
        "weight": st.column_config.SelectboxColumn("weight", options=w_opts,
                                                   help="Masters の weights から選択。"),
    }
    prows = _editor(doc.get("passengers", []), ["id", "category", "weight"], "pax",
                    column_config=pax_cfg)
    default_w = w_opts[0] if w_opts else "small"
    doc["passengers"] = [
        {
            "id": str(r.get("id", "")).strip(),
            "category": str(r.get("category") or "").strip(),
            "weight": (str(r.get("weight") or "").strip() or default_w),
        }
        for r in prows if str(r.get("id", "")).strip()
    ]

    st.subheader("Passenger rules（赴任可能な B 島サイト）")
    st.caption(
        "各乗客が赴任できる B 島サイトを選択します（passengers と staffed_sites の依存に基づく選択式）。"
        "サイトを 1 つも選ばない乗客は B 島に赴任できません（passenger_rules から省略されます）。"
    )
    site_names = list(doc.get("staffed_sites", {}).keys())
    prev_rules = doc.get("passenger_rules", {})
    passengers = doc.get("passengers", [])
    if not passengers:
        st.info("乗客が未定義です。上の Passengers で追加してください。")
        doc["passenger_rules"] = {}
    elif not site_names:
        st.info("B 島サイトが未定義です。Sites タブで追加してください。")
        doc["passenger_rules"] = {}
    else:
        hdr = st.columns([3, 7])
        hdr[0].markdown("**passenger**")
        hdr[1].markdown("**allowed_sites**")
        rules: dict[str, dict] = {}
        for p in passengers:
            pid = str(p.get("id", "")).strip()
            if not pid:
                continue
            cur = [s for s in prev_rules.get(pid, {}).get("allowed_sites", [])
                   if s in site_names]
            row = st.columns([3, 7])
            row[0].markdown(f"`{pid}`")
            sel = row[1].multiselect(
                "allowed_sites", site_names, default=cur,
                key=f"rule_sites_{pid}", label_visibility="collapsed",
                help="この乗客が赴任できる B 島サイト。空なら B 島へは赴任しません。")
            if sel:
                rules[pid] = {"allowed_sites": sel}
        doc["passenger_rules"] = rules

    st.subheader("Initial state")
    # 左＝初期配置マップ（サイクル図）／右＝Initial state 編集表 の 2 カラム構成。
    left, right = st.columns([5, 6], gap="large")

    with left:
        st.markdown("**初期配置マップ（ルート構造のサイクル図上に表示）**")
        st.caption(
            "Sites タブと同じ A 待機 → B 島 → A 復帰 → C → D → C → A 待機 のサイクル図上に、"
            "計画開始時点で各拠点に居る人数を 🧽 アイコンと数値で表示します（右の表を編集するとリアルタイム更新）。"
            "C は中継点のため誰も滞在しません。initial_state に未設定の乗客は A 待機として表示します。"
        )
        map_slot = st.container()  # マップは編集後の doc を使うため右の編集後に描画

    with right:
        st.caption(
            "計画開始時点での各乗客の状態を、経路設定に基づくフォームで設定します。"
            "**location**: A（本拠点）/ B 島名 / D（一時サイト）/ "
            "A->C（D へ向かう移動中）/ C->A（D から A へ戻る移動中）から選択します。"
            "**arrived_at**: 現地への到着日時（ISO 8601）。空欄の場合は計画開始時刻とみなします。"
            "A->C は D 到着時刻、C->A は A 到着時刻を指し、いずれも必須です。"
        )

        # location は経路設定（staffed_sites / cd_arm）から導出した候補だけを許可する。
        # これにより data_editor の自由入力で不正な location が混入するのを防ぐ。
        site_names = list(doc.get("staffed_sites", {}).keys())
        loc_options = ["A", *site_names, "D"]
        if doc.get("cd_arm") is not None:
            loc_options += ["A->C", "C->A"]
        transit_locs = {"A->C", "C->A"}

        # 既存 initial_state を乗客 ID で引けるようにする（未記載は A 既定）。
        prev = {s.get("passenger_id"): s for s in doc.get("initial_state", [])}

        passengers = doc.get("passengers", [])
        if not passengers:
            st.info("乗客が未定義です。上の Passengers で追加してください。")
            doc["initial_state"] = []
        else:
            hdr = st.columns([3, 3, 4])
            hdr[0].markdown("**passenger**")
            hdr[1].markdown("**location**")
            hdr[2].markdown("**arrived_at (ISO 8601)**")
            out = []
            for p in passengers:
                pid = str(p.get("id", "")).strip()
                if not pid:
                    continue
                cur = prev.get(pid, {})
                cur_loc = str(cur.get("location") or "A").strip()
                if cur_loc not in loc_options:
                    cur_loc = "A"
                cur_at = str(cur.get("arrived_at") or "").strip()

                row = st.columns([3, 3, 4])
                row[0].markdown(f"`{pid}`")
                loc = row[1].selectbox(
                    "location", loc_options, index=loc_options.index(cur_loc),
                    key=f"init_loc_{pid}", label_visibility="collapsed",
                    help="経路設定に基づく拠点・移動区間から選択します。")
                # arrived_at は移動中（A->C / C->A）では必須、それ以外は任意。
                is_transit = loc in transit_locs
                at = row[2].text_input(
                    "arrived_at",
                    cur_at,
                    key=f"init_at_{pid}", label_visibility="collapsed",
                    placeholder="必須: 到着日時" if is_transit else "任意（空欄=計画開始時刻）",
                    help="ISO 8601 形式の到着日時。A->C / C->A では必須です。")
                at = at.strip()
                if is_transit and not at:
                    row[2].warning("arrived_at は必須です")

                # location / arrived_at 以外の handoff 用フィールド
                # （earliest_departure / last_duty 等）は読み込んだ値を引き継ぎ、
                # save/load の round-trip で失わないようにする。
                out.append(gui_io.merge_initial_state(cur, pid, loc, at))
            doc["initial_state"] = out

    with map_slot:
        if not doc.get("passengers"):
            st.info("乗客が未定義のため表示できません。上の Passengers で追加してください。")
        else:
            mermaid_click(_initial_state_mermaid(doc), key="init_state_graph")


def _validated_instance():
    return gui_io.instance_from_doc(get_doc())


def _instance_for_solve():
    """ソルバー実行用 Instance を返す。

    solver パラメータは configs/solver_config.yaml と理論値から自動設定する。
    利用者が編集した doc の solver フィールドは無視される。
    """
    inst = _validated_instance()
    sp = solver_params_for(inst)
    return inst.model_copy(update={"solver": sp})


def tab_save():
    doc = get_doc()
    st.subheader("Validate & Save")
    st.caption(
        "保存前に必ず Validate を実行してスキーマの整合性を確認してください。"
        "検証エラーがある場合は保存できません。"
    )
    if st.button("Validate", help="現在の設定がスキーマを満たすかチェックします。エラーがある場合は詳細を表示します。"):
        try:
            _validated_instance()
            st.success("OK: スキーマ検証に成功しました")
        except Exception as e:  # noqa: BLE001
            st.error("検証エラー:")
            st.code(str(e))
    st.divider()
    name = st.text_input("保存ファイル名", "edited.yaml",
                         help="instances/ フォルダに保存するファイル名。既存ファイル名を指定すると上書きします。")
    if st.button("instances/ に保存", help="検証成功後、サーバーの instances/ フォルダに YAML を保存します。"):
        try:
            _validated_instance()
        except Exception as e:  # noqa: BLE001
            st.error("検証に失敗したため保存しません:")
            st.code(str(e))
        else:
            INSTANCES.mkdir(exist_ok=True)
            (INSTANCES / name).write_text(gui_io.yaml_from_doc(doc), encoding="utf-8")
            st.success(f"保存しました: instances/{name}")
    st.download_button("Download YAML", gui_io.yaml_from_doc(doc),
                       file_name="instance.yaml", mime="text/yaml",
                       help="現在の設定を YAML ファイルとしてローカルにダウンロードします。")
    with st.expander("YAML プレビュー"):
        st.caption("現在編集中のインスタンスを YAML 形式で確認できます。")
        st.code(gui_io.yaml_from_doc(doc), language="yaml")


def _show_improvement(rows: list[dict], header: bool = True) -> None:
    """解の改善グラフ（時刻×コスト）を描画する。

    rows は SolutionRecorder が記録した改善解の列。ソルバーがより安い解を
    見つけるたびにコストが下がる様子（最後の点が採用解）と、下限（bound）が
    どこまで上がったか（コストとの差 = gap、0 まで縮めば最適確定）を示す。
    header=False のときは見出しを描かない（Rolling の各ウィンドウ内で再利用する）。
    """
    if header:
        st.subheader("解の改善グラフ（時刻×コスト）")
    if not rows:
        st.warning("時間内に feasible 解が見つかりませんでした（改善グラフなし）。"
                   "規模が大きすぎる可能性があります。max_seconds を増やすか "
                   "Rolling horizon を試してください。")
        return
    st.caption(
        "ソルバーがより安い feasible 解を見つけるたびにコスト（青）が下がります。"
        "最後の点が採用解です。下限（橙）はコストとの差が **gap** で、"
        "下限がコストまで上がり切れば最適（OPTIMAL）が確定します。"
        "線が途中で水平のまま時間切れなら『まだ改善余地を探索中だった』ことを意味します。"
    )
    df = pd.DataFrame(rows)
    chart = (df.set_index("t")[["cost", "bound"]]
             .rename(columns={"cost": "コスト", "bound": "下限(bound)"}))
    st.line_chart(chart, x_label="経過秒 (s)", y_label="コスト")
    table = df.rename(columns={"n": "解#", "t": "発見時刻s",
                               "cost": "コスト", "bound": "下限"})
    table["gap%"] = ((df["cost"] - df["bound"]) / df["cost"].where(df["cost"] != 0, 1)
                     * 100).round(1)
    st.dataframe(table, width="stretch", hide_index=True)


def _show_flow_schedule(mdl, sol) -> None:
    """Flow エンジンの解を復号し、個体（乗客）スケジュールを表で示す。"""
    gap = None
    c, b = sol.solver.ObjectiveValue(), sol.solver.BestObjectiveBound()
    if c > 0:
        gap = (c - b) / c * 100
    st.caption(
        "Flow は固定ダイヤ上の匿名フローを解いた後、経路分解で個体（乗客 id）を復元します。"
        + (f" 下限との gap={gap:.1f}%（最適性の証明用。解自体はほぼ最適）。" if gap else ""))
    tl = sol.decode()
    rows = []
    for pid, acts in tl.items():
        for a in acts:
            if a["kind"] == "B":
                rows.append({"乗客": pid, "種別": "B", "サイト": a["site"],
                             "開始h": a["arrive"], "終了h": a["depart"], "便/積載": ""})
            else:
                rows.append({"乗客": pid, "種別": "D", "サイト": "D",
                             "開始h": a["arriveD"], "終了h": a["returnA"],
                             "便/積載": f"便{a['board']} / n{a['load']}"})
    if not rows:
        st.warning("復号結果が空です。")
        return
    df = pd.DataFrame(rows).sort_values(["乗客", "開始h"])
    st.dataframe(df, width="stretch", hide_index=True)


def tab_run():
    st.subheader("Run solver")
    st.caption("現在の設定でソルバーを実行します（Flow エンジン）。")
    try:
        inst = _instance_for_solve()
    except Exception as e:  # noqa: BLE001
        st.error("先に検証を通してください:")
        st.code(str(e))
        return

    sp = inst.solver
    st.info(
        f"ソルバーパラメータ（configs/solver_config.yaml + 自動計算）: "
        f"M={sp.max_visits_per_passenger} / J={sp.trips_per_site} / "
        f"JCD={sp.trips_cd} / max_seconds={sp.max_seconds}s"
        + (f" / commit_hours={sp.commit_hours}h" if sp.commit_hours else "")
    )

    if st.button("Solve (single, flow)"):
        try:
            mdl = FlowModel(inst)
        except FlowUnsupported as e:
            st.error("Flow エンジンは未対応の構成です:")
            st.code(str(e))
            st.info("固定ダイヤ（Fleet — owned の各車両 a_c_departures）を設定し、"
                    "各乗客の allowed_sites を単一 B サイトにしてください。")
        else:
            rec = SolutionRecorder()
            with st.spinner("solving (flow)..."):
                sol = mdl.solve(callback=rec)
            st.code(sol.summary())
            _show_improvement(rec.rows)
            if sol.ok:
                _show_flow_schedule(mdl, sol)
                # 移動可視化タブ用に「乗客別タイムライン」を保存する。
                st.session_state["anim"] = {
                    "snap": anim_mod.route_snapshot(inst),
                    "segs": anim_mod.intervals_from_flow(inst, sol.decode()),
                    "source": "Single window / Flow",
                }
                st.success("→「移動可視化」タブで時刻スライダーによるアニメーションを確認できます。")


# --------------------------------------------------------------------------
# 配色（拠点種別ごと）
_COL_A = "#4C72B0"  # A 本拠点
_COL_B = "#55A868"  # B 有人サイト
_COL_C = "#C44E52"  # C 中継点
_COL_D = "#DD8452"  # D 一時サイト


def _initial_state_mermaid(doc: dict) -> str:
    """初期配置を「ルート構造（サイクル図）」上に重ねた mermaid を生成。

    ノード構成・配色は _sites_mermaid と揃え（A 待機→Bx→A 復帰→C→D→C→A 待機）、
    各拠点ノードに「計画開始時点でそこに居る乗客」を載せる。乗客の初期位置は
    A 待機 / B 島名 / D のいずれかで、initial_state 未記載は A 待機（model.py と同じ既定）。
    C は中継点のため誰も滞在しない。
    """
    sites = doc.get("staffed_sites", {})
    cd = doc.get("cd_arm") or {}
    tmp = doc.get("temporary_site") or {}
    passengers = doc.get("passengers", [])

    # 乗客 → 初期位置（未記載は A 待機）。
    loc_of: dict[str, str] = {p.get("id"): "A" for p in passengers}
    for stt in doc.get("initial_state", []):
        pid = stt.get("passenger_id")
        if pid in loc_of:
            loc_of[pid] = str(stt.get("location") or "A").strip() or "A"

    members: dict[str, list[str]] = {}
    for pid, loc in loc_of.items():
        members.setdefault(loc, []).append(pid)
    for loc in members:
        members[loc].sort()

    icon, cap = "🧽", 12  # 在籍人数を表すアイコンと、並べる上限数

    def _roster(loc: str) -> str:
        """ノードラベル末尾に付ける在籍人数表記（アイコン＋人数）。改行は _mm_esc で <br/> 化。"""
        n = len(members.get(loc, []))
        if n == 0:
            return "（不在）"
        icons = icon * min(n, cap) + (f"＋{n - cap}" if n > cap else "")
        return f"{icons}\n{n}名"

    lines = ["graph TD"]
    lines.append(f'  Await(("{_mm_esc("A 待機" + chr(10) + _roster("A"))}")):::anode')
    lines.append('  Aout(("A 復帰")):::anode')

    if sites:
        for i, (name, s) in enumerate(sites.items()):
            seg = s.get("segments", {})
            inb = seg.get("inbound_hours", "?")
            out = seg.get("outbound_hours", "?")
            nid = f"B{i}"
            label = _mm_esc(f"{name}\n{_roster(name)}")
            lines.append(f'  {nid}["{label}"]:::bnode')
            lines.append(f"  Await -->|往 {inb}h| {nid}")
            lines.append(f"  {nid} -->|復 {out}h| Aout")
    else:
        lines.append('  Bnone["B 島 未定義"]:::bnode')
        lines.append("  Await --> Bnone --> Aout")

    ac = cd.get("a_c_hours", "?")
    cdh = cd.get("c_d_hours", "?")
    dc = cd.get("d_c_hours", "?")
    ca = cd.get("c_a_hours", "?")
    lines.append('  Cwait(("C 往")):::cnode')
    lines.append('  Cout(("C 復")):::cnode')
    lines.append(f'  D["{_mm_esc("D" + chr(10) + _roster("D"))}"]:::dnode')
    lines.append(f"  Aout -->|A→C {ac}h| Cwait")
    lines.append(f"  Cwait -->|C→D {cdh}h| D")
    lines.append(f"  D -->|D→C {dc}h| Cout")
    lines.append(f"  Cout -->|C→A {ca}h| Await")

    # 既知拠点(A/B/D)以外に居る乗客はサイクル外の「その他」ノードへ。
    known = {"A", "D"} | set(sites.keys())
    extras = sorted(loc for loc in members if loc not in known)
    for j, loc in enumerate(extras):
        nid = f"X{j}"
        lines.append(f'  {nid}["{_mm_esc(loc + chr(10) + _roster(loc))}"]:::xnode')

    lines.append("  classDef anode fill:#4C72B0,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef bnode fill:#55A868,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef cnode fill:#C44E52,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef dnode fill:#DD8452,color:#fff,stroke:#fff,stroke-width:2px;")
    lines.append("  classDef xnode fill:#888888,color:#fff,stroke:#fff,stroke-width:2px;")
    return "\n".join(lines)


def _occupancy_bar_chart(snap: dict, node_members: dict, total: int) -> alt.Chart:
    """時刻 t の各拠点の滞在人数を棒グラフ(Altair)にして返す。

    matplotlib の PNG だと再生・シークに追従できないため、クライアント側で描画される
    Altair(Vega-Lite) を使い、スライダー / 再生に合わせて滑らかに更新されるようにする。
    色は図のノード配色に合わせ（B 島=緑 / D=橙 / A 待機=青）、フレーム間で高さの
    比較がぶれないよう y 軸は乗客総数で固定する。
    """
    series = anim_mod.occupancy_series(snap, node_members)
    palette = {"B": _COL_B, "D": _COL_D, "A": _COL_A}
    df = pd.DataFrame({
        "拠点": [s[0] for s in series],
        "滞在人数": [s[1] for s in series],
        "種別": [s[2] for s in series],
    })
    order = [s[0] for s in series]  # occupancy_series の並び（各 B 島→D→A 待機）を保持
    y_max = max(total, 1)
    base = alt.Chart(df).encode(
        x=alt.X("拠点:N", sort=order, axis=alt.Axis(labelAngle=0, title=None)),
        y=alt.Y("滞在人数:Q", scale=alt.Scale(domain=[0, y_max]),
                axis=alt.Axis(title="滞在人数")),
    )
    bars = base.mark_bar(size=36).encode(
        color=alt.Color("種別:N",
                        scale=alt.Scale(domain=list(palette), range=list(palette.values())),
                        legend=None),
    )
    labels = base.mark_text(dy=-6, fontSize=12).encode(text="滞在人数:Q")
    return (bars + labels).properties(
        height=240, title="各拠点の滞在人数（移動中は含まない）")


def _natural_passenger_order(pids: list[str]) -> list[str]:
    """乗客 id を自然順（P2 < P10）に並べる。数字を含まない id は末尾へ。"""
    import re

    def key(pid: str):
        m = re.search(r"\d+", pid)
        return (0, int(m.group())) if m else (1, pid)

    return sorted(pids, key=key)


def _gantt_chart(snap: dict, segs: dict) -> alt.Chart:
    """乗客別タイムライン（ガントチャート）を Altair で返す。

    縦軸＝乗客、横軸＝時間（計画開始からの h）。各乗客の在不在・移動を、状態カテゴリ
    （A 待機 / 島 滞在 / D 滞在 / fleet 便 / 徒歩移動）で色分けした横棒で示す。
    色はサイクル図・棒グラフのノード配色に合わせる。
    """
    rows = anim_mod.gantt_rows(snap, segs)
    df = pd.DataFrame(rows)
    palette = {"A 待機": _COL_A, "島 滞在": _COL_B, "D 滞在": _COL_D,
               "fleet 便": _COL_C, "徒歩移動": "#999999"}
    order = _natural_passenger_order(list({r["passenger"] for r in rows}))
    H = max(int(snap["H"]), 1)
    return alt.Chart(df).mark_bar().encode(
        x=alt.X("start_h:Q", scale=alt.Scale(domain=[0, H], nice=False),
                axis=alt.Axis(title="時刻 t（計画開始からの h）")),
        x2="end_h:Q",
        y=alt.Y("passenger:N", sort=order, axis=alt.Axis(title="乗客")),
        color=alt.Color("category:N",
                        scale=alt.Scale(domain=list(palette), range=list(palette.values())),
                        legend=alt.Legend(title="状態", orient="bottom")),
        tooltip=[alt.Tooltip("passenger:N", title="乗客"),
                 alt.Tooltip("place:N", title="現在地"),
                 alt.Tooltip("start_h:Q", title="開始 h"),
                 alt.Tooltip("end_h:Q", title="終了 h")],
    ).properties(height=alt.Step(22), title="乗客別タイムライン（ガントチャート）")


def tab_animation():
    import datetime as _dt

    st.subheader("移動可視化（タイムスライダー）")
    st.caption(
        "Run タブで解いた結果をもとに、各島・拠点を人が移動し fleet が載せていく様子を、"
        "ルート構造のサイクル図上で時刻を進めながら確認できます。"
        "スライダーを動かすと、各拠点の在籍人数（🧽）と、区間を移動中の人数"
        "（🚶＝徒歩、🚐＝fleet 便）がリアルタイムに更新されます。"
    )

    anim = st.session_state.get("anim")
    if not anim:
        st.info(
            "まだ解がありません。**Run** タブで「Solve (single, flow)」または "
            "「Solve (rolling)」を実行すると、その結果がここで可視化できるようになります。"
        )
        return

    snap, segs = anim["snap"], anim["segs"]
    H = max(int(snap["H"]), 1)
    try:
        start = _dt.datetime.fromisoformat(snap["start"])
    except (ValueError, TypeError):
        start = None

    src = anim.get("source", "")
    st.caption(f"対象: {src}（horizon {H}h ＝ {H / 24:.1f}日 / 乗客 {len(segs)} 名）")

    st.session_state.setdefault("anim_t", 0)
    st.session_state.setdefault("anim_playing", False)
    playing = bool(st.session_state["anim_playing"])

    # --- 再生コントロール（フラグメント外。変更時はフル再実行で間隔を再評価）---
    c = st.columns([1.3, 2.2, 2, 1.2])
    if c[0].button("⏸ 一時停止" if playing else "▶ 再生", width="stretch",
                   help="自動再生の開始 / 停止。"):
        st.session_state["anim_playing"] = not playing
        st.rerun()
    # 再生ペース = 1 コマを表示する秒数（小さいほど速い）。
    pace_opts = {"遅い（1.5s/コマ）": 1.5, "標準（0.7s/コマ）": 0.7,
                 "速い（0.3s/コマ）": 0.3, "最速（0.1s/コマ）": 0.1}
    pace_label = c[1].selectbox("再生ペース", list(pace_opts), index=1, key="anim_pace",
                                help="1 コマを何秒表示するか。小さいほど速く再生します。")
    interval = pace_opts[pace_label]
    step = int(c[2].selectbox("1 コマの進み幅", [1, 2, 3, 6, 12, 24], index=0, key="anim_step",
                              format_func=lambda h: f"{h}h",
                              help="再生 1 コマで進める時間。大きいほど飛ばし見になります。"))
    loop = c[3].checkbox("ループ", value=True, key="anim_loop",
                         help="末尾まで来たら先頭へ戻って繰り返します。")

    # 再生中のみ run_every を設定 → そのときだけフラグメントが自動再実行される。
    run_every = interval if playing else None

    @st.fragment(run_every=run_every)
    def _player():
        # 自動再実行（再生中）でコマを進める。末尾でループ or 停止。
        if st.session_state["anim_playing"]:
            nt = st.session_state["anim_t"] + step
            if nt > H:
                if loop:
                    nt = 0
                else:
                    nt = H
                    st.session_state["anim_playing"] = False
                    st.session_state["anim_t"] = nt
                    st.rerun(scope="app")   # タイマーを止めるためフル再実行
            st.session_state["anim_t"] = nt

        # スライダーは key を持たせず value=現在時刻に追従させる（ドラッグで手動シーク可）。
        t = int(st.session_state["anim_t"])
        new_t = st.slider("時刻 t（計画開始からの経過時間 h）", 0, H, t, step=1,
                          help="0 = 計画開始時点。ドラッグで任意の時刻へ移動できます。")
        if new_t != t:
            st.session_state["anim_t"] = new_t
            t = new_t

        cur = ""
        if start is not None:
            cur = "　" + (start + _dt.timedelta(hours=t)).strftime("%Y-%m-%d %H:%M")
        bar = "▶ 再生中" if st.session_state["anim_playing"] else "⏸ 停止中"
        st.markdown(f"**{bar}　経過 {t}h（{t // 24}日 {t % 24}h 目）{cur}**")

        node_members, edge_members = anim_mod.positions_at(segs, t)

        left, right = st.columns([6, 5], gap="large")
        with left:
            mermaid_click(anim_mod.anim_mermaid(snap, node_members, edge_members),
                          key="anim_graph")
            moving = sum(len(v) for v in edge_members.values())
            resting = len(node_members.get("Await", []))
            st.caption(f"この時刻: 移動中 {moving} 名 ／ A 待機 {resting} 名")

        with right:
            st.markdown("**この時刻の各乗客の居場所**")
            place_of: dict[str, str] = {}
            for tok, pids in {**node_members, **edge_members}.items():
                for pid in pids:
                    place_of[pid] = anim_mod.place_label(snap, tok)
            rows = [{"乗客": pid, "現在地": place_of.get(pid, "A 待機")}
                    for pid in sorted(place_of)]
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=420)

        # 各拠点の滞在人数を棒グラフでも示す（スライダー / 再生に追従して更新）。
        # Altair はクライアント側描画なので、PNG と違い再生・シークに滑らかに追従する。
        st.altair_chart(_occupancy_bar_chart(snap, node_members, len(segs)),
                        use_container_width=True)

    _player()

    # 時刻スライダーとは別に、全期間を俯瞰するガントチャート（縦軸＝乗客 / 横軸＝時間）。
    # 静的なので再生フラグメントの外に置き、毎フレームの再計算を避ける。
    st.divider()
    st.markdown("**乗客別タイムライン（ガントチャート）**")
    st.caption(
        "縦軸が乗客、横軸が時間です。各乗客が全期間を通じてどこに居て、いつ移動したかを"
        "状態別の色（A 待機 / 島 滞在 / D 滞在 / fleet 便 🚐 / 徒歩移動 🚶）で俯瞰できます。"
    )
    st.altair_chart(_gantt_chart(snap, segs), use_container_width=True)


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Instance Editor", layout="wide")
    st.title("Fixed-Route Rotation — Instance Editor")
    get_doc()  # ensure init
    tabs = st.tabs(["Load/New", "General", "Vehicles & Fleet", "Sites",
                    "Passengers", "Validate & Save", "Run", "移動可視化"])
    with tabs[0]:
        tab_load()
    with tabs[1]:
        tab_general()
    with tabs[2]:
        tab_vehicles()
    with tabs[3]:
        tab_sites()
    with tabs[4]:
        tab_passengers()
    with tabs[5]:
        tab_save()
    with tabs[6]:
        tab_run()
    with tabs[7]:
        tab_animation()


main()
