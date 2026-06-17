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

import matplotlib.pyplot as plt  # noqa: E402

# matplotlib 図中の日本語が文字化け（豆腐 □）するのを防ぐ。
# japanize_matplotlib を import するだけで rcParams のフォントが日本語対応になる。
try:
    import japanize_matplotlib  # noqa: F401,E402
except Exception:  # 未インストール環境でもアプリ自体は起動できるようにする
    pass

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from route_opt import gui_io  # noqa: E402
from route_opt.bench import make_instance  # noqa: E402
from route_opt.loader import load_instance  # noqa: E402
from route_opt.model import FullModel  # noqa: E402
from route_opt.report import plot_gantt, trips_df, write_csv  # noqa: E402
from route_opt.rolling import solve_rolling  # noqa: E402

INSTANCES = pathlib.Path("instances")
OUTDIR = pathlib.Path("out")


# --------------------------------------------------------------------------
def get_doc() -> dict:
    return st.session_state.setdefault("doc", _default_doc())


def set_doc(doc: dict) -> None:
    st.session_state["doc"] = doc


def _default_doc() -> dict:
    f = INSTANCES / "full_cd.yaml"
    if f.exists():
        return gui_io.doc_from_instance(load_instance(f))
    return gui_io.doc_from_instance(
        make_instance(days=5, islands=1, workers_per_island=2, vans=1, trucks=0,
                      M=4, J=8, JCD=6, max_seconds=20)
    )


def _editor(rows: list[dict], cols, key: str) -> list[dict]:
    df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
    edited = st.data_editor(df, num_rows="dynamic", width="stretch", key=key)
    return edited.to_dict("records")


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
        "**cost_per_hour**=自社車の時間コスト、**rental_cost_per_hour**=レンタル時の時間コスト。"
    )
    rows = _editor(gui_io.vehicle_types_to_rows(doc["vehicle_types"]),
                   ["name", "capacity", "cost_per_hour", "rental_cost_per_hour"], "vt")
    try:
        doc["vehicle_types"] = gui_io.rows_to_vehicle_types(rows)
    except (ValueError, KeyError, TypeError):
        st.warning("vehicle_types の数値を確認してください")

    st.subheader("Fleet — owned")
    st.caption(
        "自社保有車両の一覧です。"
        "列: **id**=車両固有 ID、**type**=上で定義した vehicle_types の名前、"
        "**initial_location**=計画開始時の初期位置（例: A）。"
    )
    fleet = doc.setdefault("fleet", {"owned": [], "rental": {}})
    orows = _editor(fleet.get("owned", []), ["id", "type", "initial_location"], "owned")
    fleet["owned"] = [
        {"id": str(r.get("id", "")).strip(), "type": str(r.get("type", "")).strip(),
         "initial_location": str(r.get("initial_location") or "A").strip()}
        for r in orows if str(r.get("id", "")).strip()
    ]
    st.subheader("Fleet — rental")
    st.caption("レンタル車両の利用設定です。自社車が不足する場合に必要に応じて調達されます。")
    rt = fleet.setdefault("rental", {})
    c = st.columns(3)
    rt["enabled"] = c[0].checkbox("enabled", bool(rt.get("enabled", True)),
                                  help="レンタル車両の利用を許可するかどうか。")
    rt["initial_location"] = c[1].text_input("initial_location", rt.get("initial_location", "A"),
                                             help="レンタル車両の受け取り・返却拠点（例: A）。")
    rt["max_per_type"] = int(c[2].number_input("max_per_type", 0, 50,
                                               int(rt.get("max_per_type", 0)),
                                               help="タイプごとに同時にレンタルできる最大台数。0 は無制限。"))


