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
    files = sorted(p.name for p in INSTANCES.glob("*.yaml")) if INSTANCES.exists() else []
    c1, c2 = st.columns([3, 1])
    pick = c1.selectbox("instances/ から読み込み", files or ["(なし)"])
    if c2.button("Load", disabled=not files):
        set_doc(gui_io.doc_from_instance(load_instance(INSTANCES / pick)))
        st.success(f"loaded {pick}")

    up = st.file_uploader("YAML をアップロード", type=["yaml", "yml"])
    if up is not None and st.button("Use uploaded"):
        set_doc(gui_io.doc_from_yaml(up.getvalue().decode("utf-8")))
        st.success("uploaded YAML を読み込みました")

    st.divider()
    st.markdown("**サンプル生成器**（雛形インスタンスを生成して編集開始）")
    g = st.columns(5)
    days = g[0].number_input("days", 1, 60, 14)
    islands = g[1].number_input("islands", 1, 5, 2)
    workers = g[2].number_input("workers/island", 1, 20, 4)
    vans = g[3].number_input("minivans", 0, 10, 2)
    trucks = g[4].number_input("trucks", 0, 10, 1)
    if st.button("サンプル生成"):
        inst = make_instance(days=int(days), islands=int(islands),
                             workers_per_island=int(workers), vans=int(vans),
                             trucks=int(trucks), M=5, J=99, JCD=99, max_seconds=30)
        set_doc(gui_io.doc_from_instance(inst))
        st.success("サンプルを生成しました")


def tab_general():
    doc = get_doc()
    st.subheader("General")
    ph = doc["planning_horizon"]
    ph["start"] = st.text_input("planning_horizon.start (ISO 8601)", ph["start"])
    ph["end"] = st.text_input("planning_horizon.end (ISO 8601)", ph["end"])
    st.caption(f"time_granularity_hours = {doc.get('time_granularity_hours', 1)}（1h 固定）")
    st.markdown("**Calendar — holidays（YYYY-MM-DD）**")
    rows = [{"date": d} for d in doc.get("calendar", {}).get("holidays", [])]
    rows = _editor(rows, ["date"], "holidays")
    doc.setdefault("calendar", {})["holidays"] = [
        str(r["date"]).strip() for r in rows if str(r.get("date", "")).strip()
    ]


def tab_vehicles():
    doc = get_doc()
    st.subheader("Vehicle types")
    rows = _editor(gui_io.vehicle_types_to_rows(doc["vehicle_types"]),
                   ["name", "capacity", "cost_per_hour", "rental_cost_per_hour"], "vt")
    try:
        doc["vehicle_types"] = gui_io.rows_to_vehicle_types(rows)
    except (ValueError, KeyError, TypeError):
        st.warning("vehicle_types の数値を確認してください")

    st.subheader("Fleet — owned")
    fleet = doc.setdefault("fleet", {"owned": [], "rental": {}})
    orows = _editor(fleet.get("owned", []), ["id", "type", "initial_location"], "owned")
    fleet["owned"] = [
        {"id": str(r.get("id", "")).strip(), "type": str(r.get("type", "")).strip(),
         "initial_location": str(r.get("initial_location") or "A").strip()}
        for r in orows if str(r.get("id", "")).strip()
    ]
    st.subheader("Fleet — rental")
    rt = fleet.setdefault("rental", {})
    c = st.columns(3)
    rt["enabled"] = c[0].checkbox("enabled", bool(rt.get("enabled", True)))
    rt["initial_location"] = c[1].text_input("initial_location", rt.get("initial_location", "A"))
    rt["max_per_type"] = int(c[2].number_input("max_per_type", 0, 50,
                                               int(rt.get("max_per_type", 0))))


