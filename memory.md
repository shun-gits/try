Problem Memory

Problem Summary

固定ルート上で人員輸送を行う。

目的は各拠点の制約を満たしながら輸送コストを最小化すること。

OR-Tools CP-SAT を利用予定。

⸻

Fixed Route

基本ルート:

A -> Bx -> A -> C -> D -> C -> A

Bx は B1〜Bn のいずれか。

B島間移動は存在しない。

例:

* B1 -> B2 不可
* B2 -> B3 不可

⸻

Planning Horizon

15日または30日などの有限期間で最適化する。

固定ルートを複数回周回する。

⸻

Site Types

A

Hub

B1〜Bn

Staffed Site

特徴:

* 常駐者が必要
* 滞在時間制約あり
* 交代制約あり

D

Temporary Site

特徴:

* 滞在時間が相乗り人数に依存

⸻

Passenger

各乗客は:

* ID
* Category

を持つ。

さらに

allowed_sites:

によって訪問可能なBサイトが決定される。

⸻

Initial State

計画開始時点で:

* A
* B1〜Bn
* D

のいずれにも乗客が存在可能。

arrived_at が重要。

⸻

B Site Rules

例:

B1

* Category1 が最低1名必要
* 常時成立

滞在:

* 最低24時間
* 最大48時間

Hard Constraint

⸻

Replacement Rule

常駐者帰還時は後任到着が必須。

典型例:

P10 が B1 滞在中

↓

P20 が到着

↓

P10 が帰還

可能

逆は不可。

⸻

Ride Constraint

例:

A_B1

Category1 と Category2 は同時乗車必須。

⸻

Vehicle Model

車両:

* Minivan
* Truck

有限台数を保有。

不足時はレンタル可能。

⸻

Backhaul

重要概念。

A→C

で人を降ろした後、

同じ車両で

C→A

へ帰還者を乗せる。

例:

Truck001

A→C

↓

到着

↓

C→A

⸻

Cost

車両種別によって異なる。

例:

* Minivan
* Truck

移動時間にも依存。

⸻

Solver Design

利用:

OR-Tools CP-SAT

不使用:

OR-Tools Routing Solver

理由:

経路最適化問題ではなく、

状態遷移を伴う時系列スケジューリング問題であるため。

⸻

Design Decisions（spec v0.2 で確定した事項）

B 系サイトは「島ごとパラメータ化」する。

理由:
ユーザー回答「常駐カテゴリ・同乗条件は島ごとに異なる」。
B1 は Category1 のみ常駐、Category2 は together で同乗するが滞在義務なし（随伴）。
一方 B2 は別カテゴリ常駐、という違いを吸収するため。

→ occupancy / category_requirements / stay / replacement / ride_constraints / segment(所要時間)
   を島単位（staffed_sites.B1, .B2 ...）に持たせる構造に変更。
   v0.1 の「B1 固定記述」は廃止。

⸻

レンタル費用は車種ごと独立単価。

vehicle_types.*.rental_cost_per_hour で定義（multiplier 方式ではない）。
レンタル台数は無制限に確保可能。これが実質「実行可能性の逃がし弁」になる
（48h 以内に帰せない場合は増便で必ず帰す）。

⸻

目的関数は車両運行費に一本化。

cost = Σ duration_hours × unit_cost（保有=cost_per_hour / レンタル=rental_cost_per_hour）。
運行時間コストは単価×時間に内包されるため独立項目にしない（v0.1 の3項目併記は重複だった）。

⸻

spec.md と パラメータファイルの二層構造（重要）。

spec.md = スキーマ（構造）＋例示。件数・カテゴリ体系・島ごとの値は固定しない。
パラメータファイル = 実インスタンス（乗客・カテゴリ・各 B 島の常駐カテゴリ/滞在時間/
所要時間/同乗条件・初期常駐者など）。

§9 の P001/P002 や §12 の B1/B2/B3 の値はすべて例示であり、実データはパラメータ供給。

確定:
- C は通過点（滞在・容量制約なし、車両の乗り継ぎ/Backhaul 折返し地点）
- B 島は B1/B2/B3 の3島運用
- B2/B3 は B1 と同形（occupancy.min / stay.min-max / replacement / ride together の構造共通、
  値のみ島ごと）

D 滞在ルールの確定:
- 「A_C 相乗人数」= その A_C 便の同乗人数（便単位）。到着時に確定。
- 各乗客の D 必要滞在時間 = table[その便の人数]。min のみ（上限は現状なし）。
- 帰還は個別（同便でなくてよい）。同一便の乗客は同じ table 値だが帰還は各自。
- A_C 乗車者は全員 D 行き（C は通過点で降車しない）。
- d_stay_rules.table は車両定員上限（truck=10）まで列挙。
- D 適格性制限なし（全乗客が D へ行ける）。allowed_sites は B 系のみ制御。