def tab_sites():
    doc = get_doc()
    st.subheader("Staffed sites (B islands)")
    st.caption(
        "有人配置が必要な B 島サイトを管理します。"
        "サイトごとに必要人数・滞在時間・移動時間・カテゴリ要件などを設定できます。"
    )
    sites = doc.setdefault("staffed_sites", {})

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
        with st.expander(f"site: {name}", expanded=False):
            if st.button(f"削除 {name}", key=f"del_{name}"):
                del sites[name]
                st.rerun()
            c = st.columns(4)
            s["occupancy_min"] = int(c[0].number_input("occupancy_min", 0, 50,
                                     int(s.get("occupancy_min", 0)), key=f"occ_{name}",
                                     help="このサイトに常に駐在していなければならない最低人数。"))
            s.setdefault("stay", {})
            s["stay"]["min_hours"] = int(c[1].number_input("stay.min_hours", 0, 1000,
                                          int(s["stay"].get("min_hours", 24)), key=f"smin_{name}",
                                          help="作業員がこのサイトに滞在しなければならない最短時間（時間）。"))
            s["stay"]["max_hours"] = int(c[2].number_input("stay.max_hours", 0, 1000,
                                          int(s["stay"].get("max_hours", 48)), key=f"smax_{name}",
                                          help="作業員がこのサイトに滞在できる最長時間（時間）。"))
            s["replacement_required"] = c[3].checkbox("replacement_required",
                                          bool(s.get("replacement_required", True)), key=f"rep_{name}",
                                          help="ON: 現在の駐在員が帰る前に交代要員の到着が必須。OFF: 空席を許容。")
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
    st.subheader("CD-arm (A→C→D→C→A)")
    st.caption(
        "A 拠点から中継地 C を経て一時サイト D に立ち寄る特殊ルートです。"
        "CD-arm を使う場合は有効化し、各区間の移動時間を設定してください。"
    )
    has_cd = st.checkbox("CD-arm を有効化", doc.get("cd_arm") is not None,
                         help="このルートを使用する場合にチェックを入れてください。")
    if has_cd:
        cd = doc.get("cd_arm") or {"a_c_hours": 3, "c_d_hours": 1, "d_c_hours": 1, "c_a_hours": 3}
        cc = st.columns(4)
        labels = {
            "a_c_hours": ("a_c_hours", "A→C の移動時間（時間）"),
            "c_d_hours": ("c_d_hours", "C→D の移動時間（時間）"),
            "d_c_hours": ("d_c_hours", "D→C の移動時間（時間）"),
            "c_a_hours": ("c_a_hours", "C→A の移動時間（時間）"),
        }
        for i, k in enumerate(["a_c_hours", "c_d_hours", "d_c_hours", "c_a_hours"]):
            cd[k] = int(cc[i].number_input(k, 0, 100, int(cd.get(k, 1)), key=f"cd_{k}",
                                           help=labels[k][1]))
        doc["cd_arm"] = cd
    else:
        doc["cd_arm"] = None

    st.subheader("Temporary site D")
    st.caption(
        "CD-arm の終点となる一時滞在サイトです。"
        "d_stay_table で「同時滞在人数 n → 必要滞在時間」を設定します。"
    )
    has_d = st.checkbox("D を有効化", doc.get("temporary_site") is not None,
                        help="一時サイト D を使用する場合にチェックを入れてください。")
    if has_d:
        d = doc.get("temporary_site") or {"d_stay_table": {1: 12}, "occupancy_max": None}
        st.caption("d_stay_table: 同時滞在人数 n に対して必要な最低滞在時間（hours）を指定します。")
        rows = _editor(gui_io.intmap_to_rows(d.get("d_stay_table", {}), "n", "hours"),
                       ["n", "hours"], "dtable")
        d["d_stay_table"] = gui_io.rows_to_intmap(rows, "n", "hours", int_key=True)
        cap = st.text_input("occupancy_max（空欄=無制限）",
                            "" if d.get("occupancy_max") is None else str(d["occupancy_max"]),
                            help="D サイトに同時に滞在できる最大人数。空欄にすると上限なし。")
        d["occupancy_max"] = int(cap) if cap.strip() else None
        doc["temporary_site"] = d
    else:
        doc["temporary_site"] = None


