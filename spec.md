spec.md（ドラフト v0.2）

Fixed Route Workforce Rotation Optimization Specification

変更履歴
- v0.1 → v0.2:
  - B 系サイトを「島ごとパラメータ化」構造へ統合（occupancy / category / stay / replacement / ride / segment を島単位に）
  - レンタル費用を車種ごと独立単価として定義
  - 目的関数を「車両運行費＋レンタル費」の一本化に整理（運行時間コストは単価×時間に内包）
  - 未決事項を [TBD] マーカーで明示

⸻

1. Overview

本システムは固定ルート上で人員を輸送し、各拠点の滞在条件・常駐条件・同乗条件を満たしながら輸送コストを最小化する。

ソルバーは OR-Tools CP-SAT を利用する。Routing Solver は使用しない（経路最適化ではなく、状態遷移を伴う時系列スケジューリング問題のため）。

⸻

2. Planning Horizon

planning_horizon:
  start: 2026-01-01T00:00:00
  end: 2026-01-31T23:59:59

time_granularity: 1h   # CP-SAT の時刻離散化粒度（確定: 1h）。所要時間・滞在が整数時間のため整合。

有限期間のみ最適化対象とする。固定ルートを期間内で複数回周回する。

⸻

3. Calendar

calendar:
  holidays:
    - 2026-01-10
    - 2026-01-11

休日は全ての輸送が禁止される（全セグメント運休）。

⸻

4. Sites

Hub

A:
  type: hub

Transit Node

C:
  type: transit            # 通過点（滞在・容量制約なし）。A_C / C_D / D_C / C_A の中継点。
                           # 人は C に滞在せず、車両の乗り継ぎ/Backhaul 折返し地点として機能する。

Staffed Sites（詳細は §12 に島ごと定義）

B1:
  type: staffed_site
B2:
  type: staffed_site
B3:
  type: staffed_site

Temporary Site

D:
  type: temporary_site
  occupancy:
    max: null              # [TBD] D の最大収容人数。未定（プレースホルダ）。
  # 適格性制限なし（全乗客が D へ行ける）。滞在時間は §16 d_stay_rules で決定。

⸻

5. Routes

固定ルート

A -> Bx -> A -> C -> D -> C -> A

Bx は B1〜Bn のいずれか。B 島間移動（例 B1 -> B2）は存在しない。
Bx を訪問してから D を訪問するまでの間、別の B 島は訪問できない（経路構造上自明）。

⸻

6. Route Segments

A⇔C / C⇔D 区間（全島共通）

segments:
  A_C:
    duration_hours: 3
    allowed_vehicle_types: [minivan, truck]
  C_D:
    duration_hours: 1
  D_C:
    duration_hours: 1
  C_A:
    duration_hours: 3
    allowed_vehicle_types: [minivan, truck]

A⇔Bx 区間は島ごとに定義する（§12 staffed_sites.*.segments を参照）。

⸻

7. Vehicle Types

vehicle_types:
  minivan:
    capacity: 4
    cost_per_hour: 100
    rental_cost_per_hour: 150
  truck:
    capacity: 10
    cost_per_hour: 180
    rental_cost_per_hour: 250

運行コスト = duration_hours × cost_per_hour（保有車）または × rental_cost_per_hour（レンタル車）。

⸻

8. Vehicle Fleet

保有車両は個体（id）単位で定義し、初期位置を車両ごとに指定する（initial_location はインスタンスパラメータ）。

fleet:
  owned:                       # ← 個体リスト。台数・初期位置はパラメータファイルで与える（例示）
    - id: VAN001
      type: minivan
      initial_location: A
    - id: VAN002
      type: minivan
      initial_location: A
    - id: TRUCK001
      type: truck
      initial_location: A
  rental:
    enabled: true
    initial_location: A        # レンタル車は必要時に確保。発出元は A（既定）。
    # 追加台数は無制限に確保可能（コストは rental_cost_per_hour）。

⸻

9. Passengers