def tab_sites():
    doc = get_doc()
    st.subheader("Staffed sites (B islands)")
    sites = doc.setdefault("staffed_sites", {})

    add = st.columns([3, 1])
    new_name = add[0].text_input("新規サイト名", key="new_site")
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
                                     int(s.get("occupancy_min", 0)), key=f"occ_{name}"))
            s.setdefault("stay", {})
            s["stay"]["min_hours"] = int(c[1].number_input("stay.min_hours", 0, 1000,
                                          int(s["stay"].get("min_hours", 24)), key=f"smin_{name}"))
            s["stay"]["max_hours"] = int(c[2].number_input("stay.max_hours", 0, 1000,
                                          int(s["stay"].get("max_hours", 48)), key=f"smax_{name}"))
            s["replacement_required"] = c[3].checkbox("replacement_required",
                                          bool(s.get("replacement_required", True)), key=f"rep_{name}")
            sc = st.columns(2)
            s.setdefault("segments", {})
            s["segments"]["inbound_hours"] = int(sc[0].number_input("segments.inbound_hours", 0, 100,
                                              int(s["segments"].get("inbound_hours", 2)), key=f"in_{name}"))
            s["segments"]["outbound_hours"] = int(sc[1].number_input("segments.outbound_hours", 0, 100,
                                              int(s["segments"].get("outbound_hours", 2)), key=f"out_{name}"))
            st.markdown("category_requirements (category → min)")
            rows = _editor(gui_io.intmap_to_rows(s.get("category_requirements", {}), "category", "min"),
                           ["category", "min"], f"catreq_{name}")
            s["category_requirements"] = gui_io.rows_to_intmap(rows, "category", "min")
            s["ride_together"] = gui_io.str_to_ride_together(
                st.text_input("ride_together（例: Category1,Category2; Category3,Category4）",
                              gui_io.ride_together_to_str(s.get("ride_together", [])), key=f"rt_{name}")
            )

    st.divider()
    st.subheader("CD-arm (A→C→D→C→A)")
    has_cd = st.checkbox("CD-arm を有効化", doc.get("cd_arm") is not None)
    if has_cd:
        cd = doc.get("cd_arm") or {"a_c_hours": 3, "c_d_hours": 1, "d_c_hours": 1, "c_a_hours": 3}
        cc = st.columns(4)
        for i, k in enumerate(["a_c_hours", "c_d_hours", "d_c_hours", "c_a_hours"]):
            cd[k] = int(cc[i].number_input(k, 0, 100, int(cd.get(k, 1)), key=f"cd_{k}"))
        doc["cd_arm"] = cd
    else:
        doc["cd_arm"] = None

    st.subheader("Temporary site D")
    has_d = st.checkbox("D を有効化", doc.get("temporary_site") is not None)
    if has_d:
        d = doc.get("temporary_site") or {"d_stay_table": {1: 12}, "occupancy_max": None}
        rows = _editor(gui_io.intmap_to_rows(d.get("d_stay_table", {}), "n", "hours"),
                       ["n", "hours"], "dtable")
        d["d_stay_table"] = gui_io.rows_to_intmap(rows, "n", "hours", int_key=True)
        cap = st.text_input("occupancy_max（空欄=無制限）",
                            "" if d.get("occupancy_max") is None else str(d["occupancy_max"]))
        d["occupancy_max"] = int(cap) if cap.strip() else None
        doc["temporary_site"] = d
    else:
        doc["temporary_site"] = None


def tab_passengers():
    doc = get_doc()
    st.subheader("Passengers")
    prows = _editor(doc.get("passengers", []), ["id", "category"], "pax")
    doc["passengers"] = [
        {"id": str(r.get("id", "")).strip(), "category": str(r.get("category", "")).strip()}
        for r in prows if str(r.get("id", "")).strip()
    ]

    st.subheader("Passenger rules (allowed B sites; カンマ区切り)")
    rules = doc.get("passenger_rules", {})
    rrows = _editor(gui_io.passenger_rules_to_rows(rules),
                    ["passenger", "allowed_sites"], "rules")
    doc["passenger_rules"] = gui_io.rows_to_passenger_rules(rrows)

    st.subheader("Initial state")
    st.caption("location は A / B島名 / D。arrived_at は ISO（空欄可）。")
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
    sp = doc.setdefault("solver", {})
    c = st.columns(3)
    sp["max_visits_per_passenger"] = int(c[0].number_input("max_visits_per_passenger", 1, 60,
                                         int(sp.get("max_visits_per_passenger", 4))))
    sp["trips_per_site"] = int(c[1].number_input("trips_per_site", 1, 300,
                               int(sp.get("trips_per_site", 8))))
    sp["trips_cd"] = int(c[2].number_input("trips_cd", 1, 300, int(sp.get("trips_cd", 8))))
    c2 = st.columns(2)
    sp["max_seconds"] = float(c2[0].number_input("max_seconds", 1.0, 600.0,
                              float(sp.get("max_seconds", 30.0))))
    cm = c2[1].text_input("commit_hours（空欄=単一ウィンドウ）",
                          "" if sp.get("commit_hours") is None else str(sp["commit_hours"]))
    sp["commit_hours"] = int(cm) if cm.strip() else None


def _validated_instance():
    return gui_io.instance_from_doc(get_doc())


def tab_save():
    doc = get_doc()
    st.subheader("Validate & Save")
    if st.button("Validate"):
        try:
            _validated_instance()
            st.success("OK: スキーマ検証に成功しました")
        except Exception as e:  # noqa: BLE001
            st.error("検証エラー:")
            st.code(str(e))
    st.divider()
    name = st.text_input("保存ファイル名", "edited.yaml")
    if st.button("instances/ に保存"):
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
                       file_name="instance.yaml", mime="text/yaml")
    with st.expander("YAML プレビュー"):
        st.code(gui_io.yaml_from_doc(doc), language="yaml")


def tab_run():
    st.subheader("Run solver")
    try:
        inst = _validated_instance()
    except Exception as e:  # noqa: BLE001
        st.error("先に検証を通してください:")
        st.code(str(e))
        return

    mode = st.radio("モード", ["Single window", "Rolling horizon"], horizontal=True)
    if mode == "Single window":
        if st.button("Solve (single)"):
            with st.spinner("solving..."):
                sol = FullModel(inst).solve()
            st.code(sol.summary())
    else:
        c = st.columns(2)
        wd = c[0].number_input("window_days (lookahead)", 1.0, 30.0, 6.0)
        sd = c[1].number_input("step_days (commit)", 1.0, 30.0, 5.0)
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
                                 file_name="schedule_trips.csv")
            d[1].download_button("stays.csv", paths["stays"].read_bytes(),
                                 file_name="schedule_stays.csv")


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Instance Editor", layout="wide")
    st.title("Fixed-Route Rotation — Instance Editor")
    get_doc()  # ensure init
    tabs = st.tabs(["Load/New", "General", "Vehicles & Fleet", "Sites",
                    "Passengers", "Solver", "Validate & Save", "Run"])
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


main()