残りの確定事項:
- time_granularity = 1h（所要時間・滞在が整数時間のため整合）
- 保有車両は個体(id)単位で初期位置を指定。台数・初期位置はインスタンスパラメータ。
  レンタル発出元は A（既定）。
- D 滞在は上限なし（min のみ）。

⸻

現状（2026-06-16 時点）:
構造（スキーマ）側の未決はなし。spec.md は v0.2 で構造確定。
残りはすべてインスタンスパラメータ（乗客・カテゴリ・各 B 島の値・車両台数/初期位置・
D.occupancy.max・d_stay_rules.table 値）。

⸻

モデル化フェーズ：model.md v0.1 を作成（CP-SAT 定式化）。

採用したモデリング方針:
1. トリップ表現 = アーム別「往復トリップ列挙」方式。
   - B-arm(k): A→k→A（到着便と同一車両で交代者を回収）
   - CD-arm: A→C→D→C→A（往路で D 行き、復路で D 帰還者を Backhaul）
   - C は通過点（乗降・滞在なし）。実乗降は A / D / 各 B 島のみ。
2. 各トリップに整数出発時刻 dep[a,j] を持たせ、到着/出発時刻は所要時間加算で導出。
   時刻ごとの location[p,t] は持たない（滞在制約は時刻変数間の不等式）。
3. 常駐/カテゴリ制約は「イベント時点（トリップ完了直後）」で評価し ∀t を回避。
   乗降が便単位で離散 → 初期≥min かつ各イベント直後≥min で全時刻成立。
   replacement_required は「下限割れ禁止」で内包。

主な決定変数: used[a,j], assignV[a,j,v], dep[a,j], inB/outB[p,k,j], toD/frD[p,j],
arrB/depB/arrD/depD（滞在時刻）。
目的: Σ assignV × trip_hours(a) × unit_cost(v) の最小化。

乗客ライフサイクルの確定（重要・model v0.2 反映）:
- 1乗客は固定ルートを期間内で「周回」する（B島滞在→A→C→D滞在→A→再びB島滞在…）。
- B も D も複数回訪問。同一人が B 島勤務と D 訪問の両方を担いうる。
→ model は2層構造に改訂:
  (L1) トリップ層（車両ディスパッチ、アーム別往復トリップ列挙）
  (L2) 乗客訪問層（順序付き訪問スロット m=1..M で周回を表現）
  両層を結合変数 inB/outB[p,m,k,j], toD/frD[p,m,j] で接続（4添字）。
- 単一 arrB[p,k] を「スロット m 単位」a[p,m]/d[p,m] へ一般化。
- 常駐/カテゴリはイベント評価（∀t 回避）を維持。

想定インスタンス規模: |P| ≈ 数十人、H ≈ 720h（30日）。

規模見積りと上限（model.md v0.2 §6 で確定）:
- 最短B周回 ≈ smin+往復(28h)、最短D周回 ≈ dtable_min+8(32h) から上限導出。
- M≈26（乗客ごとに導出可）、J[B-arm(k)]≈60、J[CD]≈120、Nrent≈10。
- 結合変数 toD/frD が支配項。総 boolean ≈ 37万 + 時刻チャネリング。可解だが重い部類。
- Q2 決着: (a) スロット方式を継続採用（業務制約が素直）。
  (b) 時刻インデックス location[p,t] は不採用（遷移・容量結合が煩雑）。
- 緩和策: M を乗客別導出 / allowedB 絞込で k 削減 / J タイト化＋対称性除去 /
  段階解法（ベンチ後に結合変数を時刻ウィンドウで疎化）。

残課題（model.md §7、実装中に確定）:
Q4 frD 復路便の D 出発時刻の表現、Q5 公平性・連続勤務上限（将来 Soft）。

⸻

実装フェーズ：B-arm 縦スライス完成（2026-06-16）。

環境: /workspaces/try（既存の stock-data-factory 監視リポだが実体は本最適化問題のワークスペース）。
ortools 9.15 / pydantic 2.13 / pyyaml 使用。git は IDE 側で管理。

ファイル:
- route_opt/schema.py  : パラメータ YAML スキーマ（pydantic、B-arm 範囲）
- route_opt/loader.py  : YAML→Instance、休日時間帯・時刻オフセット計算
- route_opt/barm_model.py : B-arm の CP-SAT モデル（model.md §3/§4 の B-arm 部分集合）
- route_opt/run_barm.py : 実行エントリ（python -m route_opt.run_barm <yaml>）
- instances/barm_small.yaml : 検証用小インスタンス（60h, 1島, 3名）
- tests/test_barm.py : 正例＋実行不能反例（全5件パス）