def tab_passengers():
    doc = get_doc()
    st.subheader("Passengers")
    st.caption(
        "乗客（作業員）のマスタです。"
        "列: **id**=乗客固有 ID、**category**=乗客カテゴリ（スキル区分など）。"
    )
    prows = _editor(doc.get("passengers", []), ["id", "category"], "pax")
    doc["passengers"] = [
        {"id": str(r.get("id", "")).strip(), "category": str(r.get("category", "")).strip()}
        for r in prows if str(r.get("id", "")).strip()
    ]

    st.subheader("Passenger rules (allowed B sites; カンマ区切り)")
    st.caption(
        "各乗客が赴任可能な B 島サイトを制限します。"
        "列: **passenger**=乗客 ID、**allowed_sites**=赴任できるサイト名（カンマ区切り）。"
        "指定のない乗客はすべてのサイトに赴任可能です。"
    )
    rules = doc.get("passenger_rules", {})
    rrows = _editor(gui_io.passenger_rules_to_rows(rules),
                    ["passenger", "allowed_sites"], "rules")
    doc["passenger_rules"] = gui_io.rows_to_passenger_rules(rrows)

    st.subheader("Initial state")
    st.caption(
        "計画開始時点での各乗客の状態を設定します。"
        "**location**: A（本拠点）/ B 島名 / D（一時サイト）。"
        "**arrived_at**: 現地への到着日時（ISO 8601）。空欄の場合は計画開始時刻とみなします。"
    )
    irows = []
    for s in doc.get("initial_state", []):
        irows.append({"passenger_id": s.get("passenger_id"),
                      "location": s.get("location"),
                      "arrived_at": s.get("arrived_at") or ""})
    irows = _editor(irows, ["passenger_id", "location", "arrived_at"], "init")
    out = []
    for r in irows:
        pid = str(r.get("passenger_id", "")).strip()
        if not pid:
            continue
        rec = {"passenger_id": pid, "location": str(r.get("location") or "A").strip()}
        at = str(r.get("arrived_at", "")).strip()
        if at:
            rec["arrived_at"] = at
        out.append(rec)
    doc["initial_state"] = out


def tab_solver():
    doc = get_doc()
    st.subheader("Solver params")
    st.caption(
        "MIP ソルバーの挙動を制御するパラメータです。"
        "値を大きくするとモデルが複雑になり求解時間が増えます。小さくすると近似解になる可能性があります。"
    )
    sp = doc.setdefault("solver", {})
    c = st.columns(3)
    sp["max_visits_per_passenger"] = int(c[0].number_input("max_visits_per_passenger", 1, 60,
                                         int(sp.get("max_visits_per_passenger", 4)),
                                         help="1 人の乗客が計画期間中に B 島を訪問できる最大回数。"
                                              "大きいほど柔軟なスケジュールになりますが変数が増えます。"))
    sp["trips_per_site"] = int(c[1].number_input("trips_per_site", 1, 300,
                               int(sp.get("trips_per_site", 8)),
                               help="各 B 島サイトへのトリップ数の上界。"
                                    "計画期間÷平均滞在日数 程度を目安にしてください。"))
    sp["trips_cd"] = int(c[2].number_input("trips_cd", 1, 300, int(sp.get("trips_cd", 8)),
                                           help="CD-arm（A→C→D→C→A）のトリップ数の上界。"
                                                "CD-arm を使わない場合は無視されます。"))
    c2 = st.columns(2)
    sp["max_seconds"] = float(c2[0].number_input("max_seconds", 1.0, 600.0,
                              float(sp.get("max_seconds", 30.0)),
                              help="ソルバーの最大実行時間（秒）。時間内に最適解が見つからない場合は暫定解を返します。"))
    cm = c2[1].text_input("commit_hours（空欄=単一ウィンドウ）",
                          "" if sp.get("commit_hours") is None else str(sp["commit_hours"]),
                          help="ローリングホライズン使用時のコミット幅（時間）。"
                               "空欄にすると計画期間全体を一括で解く単一ウィンドウモードになります。")
    sp["commit_hours"] = int(cm) if cm.strip() else None


