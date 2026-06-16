モノを運ぶための配送最適化問題を解く式を作りたい。制約条件のパラメータは変わるが、経路は変わらない。
経路は、A->B->A->C->D->C->A。

固定経路なので川渡問題の要素があり複数対岸を段階を追って移動しながらその対岸ごとの条件を満たす必要があります。また相乗りできる組み合わせもあるので狼羊の川渡り要素もあります。そして移動コストを最適化するソルバーでもある必要があります。

これをOR-toolsを使って解こうと思っており、条件を示していくので一緒にspec.mdファイルに落とせるよう考えていってください。

========
面白い問題ですね。

現時点の情報を見る限り、これは典型的なVRPではなく、

固定経路上での「乗客配置最適化 + 制約充足 + コスト最小化」問題

に見えます。

また、「人を運ぶ」と言っても、実態は

* 個体(ID)
* 属性(Category)
* 現在位置
* 最終配置

を持つエージェントを状態遷移させる問題です。

⸻

現時点のspec.md案

Fixed Route Transportation Optimization Problem

Route

The route is fixed.

A -> B -> A -> C -> D -> C -> A

No route changes are allowed.

⸻

Transport Objects

Each passenger has:

* Passenger ID
* Category

Example:

PassengerID	Category
P1	Category1
P2	Category2
P3	Category1

⸻

Decision Variables

Location

location[p][s]

Passenger p location after route segment s.

Possible values:

* A
* B
* C
* D
* OnVehicle

⸻

Boarding

board[p][s]

Boolean.

Whether passenger p boards vehicle on segment s.

⸻

Constraints

Fixed Route Constraint

Vehicle route is fixed.

A→B→A→C→D→C→A

⸻

Destination Category Constraint

Examples:

B may contain only Category1.

or

B must contain at least one Category1 and one Category2.

These constraints are parameterized.

⸻

Companion Constraint

For segment:

A→B
B→A

Category1 and Category2 passengers must travel together.

For other segments no such restriction exists.

⸻

Capacity Constraint

Vehicle capacity is configurable.

capacity[s]

Maximum passengers allowed on segment s.

⸻

Objective

Minimize transportation cost.

cost = Σ segment_cost[s]

Segment cost may depend on:

* number of passengers
* passenger categories
* fixed segment cost
* other future parameters

⸻

ここで確認したい重要ポイントがあります。

あなたの「Bにカテゴリ1のみしかいれない」「Bにカテゴリ1とカテゴリ2がいる必要がある」は、

ケースA

問題ごとにどちらか一方

今回:
BにはCategory1のみ
次回:
BにはCategory1とCategory2が必要

なのか、

⸻

ケースB

同時に成立

BにはCategory1のみ
かつ
Category2も必要

なのか。

後者だと矛盾します。

⸻

もう1つ重要です。

「カテゴリ1とカテゴリ2は同時に移動」

は

パターン1

人数が一致

C1が3人
C2が2人
→ 不可

なのか

⸻

パターン2

代表者がいればよい

C1が1人以上乗車
なら
C2も1人以上乗車

なのか。

⸻

さらにOR-Toolsのモデルを決める上で最も重要なのが、

全員の初期位置と最終位置が決まっているか

です。

例えば

P1 Category1 A→B
P2 Category2 A→B
P3 Category1 A→D

のように、

各乗客に

* 出発地
* 目的地

があるのか、

それとも

BにCategory1を2人置きたい
DにCategory2を3人置きたい

という配置問題なのか。

ここが分かると、

* Network Flow
* Set Partitioning
* CP-SAT

のどれが最適かほぼ決まります。現状はCP-SAT寄りに見えています。
==================
ケースCで　Bの対岸はB1, B2, B3とあります。これが制約はパラメータを持って変わるの意味の一例です。

カテゴリ1とカテゴリ2は同時に移動も同様にこの制約もパラメータで持たせる必要があり、B1の場合はカテゴリ1が1名とカテゴリ2が1名
==================
なるほど、かなり見えてきました。

この時点で「川渡り問題」そのものとして考えるより、

“制約駆動型の固定経路輸送最適化”

としてモデル化した方が良さそうです。

つまり、問題ごとに変わるのは

* 対岸(B1, B2, B3…)
* 各対岸の配置条件
* 各区間の同乗条件
* コスト関数

であり、

経路だけが固定です。

⸻

現時点での理解

固定ルート

A -> B -> A -> C -> D -> C -> A

ただし B は

B1
B2
B3
...

という複数の島（対岸）を表す。

⸻

乗客

Passenger
  id
  category

例

P1 Category1
P2 Category1
P3 Category2
P4 Category3

⸻

制約例

B1

Category1 = 1名
Category2 = 1名

⸻

B2

Category1 >= 2名
Category3 = 0名

⸻

B3

Category2 = 3名

⸻

重要な気付き

ここで最適化対象は

「誰をどこへ配置するか」

です。

つまり決定変数は

x[p, island]

となります。

⸻

x[p, island]

1 : 乗客pをその島へ配置
0 : 配置しない

例

x[P1,B1] = 1
x[P2,B2] = 1

⸻

同乗条件

これもパラメータ化する

例えば

ride_constraints:
  - segment: A_B
    categories:
      - Category1
      - Category2
    required_count:
      Category1: 1
      Category2: 1

意味

A→Bの移動では
Category1を1人乗せるなら
Category2も1人乗せなければならない

⸻

対岸条件もパラメータ化

例えば

destination_constraints:
  B1:
    Category1: 1
    Category2: 1
  B2:
    Category1:
      min: 2
  B3:
    Category2:
      exact: 3

⸻

OR-Tools視点

私は最初から

CP-SAT

を推します。

理由は、

* 等式制約
* 不等式制約
* 同乗制約
* 条件付き制約
* カテゴリ組み合わせ制約

が大量に出そうだからです。

Routing Solverは不要です。

経路を最適化しないので。

⸻

次に確認したいのはかなり重要で、

B1に配置された人は、その後Aへ戻るのか？

つまり

A→B→A

のBで降ろしたら固定配置なのか、

それとも

Bで降ろす
↓
後でまた乗せる
↓
Aへ戻す

ことがあるのかです。

ここでモデルが

* 配置問題
* 一時配置問題
* 在庫遷移問題

のどれになるかが決まります。

======================
YAMLでパラメーター化するはあっています。

