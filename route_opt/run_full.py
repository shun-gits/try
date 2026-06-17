"""Full モデル（B-arm + CD-arm）の実行エントリ。

  python -m route_opt.run_full instances/full_small.yaml
"""

from __future__ import annotations

import sys

from .loader import load_instance
from .model import FullModel


def main(path: str) -> int:
    inst = load_instance(path)
    print(f"loaded: {path}  (H={inst.planning_horizon.hours}h, "
          f"B={list(inst.staffed_sites)}, pax={len(inst.passengers)})")
    sol = FullModel(inst).solve()
    print(sol.summary())
    return 0 if sol.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "instances/full_small.yaml"))
