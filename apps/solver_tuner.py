"""Solver parameter tuner — 管理者向け専用 Streamlit アプリ。

インスタンス YAML から最適化パラメータの理論上限値を自動計算して表示し、
結果を configs/solver_config.yaml に保存する。

起動:
    streamlit run apps/solver_tuner.py
"""

from __future__ import annotations

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402
import yaml  # noqa: E402

from route_opt import gui_io  # noqa: E402
from route_opt.loader import load_instance  # noqa: E402
from route_opt.schema import Instance  # noqa: E402
from route_opt.solver_cfg import (  # noqa: E402
    _CONFIG_PATH,
    load_solver_config,
    theoretical_params,
)

INSTANCES = pathlib.Path("instances")


# --------------------------------------------------------------------------
def _load_current_config() -> dict:
    cfg = load_solver_config()
    return {
        "max_visits_per_passenger": cfg.get("max_visits_per_passenger"),
        "trips_per_site": cfg.get("trips_per_site"),
        "trips_cd": cfg.get("trips_cd"),
        "max_seconds": float(cfg.get("max_seconds", 30.0)),
        "commit_hours": cfg.get("commit_hours"),
    }


def _save_config(cfg: dict) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Solver configuration — managed by system administrators.\n"
        "#\n"
        "# max_visits_per_passenger / trips_per_site / trips_cd\n"
        "#   null にすると instance_editor がインスタンスから理論上限値を自動計算します。\n"
        "#   手動で上書きしたい場合は整数を指定してください。\n"
        "#\n"
        "# max_seconds: ソルバーの最大実行時間（秒）。\n"
        "# commit_hours: ローリングホライズンのコミット幅。null = 単一ウィンドウ。\n\n"
    )
    _CONFIG_PATH.write_text(
        header + yaml.dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
def _explain_m(inst: Instance, theory: dict) -> str:
    H = inst.planning_horizon.hours
    lines = [f"計画期間 H = {H}h"]
    min_b = None
    for name, site in inst.staffed_sites.items():
        c = site.segments.inbound_hours + site.stay.min_hours + site.segments.outbound_hours
        lines.append(f"  B サイト「{name}」最短サイクル = "
                     f"{site.segments.inbound_hours}(往) + {site.stay.min_hours}(滞在) "
                     f"+ {site.segments.outbound_hours}(復) = {c}h")
        if min_b is None or c < min_b:
            min_b = c
    if inst.cd_arm is not None and inst.temporary_site is not None:
        rh = inst.cd_arm.round_hours
        tbl = inst.temporary_site.d_stay_table
        d_stays: list[int] = []
        if tbl and all(isinstance(v, dict) for v in tbl.values()):
            d_stays = [int(h) for sub in tbl.values() for h in sub.values()]  # type: ignore[union-attr]
        elif tbl:
            d_stays = [int(v) for v in tbl.values()]  # type: ignore[union-attr]
        min_d_stay = min(d_stays) if d_stays else 0
        lines.append(f"  CD-arm 最短サイクル = {rh}(往復) + {min_d_stay}(D最短滞在) = {rh + min_d_stay}h")
    min_c = min(x for x in [min_b, (inst.cd_arm.round_hours if inst.cd_arm else None)]
                if x is not None) if inst.staffed_sites or inst.cd_arm else 24
    lines.append(f"M = ceil({H} / {min_c}) = {theory['max_visits_per_passenger']}")
    return "\n".join(lines)


def _explain_j(inst: Instance, theory: dict) -> str:
    H = inst.planning_horizon.hours
    lines = [f"計画期間 H = {H}h（各サイトの ceil(H / min_stay) の最大値）"]
    for name, site in inst.staffed_sites.items():
        s = site.stay.min_hours
        j = math.ceil(H / s) if s > 0 else H
        lines.append(f"  B サイト「{name}」: ceil({H} / {s}) = {j}")
    lines.append(f"J = {theory['trips_per_site']}（全サイト中の最大値）")
    return "\n".join(lines)


def _explain_jcd(inst: Instance, theory: dict) -> str:
    if inst.cd_arm is None:
        return "cd_arm が未定義のため計算不要"
    H = inst.planning_horizon.hours
    rh = inst.cd_arm.round_hours
    return (
        f"計画期間 H = {H}h\n"
        f"CD 往復所要 = a_c({inst.cd_arm.a_c_hours}) + c_d({inst.cd_arm.c_d_hours}) "
        f"+ d_c({inst.cd_arm.d_c_hours}) + c_a({inst.cd_arm.c_a_hours}) = {rh}h\n"
        f"JCD = ceil({H} / {rh}) = {theory['trips_cd']}"
    )


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Solver Tuner", layout="wide")
    st.title("Solver Parameter Tuner")
    st.caption(
        "管理者向け: インスタンスから最適化パラメータの理論上限値を計算し、"
        "`configs/solver_config.yaml` に保存します。"
    )

    # --- インスタンス選択 ---
    st.subheader("1. インスタンスを選択")
    files = sorted(p.name for p in INSTANCES.glob("*.yaml")) if INSTANCES.exists() else []

    inst: Instance | None = None
    if files:
        col1, col2 = st.columns([4, 1])
        pick = col1.selectbox("instances/ から選択", files)
        if col2.button("読み込み"):
            try:
                inst = load_instance(INSTANCES / pick)
                st.session_state["tuner_inst"] = inst
                st.success(f"読み込み完了: {pick}")
            except Exception as e:  # noqa: BLE001
                st.error(f"読み込みエラー: {e}")
    else:
        st.info("instances/ フォルダに YAML ファイルがありません。")

    up = st.file_uploader("または YAML をアップロード", type=["yaml", "yml"])
    if up is not None:
        try:
            doc = gui_io.doc_from_yaml(up.getvalue().decode("utf-8"))
            inst = gui_io.instance_from_doc(doc)
            st.session_state["tuner_inst"] = inst
            st.success("アップロード YAML を読み込みました")
        except Exception as e:  # noqa: BLE001
            st.error(f"読み込みエラー: {e}")

    if inst is None:
        inst = st.session_state.get("tuner_inst")

    if inst is None:
        st.info("インスタンスを選択すると理論値を計算します。")
        st.divider()
    else:
        # --- 理論値の表示 ---
        H = inst.planning_horizon.hours
        theory = theoretical_params(inst)
        st.subheader("2. 理論上限値（自動計算）")
        st.caption(
            "各値はモデルが**解を見落とさないための上界**です。大きすぎると変数数が増え求解が遅くなります。"
            "下のパラメータ設定で手動に縮小するか、null（自動）のまま運用することを推奨します。"
        )
        cols = st.columns(3)
        with cols[0]:
            st.metric("M — max_visits_per_passenger", theory["max_visits_per_passenger"])
            with st.expander("計算根拠"):
                st.code(_explain_m(inst, theory))
        with cols[1]:
            st.metric("J — trips_per_site", theory["trips_per_site"])
            with st.expander("計算根拠"):
                st.code(_explain_j(inst, theory))
        with cols[2]:
            st.metric("JCD — trips_cd", theory["trips_cd"])
            with st.expander("計算根拠"):
                st.code(_explain_jcd(inst, theory))

        st.divider()

    # --- 現在の設定を編集して保存 ---
    st.subheader("3. solver_config.yaml の編集と保存")
    st.caption(
        "`configs/solver_config.yaml` の現在値を編集できます。"
        "空欄（null）にした項目はインスタンスから自動計算されます。"
    )
    cfg = _load_current_config()

    def _int_or_none(label: str, key: str, theory_val: int | None = None) -> int | None:
        cur = cfg[key]
        hint = f"（理論値: {theory_val}）" if theory_val is not None else ""
        use_auto = st.checkbox(f"{label} を自動計算（null）{hint}", value=cur is None, key=f"auto_{key}")
        if use_auto:
            return None
        default = cur if cur is not None else (theory_val or 1)
        return st.number_input(label, 1, 9999, int(default), key=f"val_{key}")

    tv = theoretical_params(inst) if inst else {}
    m_val = _int_or_none("M — max_visits_per_passenger", "max_visits_per_passenger", tv.get("max_visits_per_passenger"))
    j_val = _int_or_none("J — trips_per_site", "trips_per_site", tv.get("trips_per_site"))
    jcd_val = _int_or_none("JCD — trips_cd", "trips_cd", tv.get("trips_cd"))

    st.divider()
    max_sec = st.number_input("max_seconds（ソルバー最大実行時間 [秒]）", 1.0, 3600.0,
                              float(cfg["max_seconds"]), step=5.0, key="max_seconds")
    ch_str = st.text_input(
        "commit_hours（ローリングホライズン幅 [時間]、空欄 = 単一ウィンドウ）",
        "" if cfg["commit_hours"] is None else str(cfg["commit_hours"]),
        key="commit_hours",
    )
    commit_val = int(ch_str) if ch_str.strip() else None

    st.divider()
    if st.button("configs/solver_config.yaml に保存", type="primary"):
        new_cfg = {
            "max_visits_per_passenger": m_val,
            "trips_per_site": j_val,
            "trips_cd": jcd_val,
            "max_seconds": float(max_sec),
            "commit_hours": commit_val,
        }
        _save_config(new_cfg)
        st.success("保存しました: configs/solver_config.yaml")
        with st.expander("保存内容"):
            st.code(yaml.dump(new_cfg, sort_keys=False, allow_unicode=True), language="yaml")

    # --- 現在のファイル内容プレビュー ---
    st.divider()
    with st.expander("現在の configs/solver_config.yaml"):
        if _CONFIG_PATH.exists():
            st.code(_CONFIG_PATH.read_text(encoding="utf-8"), language="yaml")
        else:
            st.info("ファイルがまだ存在しません。")


main()