OR-ToolsでCP-SATを推奨するのもあっています。

B1に配置された人は、その後Aへ戻るのか？はYESです。
あなたの想定どおり、以下であっています。
Bで降ろす
↓
後でまた乗せる
↓
Aへ戻す
===========================
それなら問題の本質がかなり明確になりました。

これはもう「配置問題」ではなく、

有限状態遷移を伴う輸送計画問題

です。

そしてOR-Tools的にはほぼ間違いなく CP-SAT が第一候補です。

⸻

問題構造

例えば

A -> B -> A -> C -> D -> C -> A

を

Step0: A(出発)
Step1: B到着
Step2: A到着
Step3: C到着
Step4: D到着
Step5: C到着
Step6: A到着

として扱います。

⸻

乗客状態

各時点で

location[p,s]

を持ちます。

例

location[P1,0] = A
location[P1,1] = B1
location[P1,2] = A
location[P1,3] = C2

⸻

ただしCP-SATでは

文字列は使えないので

locations:
  A: 0
  B1: 1
  B2: 2
  B3: 3
  C1: 4
  C2: 5
  D1: 6

など整数化します。

⸻

本当に重要な設計

実は location を直接持つより

こちらの方が解きやすいです。

滞在変数

stay[p,node,s]

Boolean

⸻

意味

stay[P1,B1,step1] = 1

なら

Step1時点でP1はB1にいる

⸻

CP-SATはBooleanが得意なのでこちらを推奨します。

⸻

輸送変数

move[p,segment]

⸻

例

move[P1,A_B] = 1

⸻

意味

A→Bの移動時にP1を乗せる

⸻

状態遷移制約

例えば

A→B

で

P1を輸送した場合

stay[P1,A,step0]
→
stay[P1,B1,step1]

が成立

⸻

輸送しなければ

stay[P1,A,step0]
→
stay[P1,A,step1]

となる

⸻

これはフロー保存制約になります。

⸻

対岸条件

YAML

constraints:
  B1:
    Category1:
      exact: 1
    Category2:
      exact: 1

⸻

数式

Σ stay[p,B1,step1]
  where category(p)=Category1
= 1

⸻

同乗条件

YAML

ride_constraints:
  - segment: A_B
    categories:
      Category1: 1
      Category2: 1

⸻

数式

count(Category1,A_B)
=
count(Category2,A_B)

あるいは

count(Category1,A_B) >= 1
⇒
count(Category2,A_B) >= 1

など。

ここは仕様を詰める必要があります。

⸻

コスト

ここがまだ不明

あなたが以前書いた

乗客数によって値段が変わる

だと

例えば

cost:
  A_B:
    0: 0
    1: 100
    2: 150
    3: 180

みたいなテーブルになる可能性があります。

⸻

すると目的関数は

minimize
Σ segment_cost(segment)

になります。

⸻

現時点の私の認識

この問題は

* VRPではない
* Routing Solver不要
* Network Flow単独では不足
* CP-SATが本命

です。

spec.mdとしては次に定義すべきものは、

1. 乗客の初期状態
2. B1/B2/B3等の対岸定義
3. 「同乗制約」の表現力
4. コストテーブル

です。

特に 同乗制約をどこまで一般化したいか が重要です。

例えば将来的に

ride_constraints:
  - segment: A_B
    rule:
      Category1: 2
      Category2: 1
  - segment: C_D
    rule:
      Category3: 3
      Category4: 2

のような「カテゴリ比率制約」まで入れたいのか、それとも単純な「同時乗車必須」だけなのかで、モデル設計が変わります。
===================

カテゴリ比率制約は目的に叶いますが、過剰かもしれません、同時乗車必須だけの場合のパラメータ記述例を教えてください
====================
私も現時点では カテゴリ比率制約までは入れず、「同時乗車必須」だけを表現できる設計 にしておく方が良いと思います。

なぜなら、

Category1: 2
Category2: 1

のような比率制約は後から追加できますが、

まずは

カテゴリ1が乗るなら
カテゴリ2も乗る

という論理制約だけで十分そうだからです。

⸻

パターン1: 最もシンプル

「カテゴリ1とカテゴリ2は同時乗車必須」

ride_constraints:
  - segment: A_B
    categories:
      - Category1
      - Category2

意味

A→B区間で
Category1が1人以上乗る
⇔
Category2が1人以上乗る

CP-SATでは

has_cat1 == has_cat2

になります。

⸻

パターン2: 将来拡張しやすい

私ならこちらを推します。

ride_constraints:
  - segment: A_B
    type: together
    categories:
      - Category1
      - Category2

意味

Category1 と Category2 は
同じ便で存在しなければならない

⸻

将来的に

ride_constraints:
  - segment: A_B
    type: together
    categories:
      - Category1
      - Category2
  - segment: C_D
    type: exclude
    categories:
      - Category3
      - Category4

みたいな拡張が可能になります。

⸻

パターン3: 最も汎用的（おすすめ）

spec.md用ならこれです。

ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2

⸻

将来

ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2
      - type: exclude
        categories:
          - Category3
          - Category4
      - type: require
        source_category: Category5
        target_category: Category6

まで拡張できます。

⸻

OR-Tools側の数式イメージ

カテゴリごとの乗車人数

count_cat1_A_B
count_cat2_A_B

を作る。

存在フラグ

has_cat1_A_B ∈ {0,1}
has_cat2_A_B ∈ {0,1}

を作る。

has_cat1_A_B = 1
⇔ count_cat1_A_B > 0
has_cat2_A_B = 1
⇔ count_cat2_A_B > 0

そして

has_cat1_A_B
=
has_cat2_A_B

を追加。

⸻

spec.mdとしては次のように書けます。

ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2

定義:

type=together
指定されたカテゴリ群は
対象区間において
全て存在する
または
全て存在しない
のいずれかでなければならない。

この定義だと、

* Category1だけ乗る → NG
* Category2だけ乗る → NG
* 両方乗る → OK
* 両方乗らない → OK

となり、CP-SATで非常に扱いやすいです。

==========================
では、同時乗車必須の方で進めることとします、他にも制約がありますが、今伝えた方が良いですか？
==========================
はい、今の段階でできるだけ制約を洗い出した方が良いです。

理由は、CP-SATのモデル設計では後から制約を追加すること自体は簡単ですが、