乗客・カテゴリは「インスタンスパラメータ」であり、パラメータファイルで与える。
spec.md は件数・カテゴリ体系を固定しない（以下は構造を示す例示にすぎない）。
カテゴリ数は任意（Category1, Category2, ... CategoryN）。

passengers:        # ← 例示。実データはパラメータファイルで増減する。
  - id: P001
    category: Category1
  - id: P002
    category: Category2

⸻

10. Passenger Rules

passenger_rules:
  P001:
    allowed_sites:
      - B1
  P002:
    allowed_sites:
      - B2

allowed_sites 以外への移動は禁止（サイト適格性は Passenger 単位）。
allowed_sites は B 系サイトへの適格性を制御する。D は適格性制限なし（全乗客が行ける）。

⸻

11. Initial State

initial_state:
  passengers:
    - passenger_id: P001
      location: B1
      arrived_at: 2026-01-01T09:00:00
    - passenger_id: P002
      location: A
  # [TBD] B2 / B3 に常駐 min 制約がある場合、開始時点で常駐者を置かないと
  #       t=0 で常駐制約違反になる。各 B 島の初期常駐者を要定義。

全乗客の初期位置を定義する。arrived_at は滞在時間制約（§12 stay）の起点として重要。

⸻

12. Staffed Sites（島ごとパラメータ化）

各 B 島は以下を島単位で持つ。構造（スキーマ）は全島共通で、B1 と同形:
  occupancy.min / category_requirements / stay.{min,max}_hours /
  replacement_required / ride_constraints / segments.{inbound,outbound}

常駐カテゴリ・滞在時間・同乗カテゴリ・所要時間といった「値」はインスタンスパラメータであり、
パラメータファイルで島ごとに与える（以下は構造を示す例示。B1/B2/B3 の3島運用）。

staffed_sites:
  B1:
    occupancy:
      min: 1                       # 全時刻で occupancy(B1,t) >= 1
    category_requirements:
      Category1:
        min: 1                     # 全時刻で Category1 が 1 名以上常駐
    stay:
      min_hours: 24                # Hard。arrival + min_hours <= departure
      max_hours: 48                # Hard。departure <= arrival + max_hours
    replacement_required: true     # 帰還前に後任到着が必須（§14）
    ride_constraints:              # この島への A_Bx 便に適用（§15）
      - type: together
        categories:
          - Category1
          - Category2
    segments:
      inbound:  { name: A_B1, duration_hours: 2 }
      outbound: { name: B1_A, duration_hours: 2 }

  B2:                              # ← 値はインスタンスパラメータ（以下は例示）
    occupancy:
      min: 1
    category_requirements:
      Category2:
        min: 1                     # 常駐カテゴリは島ごとに異なってよい
    stay:
      min_hours: 24
      max_hours: 48
    replacement_required: true
    ride_constraints: []           # 同乗条件は島ごと（無しも可）
    segments:
      inbound:  { name: A_B2, duration_hours: 2 }
      outbound: { name: B2_A, duration_hours: 2 }

  B3:                              # ← B1/B2 と同形。値はインスタンスパラメータ（例示）
    occupancy:
      min: 1
    category_requirements:
      Category3:
        min: 1
    stay:
      min_hours: 24
      max_hours: 48
    replacement_required: true
    ride_constraints: []
    segments:
      inbound:  { name: A_B3, duration_hours: 2 }
      outbound: { name: B3_A, duration_hours: 2 }

category_requirements は全時刻で成立しなければならない（Hard）。

⸻

13. Stay Constraints（再掲・数式）

各 staffed_site の stay は Hard Constraint:

arrival_time + min_hours <= departure_time
departure_time <= arrival_time + max_hours

滞在時間は「乗客個人」に対してかかる（その人自身の到着〜帰還）。

⸻

14. Replacement Constraints

replacement_required: true の島では、常駐者が帰還する場合、帰還前に後任が到着していなければならない。

数式イメージ:
  occupancy_after >= occupancy_before
  （= 帰還者数 <= その便での到着者数）

§12 の occupancy.min と組み合わさり、交代勤務制約として機能する。

⸻

15. Ride Constraints

同乗条件は §12 staffed_sites.*.ride_constraints に島単位で定義する。