検証済み: 交代/常駐/カテゴリ/together/滞在min-max/車両割当(owned優先)/容量/休日/目的最小化。
小インスタンスの最適解 = VAN001 1便(4h×100=400)で P001→P003 交代、P002 が together 相棒。

実装中に確定した2つのモデル精緻化（model.md に反映予定）:
1. 滞在 max は「期限 a+max ≤ H のときのみ帰還を強制」。期限が horizon を超える
   最後の常駐者は計画末まで残れる（さもないと末端で必ず無限交代が必要で実行不能）。
2. 同一乗客は同一トリップで入域と出域を兼ねられない（自己交代の禁止）。
   かつスロット間は A 往復の物理連続性を課す: a[m+1]-din(m+1) ≥ d[m]+dout(m)。
   （これが無いと「同一便で自分を交代」する非物理解が出た。）

CD-arm 完成（2026-06-16）。

D 需要の driver = 必須ローテーション（乗客単位・交互順のみ）で確定。
連続する勤務スロットは B/D を交互。B 常駐 min ＋ 限られた Cat1 プール →
再び B 勤務に就くには間に D 勤務が必要 → CD-arm が使われる。

追加ファイル:
- route_opt/model.py : Full モデル（B-arm + CD-arm）。barm のパターンを D まで一般化。
- route_opt/run_full.py : 実行エントリ
- instances/full_cd.yaml : CD 検証用（100h, B1 Cat1常駐, Cat1 2名のみ → 再循環で D 必須）
- instances/full_small.yaml : together 付きの逼迫例（プール不足で INFEASIBLE になる例）
- tests/test_full.py : 5件（CD使用/交互順/D最低滞在/短horizonでCD不使用/Cat1単独で実行不能）
全10テスト（barm 5 + full 5）パス。

CD-arm 検証結果（full_cd.yaml, OPTIMAL=2400）:
P001 が B1(0-29)→D(35-47, dtable[1]=12h)→B1(65-末) と交互ローテーション。
個別帰還（D 往路 CD t0 / 復路 CD t1 が別便）、Backhaul 構造、D 動的滞在、
全時刻 Cat1≥1、VAN001 単独で4便を無重複実行、すべて整合。

together 同乗者の扱い（確定・2026-06-16）:
ユーザー確認済み。together は「一緒に移動」かつ同乗者もサイト滞在者になる（前者＝現状モデルが正しい）。
相棒カテゴリ（例 Category2）も B 滞在 stay(min/max) と必須ローテーション(B→D)の対象。
→ モデル変更不要。spec §15 に明記。
→ 帰結: 相棒供給が不足するとローテーション連鎖で実行不能化（full_small.yaml）。
  これを test_full.py::test_together_escort_chain_infeasible でリグレッション固定（意図的に過剰制約）。
全11テストパス。

ベンチ完了（2026-06-16, BENCH.md）。

ファイル: route_opt/bench.py（インスタンス生成＋計測）, BENCH.md（レポート）。
FullModel.solve(hint=True/False) に探索ヒント（B-arm 交代周期の骨格）を追加。

主要所見:
- モデル規模は線形でスケール（30日×30人で約3万変数・9.5万制約・ビルド0.4s）。規模は問題ない。
  → allowedB 絞り込みで k 次元 collapse、M 小さく、で変数爆発は回避済み。
- ボトルネックは求解（探索）。1h 粒度時刻変数＋長 horizon の密結合ローテーション連鎖が重い。
- 可解フロンティア: ~7日=OPTIMAL、10–14日=不安定(FEASIBLE/UNKNOWN)、21日以上=feasible不到達。
- 単純ヒントでは不足。
- 小プール（島数名）は最悪領域（深い再循環で M 大）。大プールでも 30日結合は重い。

ローリングホライズン分解 完成（2026-06-17, BENCH.md §5）。

実装: route_opt/rolling.py（solve_rolling）, route_opt/run_rolling.py。
モデルに SolverParams.commit_hours 追加 → トリップは [0, commit] のみ、lookahead [commit, H]
は常駐の余裕確保用（trip なし）。各ウィンドウ終端(commit)の状態を次ウィンドウの
initial_state に渡す。D は InitialPassengerState.earliest_departure で残り必要滞在を引き継ぐ。

重要設計値: overlap = lookahead − commit ≤ smax − dout（tail を単一常駐者で覆える条件）。
実用上 overlap = 1日(24h) で十分。lookahead=6日 / commit=5日 を既定に。

検証結果:
- 30日 3島x6人（大プール）: 全6窓 OPTIMAL, total_cost 20400, 41s（CD=0: 大プールで再循環不要）。
- 30日 2島x3人（タイト）: 全6窓 OPTIMAL, CD便12, total_cost 23200, 18s（CD/D handoff 機能）。
→ 単一モデルで feasible 不到達だった 30日を分解で各窓最適に解ける。