変数設計（Decision Variables）を後から変更するのは大変だからです。

例えば今のところ私は、

passenger
category
location
segment

を中心に考えていますが、

もし後から

・同じ人はB1とB2を両方訪問してはいけない
・あるカテゴリは必ずDを経由しなければならない
・乗車人数によって料金テーブルが変わる
・特定の人同士は同じ島に滞在してはいけない

などが出てくると、最初から変数を増やした方が綺麗になります。

⸻

私がおすすめする進め方は、

まず制約を全部列挙することです。

まだ数式化しなくてよいので、

# 配置制約
- B1にはCategory1が1人必要
- B2にはCategory2が2人必要
# 同乗制約
- A→BではCategory1とCategory2は同時乗車
# 輸送制約
- 1回の移動で最大4人
# コスト制約
- 人数に応じて料金が変わる
# その他
- ...

のような箇条書きで十分です。

⸻

特に次の種類の制約があるなら早めに知りたいです。

① 人に関する制約

例

P1はB1に行けない
P2は必ずDを経由

⸻

② カテゴリに関する制約

例

Category1はB系のみ
Category2はD系のみ

⸻

③ 島（B1,B2…）に関する制約

例

B1の定員は3人
B2はCategory1のみ

⸻

④ 区間に関する制約

例

A→Bは4人まで
C→Dは2人まで

⸻

⑤ 時系列制約

例

B1訪問後にB2訪問は禁止
Dに行く前にBへ行く必要がある

⸻

⑥ コスト制約

例

人数ごとの料金表
カテゴリごとの料金加算

⸻

この問題は今のところ

「固定ルート上の状態遷移 + 制約充足 + コスト最小化」

として非常に素直にCP-SATへ落とせそうです。

なので、まずは制約を思いつく限り全部並べてください。
その段階で、

* spec.mdの構造
* YAMLスキーマ
* OR-Toolsの変数設計

まで整理できます。
=============================
島（B1,B2…）に関する制約で、B1やB2に入ったら、何時間滞在が必要があり、そして滞在時間が経過して何時間以内の範囲でB->Aに移動しなければいけません

時系列制約で、Bの島いずれを訪問したら必ず、Dを訪問するまではBの島に訪問できません。これは経路パラメータで自明の構造になると思います。

コスト制約でカテゴリによってA->CとC->Aの移動料金が変わります。

島（D）に関する制約で、A->Cの経路であいのりした人数によって、その人たちがDに何時間滞在必要か期間が変わります。

島（B1,B2…）に関する制約で、必ず島には誰かが滞在している必要があるため、A->Bで人が来たら、Bにいた人はB->Aで移動できる形となります。
=============================
かなり重要な制約が出てきました。

実はここで問題の性質が少し変わります。

当初は

誰をどこに配置するか

でしたが、

今は

誰を
いつ
どこへ移動させるか

になっています。

つまり、

時間(Time)が状態変数になる

ので、CP-SATモデルも時間軸を持つ必要があります。

⸻

ここまでで見えてきた制約分類

1. 配置制約

例

location_constraints:
  B1:
    Category1:
      exact: 1

⸻

2. 同乗制約

例

ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2

⸻

3. 滞在時間制約（新規）

これはかなり重要です。

例えば

stay_constraints:
  B1:
    min_stay_hours: 24
    max_return_hours: 48

意味

B1到着
↓
24時間以上滞在
↓
48時間以内にB→Aで帰還

⸻

4. D滞在時間の動的制約（新規）

これは通常の制約より難しいです。

例えば

A→Cで1人で移動
→ D滞在24時間
A→Cで2人で相乗り
→ D滞在36時間
A→Cで3人で相乗り
→ D滞在48時間

のようなもの。

YAMLなら

d_stay_rules:
  based_on_segment: A_C
  table:
    1: 24
    2: 36
    3: 48

⸻

これはCP-SATでは

passenger_count_A_C
↓
lookup
↓
required_stay_D

というテーブル制約になります。

⸻

5. 島の常駐制約（非常に重要）

これが実は川渡り要素の本体ですね。

あなたの説明を数式化すると

Bの島には常に1人以上必要

です。

つまり

B1在住者 = 1人
A→Bで新規到着
到着後
B1在住者がAへ帰還可能

⸻

これは

交代勤務制約

や

トークン受け渡し制約

に近いです。

YAMLだと

occupancy_constraints:
  B1:
    min_occupancy: 1

⸻

CP-SAT的には

occupancy(B1,t)
>=
1

になります。

⸻

実は一番重要な確認

今までの話だと、

私は当初

A→B
↓
B→A
で1往復

だと思っていました。

しかし

B1に24時間滞在
48時間以内に帰還

があるということは、

経路

A→B→A→C→D→C→A

は

1回だけ実行されるわけではない

ですよね？

例えば

Day1
A→B→A→C→D→C→A
Day2
A→B→A→C→D→C→A
Day3
...

のように何度も周回する想定でしょうか？

もしそうなら、

モデルは

segment

ではなく

trip
segment

の二階層になります。

⸻

この確認が非常に重要です。

パターンA

固定ルートを1回だけ実行

⸻

パターンB

固定ルートを何度も周回

（私はこちらだと予想しています）

⸻

ここが確定すると spec.md の根幹が決まります。現時点では滞在時間制約があるため、私は 「周回型スケジューリング問題」 だと考えています。
===================================
はいそうです。パターンBで以下のように何度も周回する想定でしょうか？

Day1
A→B→A→C→D→C→A

Day2
A→B→A→C→D→C→A

Day3
...
================================
なるほど。ここで問題のクラスがほぼ確定しました。

これはもう単純な「輸送最適化」ではなく、

固定ルートを繰り返し運行する時系列スケジューリング最適化問題

です。

OR-Toolsで言うと、

* Routing Solver ❌
* Network Flow単独 ❌
* CP-SAT ◎

という判断は変わりません。

⸻

問題の本質

今まで出てきた制約を整理すると、

空間

A
├─ B1
├─ B2
├─ B3
│
├─ C
│
└─ D

⸻

時間

Trip1
A→B→A→C→D→C→A
Trip2
A→B→A→C→D→C→A
Trip3
A→B→A→C→D→C→A

⸻

状態

各人について

どこにいるか

だけでなく

いつ到着したか

も持つ必要があります。

⸻

例えば

P1
Trip3でB1到着

なら