type: together
  指定カテゴリ群は、対象 A_Bx 便において全員存在または全員不在のいずれか。
  例（B1）: Category1 だけ乗車 → NG / Category2 だけ → NG / 両方 → OK / 両方なし → OK

将来拡張: type: exclude（排他）, type: require（片方向含意）等を同じ rules 配列で追加可能。

⸻

16. D Stay Rules

d_stay_rules:
  based_on_segment: A_C
  # 人数は「その A_C 便の同乗人数（便単位）」。車両定員上限までテーブルに列挙する。
  # 値はインスタンスパラメータ（以下は例示。実際は 1..最大定員 まで定義）。
  table:
    1: 24
    2: 36
    3: 48
    # ... 4..10（truck 定員上限まで）を実データで列挙

定義:
- A_C 便に同乗した人数 n により、その便で到着した各乗客の D 必要滞在時間 = table[n]。
  人数は「その便の同乗人数」で確定し、到着時点で固定される（便ごとに独立）。
- A_C に乗車した者は全員 D へ向かう（C は通過点で降車・滞在しない）。
- D 滞在は最低滞在時間（min）として扱う:
    departure_from_D >= arrival_at_D + table[n]
  上限（max）なし（確定）。table[n] 経過後はいつでも帰還可。
- 帰還は個別。table[n] を満たした乗客は各自 D_C→C_A で帰還できる（同便でなくてよい）。
- 同一便の乗客は同じ table[n] を共有するが、帰還タイミングは個別。

⸻

17. Vehicle Capacity Constraints

各車両の定員（vehicle_types.*.capacity）を超えて乗車してはならない。

⸻

18. Vehicle Scheduling Constraints

- 同一車両は同時刻に複数便を実行できない。
- A→C で利用した車両は C 到着後、C→A 便で利用可能（Backhaul：配送＋集荷を同一車両で行いコスト削減）。
- A→Bx も同様に、到着便の車両がその場で Bx→A 便として別の人を乗せて帰れる（同一周回内の交代乗車）。
- A_Bx / A_C 便は必要に応じて複数便発出可。ただし利用可能車両数・休日・滞在時間の制約を受ける。

⸻

19. Objective Function

最小化対象（車両運行費に一本化）:

Minimize
  Σ_(executed trips) duration_hours(trip) × unit_cost(vehicle, trip)

  ここで unit_cost = cost_per_hour（保有車） または rental_cost_per_hour（レンタル車）

運行時間コストは duration × 単価に内包されるため、独立項目としては持たない。

⸻

20. Solver

Solver:
  * OR-Tools
  * CP-SAT

Routing Solver は使用しない。

⸻

21. Open Items（[TBD] 一覧）

構造（スキーマ）側の未決:
- なし（構造は確定。次フェーズの CP-SAT 変数定義・数式化へ移行可能）

インスタンスパラメータ（spec では固定しない／パラメータファイルで与える）:
- §4  D.occupancy.max: 最大収容人数
- §8  保有車両の台数・初期位置、レンタル発出元
- §9  乗客・カテゴリ体系（件数・カテゴリ数は任意）
- §11 各 B 島の初期常駐者
- §12 各 B 島の値（常駐カテゴリ / stay / ride_constraints / segment 所要時間）
- §16 d_stay_rules.table の値（1..最大定員）

確定済（v0.2 で解決）:
- C は通過点（制約なし）
- B 島は B1/B2/B3 の3島を運用
- B2/B3 は B1 と同形（構造は共通、値のみ島ごと・パラメータファイル供給）
- D 滞在 = A_C 便単位の同乗人数で決定（到着時固定）、min のみ、帰還は個別、全乗客が D 可
- d_stay_rules.table は車両定員上限まで列挙
- time_granularity = 1h
- 保有車両は個体(id)単位で初期位置を指定（値はインスタンス）
- spec.md = スキーマ＋例示、実データ = パラメータファイル の二層

⸻

次フェーズ: 上記 [TBD] を埋めつつ、CP-SAT の Decision Variables 定義と制約数式化へ移行する。