def _validated_instance():
    return gui_io.instance_from_doc(get_doc())


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
            (INSTANCES / name).write_text(gui_io.yaml_from_doc(doc))
            st.success(f"保存しました: instances/{name}")
    st.download_button("Download YAML", gui_io.yaml_from_doc(doc),
                       file_name="instance.yaml", mime="text/yaml",
                       help="現在の設定を YAML ファイルとしてローカルにダウンロードします。")
    with st.expander("YAML プレビュー"):
        st.caption("現在編集中のインスタンスを YAML 形式で確認できます。")
        st.code(gui_io.yaml_from_doc(doc), language="yaml")


def tab_run():
    st.subheader("Run solver")
    st.caption(
        "現在の設定でソルバーを実行します。"
        "**Single window**: 計画期間全体を一括で最適化します（小規模向け）。"
        "**Rolling horizon**: 期間をウィンドウで区切って逐次最適化します（大規模・長期向け）。"
    )
    try:
        inst = _validated_instance()
    except Exception as e:  # noqa: BLE001
        st.error("先に検証を通してください:")
        st.code(str(e))
        return

    mode = st.radio("モード", ["Single window", "Rolling horizon"], horizontal=True,
                    help="Single window: 全期間を一括求解。Rolling horizon: ウィンドウを滑らせて逐次求解。")
    if mode == "Single window":
        if st.button("Solve (single)"):
            with st.spinner("solving..."):
                sol = FullModel(inst).solve()
            st.code(sol.summary())
    else:
        c = st.columns(2)
        wd = c[0].number_input("window_days (lookahead)", 1.0, 30.0, 6.0,
                               help="各ウィンドウで先読みする日数。大きいほど精度は上がりますが求解時間が増えます。")
        sd = c[1].number_input("step_days (commit)", 1.0, 30.0, 5.0,
                               help="各ウィンドウで確定（コミット）する日数。window_days 以下にしてください。")
        if st.button("Solve (rolling)"):
            with st.spinner("rolling solve..."):
                r = solve_rolling(inst, window_days=float(wd), step_days=float(sd),
                                  verbose=False)
            if not r.ok:
                st.error(f"FAILED: {r.message}")
                return
            st.success(f"OK  total_cost={r.total_cost:.0f}  "
                       f"windows={len(r.windows)}  CD_trips={sum(w['cd_trips'] for w in r.windows)}")
            st.dataframe(pd.DataFrame(r.windows), width="stretch")
            OUTDIR.mkdir(exist_ok=True)
            paths = write_csv(r, inst, OUTDIR)
            png = plot_gantt(r, inst, OUTDIR / "schedule_gantt.png")
            st.image(str(png), caption="Schedule Gantt")
            st.dataframe(trips_df(r).head(50), width="stretch")
            d = st.columns(2)
            d[0].download_button("trips.csv", paths["trips"].read_bytes(),
                                 file_name="schedule_trips.csv",
                                 help="各トリップの詳細（出発・到着時刻、乗客など）を含む CSV。")
            d[1].download_button("stays.csv", paths["stays"].read_bytes(),
                                 file_name="schedule_stays.csv",
                                 help="各乗客のサイト滞在期間を含む CSV。")


# --------------------------------------------------------------------------
# 配色（拠点種別ごと）
_COL_A = "#4C72B0"  # A 本拠点
_COL_B = "#55A868"  # B 有人サイト
_COL_C = "#C44E52"  # C 中継点
_COL_D = "#DD8452"  # D 一時サイト


