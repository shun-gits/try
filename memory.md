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
次フェーズ = CP-SAT の Decision Variables 定義と制約数式化。

⸻

この2ファイルをベースに、次のフェーズでは CP-SATの変数定義（Decision Variables）と制約数式化 を起こしていく。
未決事項は [TBD] を埋めつつ進める方針（TBD 付きで構造を先に固める）。