arrival_time[P1,B1]

を保持します。

⸻

そして

B1:
  min_stay_hours: 24
  max_return_hours: 48

なら

return_time - arrival_time
    >= 24
return_time - arrival_time
    <= 48

になります。

⸻

これまでに見えた主要制約

1. 島配置制約

例

B1:
  Category1:
    exact: 1

⸻

2. 同乗制約

例

ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2

⸻

3. 最低滞在時間

B1:
  min_stay_hours: 24

⸻

4. 帰還期限

B1:
  max_return_hours: 48

⸻

5. 常駐制約

B1:
  min_occupancy: 1

これはかなり重要。

実質

誰かが来ない限り
誰も帰れない

という制約になります。

⸻

6. D滞在時間動的決定

d_stay_rules:
  based_on_segment: A_C

これは状態依存制約。

⸻

7. カテゴリ別料金

例

costs:
  A_C:
    Category1: 100
    Category2: 150
  C_A:
    Category1: 80
    Category2: 120

⸻

spec.mdで追加すべき概念

ここで新たに必要になるのは

運行便

trips:
  - trip_id: 1
  - trip_id: 2
  - trip_id: 3

⸻

イベント時刻

各区間に実時間を持たせる

例

route:
  - segment: A_B
    duration_hours: 2
  - segment: B_A
    duration_hours: 2
  - segment: A_C
    duration_hours: 3
  - segment: C_D
    duration_hours: 1

⸻

そうすると

Trip5のA→B

が

何時に到着するか

を計算できます。

⸻

次に確認したいこと

この問題で一番大きな設計ポイントは、

最適化の終了条件です。

例えば

パターン1

30日間の運行計画を作る

なら

planning_horizon_days: 30

になります。

⸻

パターン2

全員の要求を満たしたら終了

になります。

⸻

パターン3

100回運行する前提

になります。

⸻

CP-SATは有限期間の問題しか解けないので、

spec.mdには必ず

planning_horizon:

が必要です。

ここはかなり重要で、

「何日分の計画を最適化するのか」

を先に決めた方がよいです。そうしないと変数数が確定できません。
==================================
パターン1で30日分や15日分のように指定する前提です。
=================================
それならモデルの骨格はほぼ固まりました。

ここまでの情報からすると、この問題は

有限計画期間（Planning Horizon）における固定ルート巡回輸送スケジューリング問題

として定義できます。

⸻

ここまでで確定した仕様

Route

固定

A -> B -> A -> C -> D -> C -> A

⸻

Planning Horizon

パラメータ

planning_horizon_days: 30

または

planning_horizon_days: 15

⸻

Transport Objects

passengers:
  - id: P001
    category: Category1
  - id: P002
    category: Category2

⸻

Route Schedule

固定ルートだが所要時間はパラメータ

route:
  - segment: A_B
    duration_hours: 2
  - segment: B_A
    duration_hours: 2
  - segment: A_C
    duration_hours: 3
  - segment: C_D
    duration_hours: 1
  - segment: D_C
    duration_hours: 1
  - segment: C_A
    duration_hours: 3

⸻

Island Constraints

B系

islands:
  B1:
    min_occupancy: 1
    stay:
      min_hours: 24
      max_return_hours: 48
    category_requirements:
      Category1:
        exact: 1
      Category2:
        exact: 1

⸻

D系

islands:
  D1:
    stay_rules:
      based_on_segment: A_C
      table:
        1: 24
        2: 36
        3: 48

⸻

Ride Constraints

ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2

⸻

Cost Model

costs:
  A_C:
    Category1: 100
    Category2: 150
  C_A:
    Category1: 120
    Category2: 180

⸻

CP-SAT的に重要な状態

今の段階で最低限必要な状態変数は

Location

location[p,t]

乗客pが時刻tにどこにいるか

⸻

Movement

move[p,trip,segment]

その便で移動したか

⸻

Arrival Time

arrival[p,island]

⸻

Departure Time

departure[p,island]

⸻

Occupancy

occupancy[island,time]

⸻

spec.mdでまだ未確定な重要事項

これから確認したいのは主に以下です。

① B1/B2/B3は独立か

例えば

A→B

で到着した際、

B1
B2
B3

のどれに行くかを選択できるのか。

それとも

P1はB1固定
P2はB2固定

なのか。

⸻

② B島間移動

例えば

B1 → B2

は可能か。

私の予想では不可。

⸻

③ DもD1,D2,D3があるのか

現在は

D

しか出てきていません。

⸻

④ 人数制限

輸送手段の定員

capacity:
  A_B: 4
  B_A: 4

のような制約は存在するか。

⸻

⑤ コスト

非常に重要です。

今は

カテゴリ毎料金

が出ていますが、

最初に話されていた

乗客数によって値段が変わる

もありました。

つまり

A→C
1人: 100
2人: 180
3人: 240

のような料金テーブルなのか、

カテゴリごと単価

なのか、

あるいは両方なのか。

⸻

ここまで来ると、次の段階では「制約一覧」ではなく、実際のspec.mdのYAMLスキーマを設計するフェーズに入れそうです。特に⑤は目的関数に直結するので、早めに固めたいところです。
============================
① B1/B2/B3は独立かについては、乗客P1やP2は、自身のカテゴリでB1,B2に行けるかは決まります。
これは、以下の制約で自然と決まるはずです。つまり、segment: A_Bではなく、segment: A_B1やsegment: A_B2のように複数作る形を想定します。
ride_constraints:
  - segment: A_B
    rules:
      - type: together
        categories:
          - Category1
          - Category2

② B島間移動はB1 → B2のような移動は不可です。

③ DもD1,D2,D3があるのかは、Dのみです。

④ 人数制限はA->C, C->Aにはあります。

⑤ コストはミニバンか、トラックかの料金です。要は何人まで同時に運べるの制約とコストが違います、また移動時間でも料金がかわります。A->CとC->Aでミニバンやトラックは行き来するので、上手にA->Cで人をおろし、それと同時にC->Aへ移動したい人を乗せると、ミニバンやトラックの運送本数を減らすことができるのでコストが下がります
==========================
非常に重要な情報です。

ここで問題の定式化がかなり明確になりました。

実は私はここまで、

人の移動を最適化

だと思っていましたが、

本当の目的関数は

車両運行コスト最小化

ですね。

つまり、

