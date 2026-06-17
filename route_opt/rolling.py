"""ローリングホライズン分解ドライバ。

長 horizon を ~W 時間のウィンドウに分割して順に解き、各ウィンドウ終端の状態
（誰がどこに・いつ到着・D の残り必要滞在）を次ウィンドウの initial_state として渡す。
本モデルは任意 initial_state（過去到着・earliest_departure）を扱えるため接続できる。

各ウィンドウは [w_start, w_end] の全トリップが w_end までに往復完了するので、
w_end 時点では全乗客が「サイト常駐 or A で待機」のいずれか（移動中はいない）。
これにより終端スナップショットが一意に取れる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .loader import hour_offset
from .model import D, FullModel
from .schema import InitialPassengerState, Instance, SolverParams


@dataclass
class RollingResult:
    ok: bool
    total_cost: float = 0.0
    windows: list[dict] = field(default_factory=list)   # 各ウィンドウの要約
    trips: list[dict] = field(default_factory=list)     # 通しのトリップ明細（絶対時刻h）
    boardings: list[dict] = field(default_factory=list)  # 乗降イベント（絶対時刻h）
    total_hours: int = 0
    message: str = ""


def _window_solver_params(base: SolverParams, commit_h: int) -> SolverParams:
    # commit 区間長から上限を導出（trips は commit 内のみ）。
    commit_days = commit_h / 24
    j = max(4, int(commit_days * 2) + 2)
    return SolverParams(
        max_visits_per_passenger=max(4, base.max_visits_per_passenger),
        trips_per_site=j,
        trips_cd=max(6, int(commit_days * 3)),
        max_seconds=base.max_seconds,
        commit_hours=commit_h,
    )


def _collect(mdl: FullModel, sol, cursor: int, trips: list[dict], boardings: list[dict]) -> None:
    """当ウィンドウの used トリップと乗降イベントを絶対時刻(h)で追記。
    トリップは [0, commit] 内のみなのでウィンドウ間で重複しない。"""
    s, inst = sol.solver, mdl.inst
    cd = inst.cd_arm
    for k in mdl.bsites:
        din = inst.staffed_sites[k].segments.inbound_hours
        dout = inst.staffed_sites[k].segments.outbound_hours
        for j in range(mdl.J):
            if not s.Value(mdl.usedB[k, j]):
                continue
            dep = cursor + int(s.Value(mdl.depB[k, j]))
            veh = next(v.id for v in mdl.vehicles if s.Value(mdl.assignB[k, j, v.id]))
            ins = [p for p in (pp.id for pp in inst.passengers) for mi in range(mdl.M)
                   if (p, mi, k, j) in mdl.inB and s.Value(mdl.inB[p, mi, k, j])]
            outs = [p for p in (pp.id for pp in inst.passengers) for mi in range(mdl.M)
                    if (p, mi, k, j) in mdl.outB and s.Value(mdl.outB[p, mi, k, j])]
            trips.append({"kind": "B", "site": k, "vehicle": veh,
                          "depart_A": dep, "arrive_site": dep + din,
                          "return_A": dep + din + dout, "in": ins, "out": outs})
            for p in ins:
                boardings.append({"passenger": p, "site": k, "event": "arrive", "t": dep + din})
            for p in outs:
                boardings.append({"passenger": p, "site": k, "event": "depart", "t": dep + din})
    for j in range(mdl.JCD):
        if not s.Value(mdl.usedCD[j]):
            continue
        dep = cursor + int(s.Value(mdl.depCD[j]))
        veh = next(v.id for v in mdl.vehicles if s.Value(mdl.assignCD[j, v.id]))
        arrD = dep + cd.to_d_hours
        td = [p for p in (pp.id for pp in inst.passengers) for mi in range(mdl.M)
              if s.Value(mdl.toD[p, mi, j])]
        fd = [p for p in (pp.id for pp in inst.passengers) for mi in range(mdl.M)
              if s.Value(mdl.frD[p, mi, j])]
        # 車両の A 帰着は運転区間のみ（C↔D は徒歩で配車不要）。
        depD = dep + cd.d_depart_offset   # 帰還者が D を発つ時刻（D→C 徒歩 → C で乗車）
        trips.append({"kind": "CD", "site": D, "vehicle": veh,
                      "depart_A": dep, "arrive_site": arrD,
                      "return_A": dep + cd.drive_hours, "in": td, "out": fd,
                      "nAC": int(s.Value(mdl.nAC[j]))})
        for p in td:
            boardings.append({"passenger": p, "site": D, "event": "arrive", "t": arrD})
        for p in fd:
            boardings.append({"passenger": p, "site": D, "event": "depart", "t": depD})


def _snapshot(mdl: FullModel, sol, w_start: datetime, prev_state: dict[str, InitialPassengerState],
              ) -> list[InitialPassengerState]:
    """ウィンドウ終端（=window end）の各乗客状態を絶対時刻で返す。"""
    s, inst = sol.solver, mdl.inst
    tbl = inst.temporary_site.d_stay_table
    maxn = max(tbl.keys())
    table = [0] * (maxn + 1)
    for n, h in tbl.items():
        table[n] = h
    out: list[InitialPassengerState] = []
    for p in (pp.id for pp in inst.passengers):
        used = [mi for mi in range(mdl.M) if s.Value(mdl.atused[p, mi])]
        if not used:
            # 当ウィンドウで一切動かず → 前回の last_duty を引き継ぐ
            prev = prev_state.get(p)
            ld = prev.last_duty if prev else None
            out.append(InitialPassengerState(passenger_id=p, location="A", last_duty=ld))
            continue
        last = max(used)
        last_site = next(k for k in (mdl.bsites + [D]) if s.Value(mdl.at[p, last, k]))
        if s.Value(mdl.leaves[p, last]) == 1:
            # A に戻った → 直前勤務種別を記録（次窓で交互を強制）
            ld = "B" if last_site in mdl.bsites else "D"
            out.append(InitialPassengerState(passenger_id=p, location="A", last_duty=ld))
            continue
        site = last_site
        arrived_abs = w_start + timedelta(hours=int(s.Value(mdl.a[p, last])))
        if site != D:
            out.append(InitialPassengerState(passenger_id=p, location=site,
                                             arrived_at=arrived_abs))
        else:
            toD_js = [j for j in range(mdl.JCD) if s.Value(mdl.toD[p, last, j])]
            if toD_js:
                req = table[int(s.Value(mdl.nAC[toD_js[0]]))]
                earliest = arrived_abs + timedelta(hours=req)
            else:
                # 当ウィンドウで toD せず継続滞在中の初期 D 常駐者 → 前状態を引き継ぐ
                prev = prev_state.get(p)
                arrived_abs = prev.arrived_at if prev and prev.arrived_at else arrived_abs
                earliest = prev.earliest_departure if prev else None
            out.append(InitialPassengerState(passenger_id=p, location=D,
                                             arrived_at=arrived_abs,
                                             earliest_departure=earliest))
    return out


def solve_rolling(base: Instance, window_days: float = 7.0, step_days: float = 5.0,
                  verbose: bool = True) -> RollingResult:
    """window_days = lookahead（解く長さ）、step_days = commit（確定して次へ進む長さ）。
    overlap = window_days - step_days が、シーム常駐者の余裕を生む。"""
    g_start = base.planning_horizon.start
    total_h = base.planning_horizon.hours
    win_h = int(window_days * 24)
    step_h = int(step_days * 24)

    state = {st.passenger_id: st for st in base.initial_state}
    cursor = 0
    total_cost = 0.0
    windows: list[dict] = []
    trips: list[dict] = []
    boardings: list[dict] = []

    while cursor < total_h:
        remaining = total_h - cursor
        if remaining <= win_h:
            lookahead = remaining          # 最終区間: ウィンドウ内に収まる
            commit = remaining             # 全部 commit（end まで）
        else:
            lookahead = win_h
            commit = min(step_h, remaining)

        w_start = g_start + timedelta(hours=cursor)
        w_end = w_start + timedelta(hours=lookahead)

        winst = base.model_copy(deep=True)
        winst.planning_horizon.start = w_start
        winst.planning_horizon.end = w_end
        winst.initial_state = list(state.values())
        winst.solver = _window_solver_params(base.solver, commit)

        mdl = FullModel(winst)
        sol = mdl.solve(hint=True)
        if not sol.ok:
            return RollingResult(ok=False, total_cost=total_cost, windows=windows,
                                 trips=trips, boardings=boardings, total_hours=total_h,
                                 message=f"window @{cursor}h (look={lookahead}h commit={commit}h) "
                                         f"{sol.solver.StatusName(sol.status)}")
        _collect(mdl, sol, cursor, trips, boardings)

        obj = sol.solver.ObjectiveValue()
        total_cost += obj
        nB = sum(int(sol.solver.Value(mdl.usedB[k, j]))
                 for k in mdl.bsites for j in range(mdl.J))
        nCD = sum(int(sol.solver.Value(mdl.usedCD[j])) for j in range(mdl.JCD))
        w = {"start_h": cursor, "look_h": lookahead, "commit_h": commit,
             "status": sol.solver.StatusName(sol.status),
             "cost": obj, "b_trips": nB, "cd_trips": nCD,
             "wall": round(sol.solver.WallTime(), 1)}
        windows.append(w)
        if verbose:
            print(f"  win @{cursor:>3}h look={lookahead:>3} commit={commit:>3}: "
                  f"{w['status']:<9} cost={obj:>7.0f} B={nB} CD={nCD} wall={w['wall']}s")

        # commit 時点でスナップショット（その時点まで trip 完了済 → 全員 at rest）
        state = {st.passenger_id: st for st in _snapshot(mdl, sol, w_start, state)}
        cursor += commit

    return RollingResult(ok=True, total_cost=total_cost, windows=windows,
                         trips=trips, boardings=boardings, total_hours=total_h)