テスト: tests/test_rolling.py 2件追加。全13テストパス（barm5 + full6 + rolling2）。

留意: 各窓最適≠全体最適（myopic、overlap で緩和）。overlap 上限は最小 smax に律速。

注: time domain の粗粒度化は不可（区間が 3h と 1h を含み GCD=1、1h が強制される）。

⸻

集約出力（CSV ＋ Gantt 可視化）完成（2026-06-17）。

可視化は matplotlib（静的 Gantt PNG）＋ pandas（CSV）を採用（matplotlib/pandas 導入済、
plotly は未使用）。ファイル:
- route_opt/report.py : build_stays（boardings＋初期常駐から滞在区間を再構成）/
  trips_df / write_csv / plot_gantt（乗客滞在＋車両稼働の2段 Gantt）。
- route_opt/run_rolling.py に CSV/PNG 出力を統合（out/ に schedule_stays.csv,
  schedule_trips.csv, schedule_gantt.png）。
- solve_rolling は trips/boardings を絶対時刻(h)で集約して RollingResult に格納。

分解で判明した重要な正当性バグ（修正済）:
ローテーション交互順は各ウィンドウ内でしか効かず、境界をまたぐと前回勤務種別を忘れ、
D を挟まず B→B が起きていた。修正: InitialPassengerState.last_duty を handoff し、
A 待機者の次スロット btype を交互強制（model.py）。
これにより正当な解になり、コストは上昇（以前の解は制約違反で過小だった）。
test_rolling.py::test_rolling_rotation_alternation_across_seams で固定。全14テストパス。

handoff で引き継ぐ状態（最終形）: location, arrived_at, earliest_departure(D残り滞在),
last_duty(A待機者の交互用)。

他に未実装: B2/B3 複数島テスト、D.occupancy_max テスト、休日 Full テスト、
plotly 等インタラクティブ可視化（必要なら）。

⸻

GUI（条件編集ツール）完成（2026-06-17）。

フレームワーク = Streamlit（依存に既存）。起動: `streamlit run apps/instance_editor.py`。
ファイル:
- apps/instance_editor.py : Streamlit UI（タブ: Load/New, General, Vehicles&Fleet, Sites,
  Passengers, Solver, Validate&Save, Run）。先頭で sys.path bootstrap（repo 規約, E402）。
- route_opt/gui_io.py : 純ヘルパ（doc↔Instance↔YAML、dict-keyed↔table 変換、ride_together↔str）。
- tests/test_gui_io.py : round-trip 6件。

作業状態は session_state["doc"] に JSON-able dict（Instance.model_dump(mode="json")）で保持。
検証は Validate/Save/Run 時に Instance.model_validate で実施。サンプルは bench.make_instance、
単一解は FullModel.summary、ローリングは solve_rolling + report.write_csv/plot_gantt を再利用。
内部 handoff フィールド（earliest_departure, last_duty）は UI 非公開（機械管理）。

pyproject: ortools/matplotlib/pyyaml を deps に追記、apps/instance_editor.py に ruff E402 ignore。
注: streamlit の use_container_width は非推奨 → width="stretch" を使用。
検証: py_compile OK、streamlit AppTest で例外0・8タブ描画、全20テストパス（gui_io6 含む）。

⸻

ドキュメント整備（2026-06-17）: spec.md → v0.3、model.md → v0.3 に更新。

spec.md v0.3 の主な追記:
- §17 Rotation Constraints を新設（必須ローテーション＝乗客単位・B/D 交互順。D 需要の駆動力）。
  これに伴い旧 §17–21 を §18–22 に繰り下げ。
- §13 に「帰還期限は arrival+max が計画期間内のときのみ帰還を強制」を明記。
- §21 を Solver / Reference Implementation に改題し、route_opt/ とローリングホライズン分解を記載。
- §22 確定済リストに v0.3 事項を追加。

model.md v0.3 の主な追記:
- 4.11 必須ローテーション制約（btype の交互）を追加（実装と同期。これまで本文に欠落していた）。
- spec 節番号の繰り下げを本文参照に反映（旧§17/18/19 → §18/19/20）。
- §7 を更新（Q4 解決）、§8「実装ステータス＆ローリングホライズン定式化」を追加
  （commit_hours 制約、handoff 状態 location/arrived_at/earliest_departure/last_duty）。

現状の成果物: spec.md(v0.3) / model.md(v0.3) / memory.md / BENCH.md ＋ route_opt/（実装）
＋ apps/instance_editor.py（GUI）＋ tests/（全20パス）。設計・実装・検証・可視化・GUI まで一通り完了。

⸻

この2ファイルをベースに、次のフェーズでは CP-SATの変数定義（Decision Variables）と制約数式化 を起こしていく。
未決事項は [TBD] を埋めつつ進める方針（TBD 付きで構造を先に固める）。