* 人は輸送対象
* 車両がコスト発生主体

です。

これはモデル設計に大きく影響します。

⸻

問題の本質（最新版）

これは

固定ルート・固定ダイヤの車両運用最適化付き輸送スケジューリング問題

です。

⸻

B系の扱い

先ほどの

ride_constraints:
  - segment: A_B

は少し修正した方が良いです。

あなたの説明だと実際は

ride_constraints:
  - segment: A_B1
    rules:
      - type: together
        categories:
          - Category1
          - Category2
  - segment: A_B2
    rules:
      - type: together
        categories:
          - Category3
          - Category4

のようになります。

つまり、

Bは抽象ノードではなく、

A -> B1
A -> B2
A -> B3

という独立した行き先です。

⸻

ネットワーク構造

現在の理解だと

A
├─ B1
├─ B2
├─ B3
│
├─ C
│
└─ D

です。

そして

B1 → B2
B2 → B3

は存在しない。

⸻

人の属性

今後はカテゴリだけでは足りず、

passengers:
  - id: P001
    categories:
      - Category1
      - Category4

のような

「複数カテゴリ所属」

の可能性があるかは確認したいですが、

現状は

category: Category1

で十分そうです。

⸻

最重要ポイント

車両を明示的にモデル化する

これまでは

move[p, trip]

を考えていました。

しかし実際には

vehicles:
  - type: minivan
    capacity: 4
    cost_per_hour: 100
  - type: truck
    capacity: 10
    cost_per_hour: 180

が必要になります。

⸻

Decision Variablesが変わる

今後の主変数は

assign(passenger, vehicle_trip)

です。

⸻

例えば

Trip17
A→C
minivan #3

に

P1
P2
P3

を乗せる。

⸻

コスト関数

おそらく

minimize
Σ vehicle_cost

になります。

⸻

例

vehicle_types:
  minivan:
    capacity: 4
    hourly_cost: 100
  truck:
    capacity: 10
    hourly_cost: 180

⸻

A→C

所要時間

duration_hours: 3

なら

minivan
100 × 3 = 300

⸻

トラック

180 × 3 = 540

⸻

が発生。

⸻

A→C と C→A の同時最適化

ここが面白いです。

あなたが言う

A→Cで人を降ろし、同時にC→Aへ帰る人を乗せる

は実質

Backhaul Optimization

です。

物流業界では非常によくある。

⸻

つまり

A→C
空車率を下げる

が目的。

⸻

例えば

A→C
P1,P2を輸送
↓
到着
↓
C→A
P3,P4を回収

なら

往路車両
復路車両

を別々に出す必要がない。

⸻

spec.mdに追加すべき概念

vehicle_types:
  minivan:
    capacity: 4
    cost_per_hour: 100
  truck:
    capacity: 10
    cost_per_hour: 180

⸻

route_segments:
  A_C:
    duration_hours: 3
    allowed_vehicle_types:
      - minivan
      - truck
  C_A:
    duration_hours: 3
    allowed_vehicle_types:
      - minivan
      - truck

⸻

私が今懸念している点

実は、

A→C
C→A

の車両は

同じ物理車両ですか？

つまり

Truck#1
A→C
↓
到着
↓
C→A

なのか、

それとも

A→C用トラック
C→A用トラック

を別に考えるのか。

⸻

もし前者なら、

モデルは

Vehicle Routing

ではないものの、

Vehicle Resource Scheduling

要素が入り、

CP-SATで

vehicle_location[v,t]

まで管理する必要があります。

これは設計上かなり大きな分岐点です。

なので次に確認したいのは、

ミニバンやトラックは実在する有限台数の車両なのか、それとも必要なだけ使える仮想車両なのか

です。

ここでモデルの難易度が1段階上がるかどうかが決まります。
==============================
ミニバンやトラックは実在する有限台数の車両です、レンタルなので追加は可能です。

人を下ろして、載せるで配達して、集荷して帰ると同じ構造なので以下はあっています。
Truck#1
A→C
↓
到着
↓
C→A
==================================
素晴らしいです。

ここで問題のクラスがほぼ確定しました。

これはもう単なる「輸送計画」ではなく、

有限車両を用いた固定ルート巡回型 Pickup & Delivery Scheduling Problem with Time Constraints

です。

ただし一般的なPickup & Delivery Problem(PDP)より簡単な部分もあります。

なぜなら、

* ルートは固定
* 訪問順も固定
* B⇔B間移動なし
* Dは単一ノード

だからです。

一方で、

* 滞在時間制約
* 常駐制約
* 同乗制約
* 車両容量
* 車両台数
* 車両運行コスト

があるため、CP-SATが適しています。

⸻

現時点でのモデル像

車両

vehicle_types:
  minivan:
    capacity: 4
    hourly_cost: 100
  truck:
    capacity: 10
    hourly_cost: 180

⸻

実車両

vehicles:
  - id: VAN001
    type: minivan
  - id: VAN002
    type: minivan
  - id: TRUCK001
    type: truck

⸻

レンタル

追加車両は固定IDではなく、

vehicle_types:
  minivan:
    rental:
      enabled: true
      hourly_cost: 150
  truck:
    rental:
      enabled: true
      hourly_cost: 250

のような形が良いと思います。

⸻

非常に重要な設計変更

先ほどまでは

move(passenger, trip)

が主変数でした。

しかし今は

assign(passenger, vehicle, segment, trip)

になります。

⸻

例

assign(
  P001,
  TRUCK001,
  A_C,
  Trip17
)
=
1

⸻

車両状態も管理する

例えば

vehicle_location[v,t]

⸻

例

Truck001
08:00 A
11:00 C
14:00 A

⸻

これは重要です。

なぜなら

Truck001
A→C

の直後に

Truck001
A→C

を実行できないからです。

⸻

Backhaul最適化

あなたが説明していた

A→Cで配送
C→Aで集荷

はまさにこれです。

⸻

理想

Truck001
A→C
P1,P2輸送
↓
到着
↓
C→A
P3,P4回収

⸻

非効率

Truck001
A→C
P1,P2
↓
空車で帰る
↓
別便
C→A
P3,P4

⸻

目的関数は自然に

運行車両数最小
+
総運行時間最小
+
レンタル費最小

になりそうです。

⸻

spec.mdに追加したい項目

Vehicle Fleet

