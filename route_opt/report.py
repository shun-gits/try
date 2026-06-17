"""通しスケジュールの集約出力（CSV ＋ Gantt 可視化）。

solve_rolling の結果（trips / boardings, 絶対時刻h）から:
  - 乗客の滞在区間（site, 到着, 出発）を再構成
  - トリップ明細
を作り、CSV と matplotlib の Gantt(PNG) に出力する。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .loader import hour_offset  # noqa: E402
from .rolling import RollingResult  # noqa: E402
from .schema import Instance  # noqa: E402


def build_stays(result: RollingResult, base: Instance) -> pd.DataFrame:
    """boardings ＋ 初期常駐者から、各乗客の滞在区間を再構成する。"""
    H = result.total_hours
    # 乗客ごとのイベント列
    ev: dict[str, list[tuple[int, str, str]]] = {}
    for b in result.boardings:
        ev.setdefault(b["passenger"], []).append((b["t"], b["event"], b["site"]))
    # 初期常駐者（A 以外）は global start 時点の到着イベントとして追加
    for st in base.initial_state:
        if st.location == "A":
            continue
        t0 = hour_offset(base, st.arrived_at) if st.arrived_at else 0
        ev.setdefault(st.passenger_id, []).append((t0, "arrive", st.location))

    rows = []
    for p, events in ev.items():
        events.sort(key=lambda e: e[0])
        open_site = None
        open_t = None
        for t, kind, site in events:
            if kind == "arrive":
                open_site, open_t = site, t
            elif kind == "depart" and open_site is not None:
                rows.append({"passenger": p, "site": open_site,
                             "start_h": open_t, "end_h": t, "ongoing": False})
                open_site = None
        if open_site is not None:
            rows.append({"passenger": p, "site": open_site,
                         "start_h": open_t, "end_h": H, "ongoing": True})
    df = pd.DataFrame(rows).sort_values(["passenger", "start_h"]).reset_index(drop=True)
    return df


def trips_df(result: RollingResult) -> pd.DataFrame:
    rows = []
    for t in result.trips:
        rows.append({
            "kind": t["kind"], "site": t["site"], "vehicle": t["vehicle"],
            "depart_A_h": t["depart_A"], "arrive_site_h": t["arrive_site"],
            "return_A_h": t["return_A"],
            "passengers_in": "|".join(t["in"]), "passengers_out": "|".join(t["out"]),
            "nAC": t.get("nAC", ""),
        })
    return pd.DataFrame(rows).sort_values(["depart_A_h", "vehicle"]).reset_index(drop=True)


def write_csv(result: RollingResult, base: Instance, outdir: str | Path) -> dict[str, Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stays = build_stays(result, base)
    trips = trips_df(result)
    p_stays = outdir / "schedule_stays.csv"
    p_trips = outdir / "schedule_trips.csv"
    stays.to_csv(p_stays, index=False)
    trips.to_csv(p_trips, index=False)
    return {"stays": p_stays, "trips": p_trips}


def _site_color(site: str, bsites: list[str]) -> str:
    palette = ["#3b76c2", "#2e9e6b", "#9b59b6", "#16a085", "#2980b9"]
    if site == "D":
        return "#e08a2b"
    return palette[bsites.index(site) % len(palette)]


def plot_gantt(result: RollingResult, base: Instance, outpath: str | Path) -> Path:
    """乗客滞在 Gantt（上）＋ 車両稼働 Gantt（下）を1枚の PNG に。"""
    bsites = list(base.staffed_sites.keys())
    stays = build_stays(result, base)
    H = result.total_hours
    pax = sorted(stays["passenger"].unique()) if not stays.empty else []
    vehicles = sorted({t["vehicle"] for t in result.trips})

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, max(4, 0.4 * (len(pax) + len(vehicles)) + 3)),
        gridspec_kw={"height_ratios": [max(1, len(pax)), max(1, len(vehicles))]},
    )

    # --- 乗客滞在 ---
    ypos = {p: i for i, p in enumerate(pax)}
    for _, r in stays.iterrows():
        x0 = r["start_h"] / 24.0
        w = (r["end_h"] - r["start_h"]) / 24.0
        c = _site_color(r["site"], bsites)
        ax1.barh(ypos[r["passenger"]], w, left=x0, height=0.6,
                 color=c, edgecolor="black", linewidth=0.4,
                 hatch="//" if r["ongoing"] else None)
        if w > 0.6:
            ax1.text(x0 + w / 2, ypos[r["passenger"]], r["site"],
                     ha="center", va="center", fontsize=7, color="white")
    ax1.set_yticks(range(len(pax)))
    ax1.set_yticklabels(pax, fontsize=8)
    ax1.set_title("Passenger schedule (site stays)  [hatched = ongoing to horizon end]")
    ax1.set_xlim(0, H / 24.0)
    ax1.grid(axis="x", alpha=0.3)
    sites_in = list(dict.fromkeys(stays["site"])) if not stays.empty else []
    ax1.legend(handles=[mpatches.Patch(color=_site_color(s, bsites), label=s)
                        for s in sites_in], loc="upper right", fontsize=7, ncol=len(sites_in) or 1)

    # --- 車両稼働 ---
    yv = {v: i for i, v in enumerate(vehicles)}
    for t in result.trips:
        x0 = t["depart_A"] / 24.0
        w = (t["return_A"] - t["depart_A"]) / 24.0
        c = "#444" if t["kind"] == "CD" else "#888"
        ax2.barh(yv[t["vehicle"]], w, left=x0, height=0.5, color=c,
                 edgecolor="black", linewidth=0.3)
    ax2.set_yticks(range(len(vehicles)))
    ax2.set_yticklabels(vehicles, fontsize=8)
    ax2.set_title("Vehicle utilization (trips)  [dark = CD-arm, light = B-arm]")
    ax2.set_xlabel("days")
    ax2.set_xlim(0, H / 24.0)
    ax2.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    outpath = Path(outpath)
    fig.savefig(outpath, dpi=120)
    plt.close(fig)
    return outpath