def _route_figure(doc: dict) -> plt.Figure:
    """固定ルート A→B→A→C→D→C→A を巡回順序つきの 1 枚に統合して返す。

    1 本の背骨（spine）に巡回順序 ① → ② → … と各区間の移動時間を載せ、
    B ステップは「複数島のいずれか 1 つを選ぶ」ことを分岐（fan）で示す。
    各島には往復時間と滞在レンジ、D には滞在レンジを併記する。
    """
    sites = doc.get("staffed_sites", {})
    cd = doc.get("cd_arm")
    tmp = doc.get("temporary_site")

    # --- 固定順序で背骨ノードと区間を組み立てる ---
    # nodes: (ラベル, 色, 補足文), legs: 区間ラベル（len == len(nodes) - 1）
    nodes: list[tuple[str, str, str]] = [("A", _COL_A, "本拠点")]
    legs: list[str] = []
    b_idx: int | None = None  # 背骨上の B スロットの位置

    if sites:
        b_idx = len(nodes)
        nodes.append(("B", _COL_B, "いずれか1島"))
        nodes.append(("A", _COL_A, "立寄"))
        legs += ["往路", "復路"]

    if cd is not None:
        nodes.append(("C", _COL_C, "中継点"))
        legs.append(f"{cd.get('a_c_hours', '?')}h")
        if tmp is not None:
            table = tmp.get("d_stay_table", {})
            d_sub = "一時サイト"
            if table:
                vals = list(table.values())
                d_sub = f"滞在 {min(vals)}〜{max(vals)}h"
            nodes.append(("D", _COL_D, d_sub))
            nodes.append(("C", _COL_C, "中継点"))
            legs += [f"{cd.get('c_d_hours', '?')}h", f"{cd.get('d_c_hours', '?')}h"]
        legs.append(f"{cd.get('c_a_hours', '?')}h")
        nodes.append(("A", _COL_A, "帰還"))

    # --- 島分岐（fan）の位置決め ---
    site_names = list(sites.keys())
    n_isl = len(site_names)
    dx = 1.7
    xs = [i * dx for i in range(len(nodes))]
    isl_y0, isl_dy = 1.4, 1.05
    top = isl_y0 + max(n_isl - 1, 0) * isl_dy + 0.7 if n_isl else 1.1

    fig, ax = plt.subplots(figsize=(max(9, len(nodes) * dx + 2.5),
                                    max(3.6, 2.4 + n_isl * 0.95)))
    ax.set_facecolor("#F8F8F8")
    fig.patch.set_facecolor("#F8F8F8")
    ax.set_xlim(-0.8, xs[-1] + 0.8)
    ax.set_ylim(-1.05, top)
    ax.axis("off")

    # 背骨：区間矢印 + ステップ番号 + 区間ラベル
    for i, leg in enumerate(legs):
        x0, x1 = xs[i], xs[i + 1]
        ax.annotate("", xy=(x1 - 0.32, 0), xytext=(x0 + 0.32, 0),
                    arrowprops={"arrowstyle": "-|>", "color": "#555", "lw": 2.2},
                    zorder=2)
        xm = (x0 + x1) / 2
        ax.scatter([xm], [0.42], s=340, color="white", edgecolors="#555",
                   linewidths=1.6, zorder=4)
        ax.text(xm, 0.42, f"{i + 1}", ha="center", va="center", fontsize=9.5,
                fontweight="bold", color="#333", zorder=5)
        ax.text(xm, -0.16, leg, ha="center", va="top", fontsize=8, color="#222",
                zorder=5)

    # 背骨：拠点ノード
    for x, (label, color, sub) in zip(xs, nodes):
        ax.scatter([x], [0], s=1700, color=color, edgecolors="white",
                   linewidths=1.6, zorder=3)
        ax.text(x, 0, label, ha="center", va="center", color="white",
                fontsize=12, fontweight="bold", zorder=4)
        if sub:
            ax.text(x, -0.46, sub, ha="center", va="top", fontsize=8,
                    color="#333", zorder=4)

    # B スロットから各島への分岐（「いずれか 1 島を選ぶ」）
    if b_idx is not None and n_isl:
        bx = xs[b_idx]
        trunk_top = isl_y0 + (n_isl - 1) * isl_dy
        ax.plot([bx, bx], [0.34, trunk_top], ls=":", color=_COL_B, lw=1.4, zorder=1)
        isl_x = bx + 0.55
        for i, name in enumerate(site_names):
            y = isl_y0 + i * isl_dy
            s = sites[name]
            seg = s.get("segments", {})
            inb = seg.get("inbound_hours", "?")
            out = seg.get("outbound_hours", "?")
            stay = s.get("stay", {})
            lo = stay.get("min_hours", 0)
            hi = stay.get("max_hours", lo)
            ax.plot([bx, isl_x - 0.2], [y, y], ls=":", color=_COL_B, lw=1.4, zorder=1)
            ax.scatter([isl_x], [y], s=900, color=_COL_B, edgecolors="white",
                       linewidths=1.4, zorder=3)
            ax.text(isl_x, y, name, ha="center", va="center", color="white",
                    fontsize=9, fontweight="bold", zorder=4)
            ax.text(isl_x + 0.45, y, f"往 {inb}h ／ 復 {out}h ／ 滞在 {lo}〜{hi}h",
                    ha="left", va="center", fontsize=8, color="#333", zorder=4)

    # 凡例
    legend_items = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=_COL_A,
                   markersize=12, label="A 本拠点"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=_COL_B,
                   markersize=12, label="B 有人サイト（いずれか1島）"),
    ]
    if cd is not None:
        legend_items.append(plt.Line2D([0], [0], marker="o", color="w",
                                       markerfacecolor=_COL_C, markersize=12, label="C 中継点"))
    if tmp is not None:
        legend_items.append(plt.Line2D([0], [0], marker="o", color="w",
                                       markerfacecolor=_COL_D, markersize=12, label="D 一時サイト"))
    ax.legend(handles=legend_items, loc="upper right", fontsize=8.5, framealpha=0.85)
    ax.set_title("固定ルート ① → ② → … の順に巡回（数値は片道移動時間）",
                 fontsize=12, pad=10)
    fig.tight_layout()
    return fig