fleet:
  owned:
    - type: minivan
      quantity: 2
    - type: truck
      quantity: 1
  rental:
    minivan:
      enabled: true
    truck:
      enabled: true

⸻

Vehicle Costs

vehicle_costs:
  minivan:
    cost_per_hour: 100
  truck:
    cost_per_hour: 180
  rental_multiplier: 1.5

⸻

次に確認したいこと

実はこれがかなり重要です。

B系の島について、

例えば

B1

に現在

Category1 1人

が滞在中。

⸻

そして

A→B1

で

Category1 1人

が到着。

⸻

このとき

到着した人

と

帰る人

は同じ便で交換可能ですか？

つまり

A→B1

で到着した車両が、

その場で

B1→A

として別の人を乗せて帰る想定でしょうか？

それとも

到着
↓
一定時間経過
↓
次便で帰還

でしょうか。

この違いは、

車両が島で待機するのか即折り返すのか

という非常に大きなモデル差になります。

私の予想では前者（同一周回内で交代乗車）に見えています。そうであれば、B系は「常駐要員の交代勤務問題」にかなり近くなります。
============================
A→B1で到着した車両が、B1→Aとして別の人を乗せて帰る想定であっています
==========================
了解です。

この情報はモデル設計上かなり重要です。

⸻

B系の実態

当初私は

A→B1
↓
滞在
↓
別便
↓
B1→A

を想定していましたが、

実際は

A→B1
↓
到着
↓
人を降ろす
↓
人を乗せる
↓
B1→A

ですね。

つまりB系は、

配送 + 集荷が同一訪問で発生するノード

です。

物流用語だと、

Delivery + Pickup

になります。

⸻

B1の状態

例えば

Trip 15
B1在住:
  P10(Category1)

⸻

到着

A→B1
P20(Category1)

⸻

交代

降車
P20
乗車
P10

⸻

出発

B1→A
P10

⸻

結果

B1在住:
  P20

⸻

非常に良い性質

この構造だと、

B系については

occupancy(B1,t) >= 1

という時系列制約より、

もっと強い形で

occupancy_before
=
occupancy_after

が成立します。

⸻

つまり

B1最低1名

なら

実際には

交代制約

になります。

⸻

spec.mdに追加したい制約

例えば

occupancy_constraints:
  B1:
    min_occupancy: 1
    replacement_required: true

⸻

意味

帰還者数 <= 到着者数

⸻

あるいは

occupancy_after
>=
occupancy_before

⸻

重要な観察

これによりB系は

滞在者プール

になります。

つまり

B1

には

時系列で

P1
↓
P2
↓
P3
↓
P4

と交代勤務する。

⸻

D系との違い

B系

交代前提

⸻

D系

交代不要

（今の理解）

⸻

つまり制約の種類が異なる。

⸻

ここまでのノード分類

私は spec.md に以下を入れると思います。

node_types:
  B:
    type: staffed_site
  D:
    type: temporary_site

⸻

B系

B1:
  type: staffed_site
  min_occupancy: 1
  stay:
    min_hours: 24
    max_hours: 48

⸻

D

D:
  type: temporary_site

⸻

現時点で未解決な最大論点

実は今一番重要なのは

B1の滞在時間制約が誰にかかるか

です。

例えば

P10
B1に到着
Day1 08:00

⸻

制約

min_stay_hours: 24

⸻

これは

P10自身が
24時間滞在

なのか、

それとも

B1に誰かが
24時間滞在

なのか。

⸻

私は前者だと理解しています。

つまり

P10
到着
↓
24時間未満
↓
帰還不可

です。

もしそうなら、

B1の交代制約と滞在時間制約が組み合わさり、

かなり面白いCP-SATモデルになります。

⸻

ここまでの理解では、B系の人は

「島に到着 → 最低滞在時間経過 → 交代要員が来たら帰還可能 → ただし最大滞在時間までに帰還」

というライフサイクルを持つ、と解釈しています。

この認識で合っていますか？もし合っていれば、次は spec.md の エンティティ定義（Passenger, Vehicle, Site, Trip） を固める段階に入れます。
===============================
はい、前者のP10自身が24時間滞在の例であっています
============================
素晴らしいです。ここまで来ると、かなり綺麗にモデル化できます。

私の認識では、この問題は次のように整理できます。

⸻

問題クラス

固定ルート巡回型 Workforce Rotation & Transportation Scheduling Problem

特徴:

* 固定経路
* 有限計画期間（15日、30日など）
* 車両運用最適化
* 人員交代制約
* 滞在時間制約
* 同乗制約
* 配送＋集荷（Backhaul）
* コスト最小化

⸻

エンティティ

Passenger

passengers:
  - id: P001
    category: Category1

⸻

Vehicle Type

vehicle_types:
  minivan:
    capacity: 4
    cost_per_hour: 100
  truck:
    capacity: 10
    cost_per_hour: 180

⸻

Vehicle

vehicles:
  - id: VAN001
    type: minivan
  - id: TRUCK001
    type: truck

⸻

Site

sites:
  A:
    type: hub
  B1:
    type: staffed_site
  B2:
    type: staffed_site
  D:
    type: temporary_site

⸻

B系サイトの制約

ここがこの問題の中心です。

sites:
  B1:
    type: staffed_site
    occupancy:
      min: 1
    stay:
      min_hours: 24
      max_hours: 48
    category_requirements:
      Category1:
        exact: 1
      Category2:
        exact: 1

⸻

意味

到着
↓
24時間未満
↓
帰還不可
24時間以上
↓
交代要員がいれば帰還可能
48時間超過
↓
違反

⸻

交代制約

これを独立して表現した方が良さそうです。

replacement_rules:
  B1:
    replacement_required: true

⸻

意味

帰還者数 <= 到着者数

あるいは

occupancy_after
>=
occupancy_before

⸻

同乗制約

ride_constraints:
  - segment: A_B1
    rules:
      - type: together
        categories:
          - Category1
          - Category2

⸻

意味

Category1だけ乗車 → NG
Category2だけ乗車 → NG
両方乗車 → OK

⸻

Dの特殊制約

sites:
  D:
    stay_rules:
      based_on_segment: A_C
      table:
        1: 24
        2: 36
        3: 48

⸻

意味

A→Cで何人相乗りしたか
↓
D滞在時間決定

⸻

車両コスト

ここもかなり重要です。

