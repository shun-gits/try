"""B-arm スライスの実行エントリ。

  python -m route_opt.run_barm instances/barm_small.yaml
"""

from __future__ import annotations

import sys

from .barm_model import BArmModel
from .loader import load_instance


def main(path: str) -> int:
    inst = load_instance(path)
    print(f"loaded: {path}  (H={inst.planning_horizon.hours}h, "
          f"sites={list(inst.staffed_sites)}, pax={len(inst.passengers)})")
    sol = BArmModel(inst).solve()
    print(sol.summary())
    return 0 if sol.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "instances/barm_small.yaml"))
