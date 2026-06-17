"""ローリングホライズン分解の実行エントリ。

  python -m route_opt.run_rolling instances/full_cd.yaml [window_days] [step_days]
"""

from __future__ import annotations

import sys

from .loader import load_instance
from .report import plot_gantt, write_csv
from .rolling import solve_rolling


def main(path: str, window_days: float, step_days: float, outdir: str = "out") -> int:
    inst = load_instance(path)
    print(f"rolling: {path}  H={inst.planning_horizon.hours}h "
          f"lookahead={window_days}d commit={step_days}d pax={len(inst.passengers)}")
    r = solve_rolling(inst, window_days=window_days, step_days=step_days)
    print(("OK" if r.ok else "FAIL"), r.message)
    if not r.ok:
        return 1
    cd = sum(w["cd_trips"] for w in r.windows)
    b = sum(w["b_trips"] for w in r.windows)
    print(f"total_cost={r.total_cost:.0f} windows={len(r.windows)} "
          f"B_trips={b} CD_trips={cd}")
    paths = write_csv(r, inst, outdir)
    png = plot_gantt(r, inst, f"{outdir}/schedule_gantt.png")
    print(f"wrote: {paths['trips']}, {paths['stays']}, {png}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    path = args[0] if args else "instances/full_cd.yaml"
    wd = float(args[1]) if len(args) > 1 else 6.0
    sd = float(args[2]) if len(args) > 2 else 5.0
    sys.exit(main(path, wd, sd))