vehicle_types:
  minivan:
    capacity: 4
    cost_per_hour: 100
  truck:
    capacity: 10
    cost_per_hour: 180

⸻

区間

segments:
  A_C:
    duration_hours: 3
  C_A:
    duration_hours: 3

⸻

運行コスト

cost
=
duration
×
vehicle_hourly_cost

⸻

Decision Variables（ほぼ確定）

CP-SATで必要になりそうなのは

assign_passenger_to_vehicle

⸻

passenger_location

⸻

vehicle_trip_execution

⸻

site_occupancy

⸻

arrival_time

⸻

departure_time

⸻

まだ聞いておきたい制約

この問題で最後に大きそうなのは以下です。

A→B1の便数

例えば

1日1便

なのか

必要なら何便でも出せる

なのか。

⸻

A→Cの便数

1日1便固定

なのか

トラックが空いていれば複数便可

なのか。

⸻

車両

車両は24時間稼働可能

なのか

1日8時間まで

なのか。

⸻

乗客

全員最初はAにいる

のか、

計画開始時点で
B1やDにも既に人がいる

のか。

⸻

特に最後の「初期状態」はCP-SATモデルの初期条件になるので、spec.mdでは必須項目です。

現時点では、この問題はかなり綺麗にCP-SATへ落とせる見込みがあります。むしろ難しいのはOR-Tools実装より、YAMLスキーマをどこまで汎用化するかだと思います。
===================================
A→B1の便数は必要なら何便でも出せますが、休日は動けません。
A→Cの便数はトラックが空いていれば複数便可です。
計画開始時点でB1やDにも既に人がいるです。
==================================
なるほど。ここでかなり重要な仕様が3つ追加されました。

特に

計画開始時点でB1やDにも既に人がいる

これはモデルの初期条件として非常に重要です。

⸻

現時点の問題定義

この問題は実質的に

有限期間の人員ローテーション付き輸送計画最適化

です。

そしてCP-SATモデルは

状態遷移問題
+
リソーススケジューリング
+
コスト最小化

として構築できそうです。

⸻

新たに確定した仕様

営業日制約

休日は運行不可

YAML例

calendar:
  holidays:
    - 2026-01-01
    - 2026-01-02
  weekends_closed: true

または

calendar:
  operating_days:
    - Mon
    - Tue
    - Wed
    - Thu
    - Fri

こちらの方が実装しやすいかもしれません。

⸻

B系便

routes:
  A_B1:
    unlimited_trips: true
  B1_A:
    unlimited_trips: true

ただし

車両
休日
滞在時間

の制約は受ける。

⸻

A⇔C便

routes:
  A_C:
    unlimited_trips: true
  C_A:
    unlimited_trips: true

ただし

利用可能車両数

が上限。

⸻

初期状態

これは spec.md に独立セクションを作った方が良いです。

例えば

initial_state:
  passengers:
    - passenger_id: P001
      location: B1
      arrived_at: 2026-01-01T09:00
    - passenger_id: P002
      location: D
      arrived_at: 2026-01-02T15:00
    - passenger_id: P003
      location: A

⸻

なぜ arrived_at が必要か

例えば

B1:
  stay:
    min_hours: 24
    max_hours: 48

なら

P001が

昨日到着

なのか

40時間前到着

なのかで

帰還可否が変わるからです。

⸻

実はここで重要な設計判断

私は spec.md の内部表現として

イベント駆動

よりも

時間離散化

を推奨します。

例えば

1時間単位

や

30分単位

で計画期間を刻む。

⸻

理由

あなたの制約は

24時間以上
48時間以内

のような時間窓制約が多い。

CP-SATでは

時刻インデックス

で扱う方が実装しやすい。

⸻

今後出てきそうな制約

まだ確認したいものがあります。

B1のカテゴリ制約

例えば

B1:
  category_requirements:
    Category1:
      exact: 1

は

常時成立

なのか

交代タイミングだけ成立

なのか

⸻

例えば

B1には常にCategory1が1人必要

なのか、

交代後の状態だけ見ればよい

なのか。

⸻

Dの人数制限

例えば

D:
  max_occupancy: 5

のような制約はあるか。

⸻

同一人物の訪問制限

例えば

P001はB1だけ

なのか、

次回はB2へ行っても良い

のか。

⸻

私なら次に spec.md を作る

ここまでの情報量になると、

もう制約収集フェーズというより

spec.md の章立てを確定するフェーズ

です。

私なら次の構成にします。

1. Problem Overview
2. Planning Horizon
3. Calendar
4. Sites
   - A
   - B1..Bn
   - D
5. Routes
6. Vehicle Types
7. Vehicles
8. Passengers
9. Initial State
10. Constraints
    - Occupancy
    - Stay Duration
    - Replacement
    - Ride Together
    - Vehicle Capacity
    - Calendar
11. Cost Model
12. Objective Function
13. Solver Requirements

そしてこの段階で、YAMLスキーマのドラフトを作り始めても良いレベルまで来ています。特に「初期状態」と「休日」が入ったことで、CP-SATの変数設計がほぼ見えてきました。
========================
営業日制約で、曜日だけでは柔軟性が弱いです。
またinitial_state:は独立セクションはそのとおりです。

B1には常にCategory1が1人必要です。
なお、交代後の状態だけ見ればよいの質問意味はわかりません、必要なら私に質問してください。

Dの人数制限はありますが、これは今時点、最大人数は未定です。

同一人物の訪問制限は繰り返しですが、A->B1, A->B2の経路で行ける場所が決まっています
=============
ありがとうございます。かなり仕様が見えてきました。

まず、いくつか認識を修正します。

⸻

営業日制約

私も曜日指定だけでは不足だと思います。

この問題は休日だけでなく、

* 臨時運休日
* 車両ごとの運休
* 島ごとの受入停止
* 特定経路のみ停止

などが将来入りそうです。

なので曜日ではなく、

calendar:
  operating_windows:
    - start: 2026-01-01T00:00:00
      end: 2026-01-31T23:59:59
  blackout_periods:
    - start: 2026-01-10T00:00:00
      end: 2026-01-11T23:59:59
    - start: 2026-01-17T00:00:00
      end: 2026-01-17T23:59:59

のような時間窓ベースが良いと思います。

あるいはもっと単純に

calendar:
  holidays:
    - 2026-01-10
    - 2026-01-11
    - 2026-01-17