def _stay_figure(doc: dict) -> plt.Figure | None:
    """滞在時間レンジの横棒チャートを返す。サイトがなければ None。"""
    sites = doc.get("staffed_sites", {})
    tmp = doc.get("temporary_site")

    labels, mins, ranges = [], [], []
    for name, s in sites.items():
        stay = s.get("stay", {})
        lo = stay.get("min_hours", 0)
        hi = stay.get("max_hours", lo)
        labels.append(name)
        mins.append(lo)
        ranges.append(max(hi - lo, 0))

    if tmp is not None:
        table = tmp.get("d_stay_table", {})
        if table:
            max_hours = max(table.values())
            labels.append("D（一時サイト）")
            mins.append(0)
            ranges.append(max_hours)

    if not labels:
        return None

    fig, ax = plt.subplots(figsize=(7, max(2.5, len(labels) * 0.7 + 1.0)))
    y = range(len(labels))
    ax.barh(list(y), mins, color="none")  # invisible offset
    ax.barh(list(y), ranges, left=mins, color="#4C72B0", alpha=0.75, label="滞在可能レンジ")
    ax.scatter(mins, list(y), color="#2b5ea7", zorder=5, s=60, label="min")
    max_vals = [m + r for m, r in zip(mins, ranges)]
    ax.scatter(max_vals, list(y), color="#C44E52", marker="|", zorder=5, s=120, label="max")

    for i, (lo, hi) in enumerate(zip(mins, max_vals)):
        ax.text(lo, i, f" {lo}h", va="center", ha="left", fontsize=8, color="#2b5ea7")
        ax.text(hi, i, f" {hi}h", va="center", ha="left", fontsize=8, color="#C44E52")

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.set_xlabel("時間 (hours)")
    ax.set_title("滞在時間レンジ（min ～ max）", fontsize=11)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    return fig