でもよいです。

⸻

B1のCategory制約

ここは理解できました。

例えば

B1:
  category_requirements:
    Category1:
      min: 1

なら、

全ての時刻で成立

です。

つまり

08:00  Category1=1
09:00  Category1=1
10:00  Category1=1
...

でなければならない。

⸻

先ほどの質問の意図

私が確認したかったのは、

例えばB1で

現在
P10(Category1)

が滞在中。

⸻

そこへ

P20(Category1)

が到着。

⸻

交代時に

P10帰還
P20残留

なら問題ありません。

⸻

しかしもし

P10帰還
P20も帰還

となる瞬間が許されるか？

という確認でした。

今の説明からすると、

許されません。

つまり

Category1人数(B1,t)
>= 1

が常時成立です。

これは理解しました。

⸻

D人数制限

未定で問題ありません。

spec.mdには

D:
  occupancy:
    max: null

のようにプレースホルダを置けます。

⸻

同一人物の訪問制限

ここは重要です。

私の理解では、

passengers:
  - id: P001
    allowed_sites:
      - B1
  - id: P002
    allowed_sites:
      - B2

のような制約ですね。

つまり

P001
A→B2

は不可能。

⸻

ここで新たな設計提案

これまで

ride_constraints:

としていましたが、

実はB1/B2への適格性も同じ階層で扱うと分かりにくいです。

私は

passenger_rules:

を独立させます。

例:

passenger_rules:
  P001:
    allowed_sites:
      - B1
  P002:
    allowed_sites:
      - B2

あるいはカテゴリ単位なら

category_rules:
  Category1:
    allowed_sites:
      - B1
  Category2:
    allowed_sites:
      - B2

⸻

現時点で私が一番知りたいこと

実はまだ最大の未知数があります。

B1の滞在時間

例えば

B1:
  stay:
    min_hours: 24
    max_hours: 48

とします。

⸻

P10が

Day1 08:00 到着

⸻

Day2 08:00

24時間経過

⸻

しかし

交代要員が来ない

⸻

Day3 08:00

48時間経過

⸻

この場合、

パターンA

制約違反

として解なしになる

⸻

パターンB

レンタル車両増便

などを使って必ず帰還させる

⸻

パターンC

違反コスト

を払って延長を許可

⸻

どれを想定していますか？

これはCP-SATでいう

Hard Constraint（絶対守る）

なのか

Soft Constraint（違反ペナルティ）

なのかを決める非常に重要な点です。

私の予想では、業務要件的にはHard Constraintに見えています。そうするとソルバーは「48時間超過が起きない計画」だけを探索することになります。
======================

はい、いかにしても良いです。

passenger_rules:
  P001:
    allowed_sites:
      - B1
  P002:
    allowed_sites:
      - B2

また、B1の滞在はHard Constraintです
====================
了解です。

ここまでの情報を踏まえると、設計方針としてかなり明確になりました。

現時点で確定した重要事項

1. サイト適格性は Passenger 単位

カテゴリ単位ではなく、

passenger_rules:
  P001:
    allowed_sites:
      - B1
  P002:
    allowed_sites:
      - B2
  P003:
    allowed_sites:
      - B1
      - B3

です。

これは非常に良い設計です。

理由は将来的に

Category1だけどB2には行けない

のような例外を吸収できるためです。

⸻

2. 滞在時間は Hard Constraint

つまり

stay:
  min_hours: 24
  max_hours: 48

なら

24時間未満で帰還 → 不可
48時間超過 → 不可

です。

CP-SAT的には

arrival_time + min_hours <= departure_time
departure_time <= arrival_time + max_hours

をそのまま制約として入れます。

⸻

3. Bサイトは常駐制約あり

例えば

B1:
  occupancy:
    min: 1
  category_requirements:
    Category1:
      min: 1

なら、

全時刻で

occupancy(B1,t) >= 1
category_count(B1,Category1,t) >= 1

が成立します。

⸻

私が spec.md に追加したい新しい章

ここまで来ると「制約」だけでなく、

制約の強度(Hard/Soft) を明示した方が良いです。

例えば

constraints:
  stay_duration:
    type: hard
  occupancy:
    type: hard
  ride_together:
    type: hard

将来的に

constraints:
  preferred_rotation:
    type: soft
    penalty: 100

も追加できます。

⸻

モデルの中核

私なら Decision Variables を以下のレベルまで先に定義します。

Passenger Location

location[p, t]

時刻 t における乗客 p の所在地

⸻

Vehicle Assignment

assign[p, vehicle_trip]

乗客 p をどの車両便へ割り当てるか

⸻

Vehicle Execution

execute_trip[v, trip]

車両 v がその便を実行するか

⸻

Arrival / Departure

arrival[p, site]
departure[p, site]

⸻

Site Occupancy

occupancy[site, t]

⸻

ここから先に出てきそうな制約

まだ出てきていないですが、業務的には高確率で出るものがあります。

ローテーション公平性

例えば

P001ばかりB1へ行く

を防ぎたいか。

⸻

連続勤務制限

例えば

30日間で
B系滞在は最大10日

のようなもの。

⸻

車両稼働時間

例えば

Truck001
1日12時間まで

⸻

車両整備日

例えば

Truck001
毎週日曜停止

⸻

次のフェーズ

私はもう

制約収集フェーズ → モデル設計フェーズ

へ移って良い段階だと思います。

次にやるべきは、

制約を追加で洗い出しながら、

1. エンティティ定義
2. YAMLスキーマ
3. CP-SAT変数一覧
4. 目的関数

を spec.md のドラフトとして固めることです。

現時点では、この問題は Routing Solver を一切使わず、OR-Tools の CP-SAT を中心に組み立てるのが最も自然な構造になっています。
=================================
はい、制約収集フェーズ → モデル設計フェーズへ移って良いです。
spec.md のドラフトとして固めてください。

なおspec.mdと同時にspec.mdの背景になる情報もmemory.mdも作成してください。

memory.mdはあなたとの会話セッションを終えて、新たなセッションでメモリ情報として利用した方が、あなたとの会話が再開しやすくなるためです。
=============================================
良いと思います。

この段階では「実装を始めるための仕様書」ではなく、

* spec.md = ソルバー仕様
* memory.md = 問題背景・設計意図・会話で決まった事項

の2層構造にするのが良いです。

⸻



⸻