def _staffing_table(doc: dict) -> pd.DataFrame | None:
    """人員充足制約テーブルを DataFrame で返す。"""
    sites = doc.get("staffed_sites", {})
    if not sites:
        return None

    all_cats: list[str] = sorted({
        cat
        for s in sites.values()
        for cat in s.get("category_requirements", {}).keys()
    })

    rows = []
    for name, s in sites.items():
        row: dict = {"サイト": name,
                     "occupancy_min": s.get("occupancy_min", 0),
                     "交代必須": "✓" if s.get("replacement_required", False) else ""}
        for cat in all_cats:
            row[cat] = s.get("category_requirements", {}).get(cat, 0)
        rows.append(row)
    return pd.DataFrame(rows)


def _category_figure(doc: dict) -> plt.Figure | None:
    """乗客カテゴリ分布の棒グラフを返す。乗客がいなければ None。"""
    passengers = doc.get("passengers", [])
    if not passengers:
        return None

    from collections import Counter
    counts = Counter(p.get("category", "?") for p in passengers)
    cats = sorted(counts.keys())
    vals = [counts[c] for c in cats]

    fig, ax = plt.subplots(figsize=(max(4, len(cats) * 0.9 + 1.5), 3.5))
    bars = ax.bar(cats, vals, color="#55A868", alpha=0.85, width=0.6)
    ax.bar_label(bars, padding=3, fontsize=10)
    ax.set_xlabel("カテゴリ")
    ax.set_ylabel("人数")
    ax.set_title("乗客カテゴリ分布", fontsize=11)
    ax.set_ylim(0, max(vals) * 1.25)
    fig.tight_layout()
    return fig


def tab_visualize():
    doc = get_doc()
    st.subheader("制約可視化")
    st.caption("現在の設定から経路構造・時間制約・人員制約を図示します。Sites タブや Passengers タブを変更するとリアルタイムで更新されます。")

    sites = doc.get("staffed_sites", {})

    # 1. ルート経路図（固定順序つき）
    st.markdown("#### ルート経路図（巡回順序つき）")
    if not sites and doc.get("cd_arm") is None:
        st.info("サイトが未定義です。Sites タブで B 島を追加してください。")
    else:
        st.pyplot(_route_figure(doc))
        st.caption(
            "経路は **A（本拠点）→ B（複数島のいずれか1島）→ A → C → D → C → A（帰還）** に固定されています。"
            "矢印上の ① → ② → … が巡回順序、数値は各区間の片道移動時間です。"
            "B では複数の島から 1 つを選びます（点線の分岐）。"
            "CD アームは必ず C を経由して D に到達します（A→D 直行は不可）。"
        )

    st.divider()

    # 2. 滞在時間レンジ
    st.markdown("#### 滞在時間レンジ")
    stay_fig = _stay_figure(doc)
    if stay_fig is None:
        st.info("サイトが未定義のため表示できません。")
    else:
        st.pyplot(stay_fig)

    st.divider()

    # 3. 人員充足制約テーブル
    st.markdown("#### 人員充足制約")
    df = _staffing_table(doc)
    if df is None:
        st.info("サイトが未定義のため表示できません。")
    else:
        st.dataframe(df, hide_index=True, use_container_width=True)
        st.caption("各列はカテゴリ別の最低必要人数。occupancy_min はサイト全体の最低駐在人数。")

    st.divider()

    # 4. 乗客カテゴリ分布
    st.markdown("#### 乗客カテゴリ分布")
    cat_fig = _category_figure(doc)
    if cat_fig is None:
        st.info("乗客が未定義のため表示できません。Passengers タブで追加してください。")
    else:
        st.pyplot(cat_fig)
        rules = doc.get("passenger_rules", {})
        if rules:
            st.caption(f"赴任制約あり乗客: {len(rules)} 名（Passengers タブの Passenger rules を参照）")


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Instance Editor", layout="wide")
    st.title("Fixed-Route Rotation — Instance Editor")
    get_doc()  # ensure init
    tabs = st.tabs(["Load/New", "General", "Vehicles & Fleet", "Sites",
                    "Passengers", "Solver", "Validate & Save", "Run", "制約可視化"])
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
        tab_solver()
    with tabs[6]:
        tab_save()
    with tabs[7]:
        tab_run()
    with tabs[8]:
        tab_visualize()


main()
