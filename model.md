model.md（ドラフト v0.3）

Fixed Route Workforce Rotation — CP-SAT Formulation

本書は spec.md v0.3 を前提に、OR-Tools CP-SAT の決定変数・制約・目的関数を数式化する。
時間は 1h 粒度の整数で表す（spec §2）。参考実装は route_opt/。

変更履歴
- v0.1 → v0.2: 乗客が固定ルートを期間内で「周回」し（B滞在→A→D滞在→A→再びB滞在…）、
  B も D も複数回訪問、同一人が B/D 両方を担う、と確定。
  これに伴い「乗客=訪問列（visit sequence）の状態遷移」層を追加し、
  単一の arrB[p,k] 等を「訪問スロット m 単位」へ一般化。
- v0.2 → v0.3（実装と同期）:
  - 必須ローテーション制約 (4.11) を追加（spec §17。B/D 交互順）。
  - spec の節番号繰り下げ（旧§17/18/19 → §18/19/20）を本文参照に反映。
  - §7 を更新（Q4 は実装で解決）、§8「実装ステータス＆ローリングホライズン定式化」を追加。

⸻

0. モデリング方針（最重要の設計判断）

本モデルは2層構造:
  (L1) トリップ層    : 車両ディスパッチ（コスト発生主体）。アーム別に往復トリップを列挙。
  (L2) 乗客訪問層    : 各乗客の訪問列（B/D の滞在を時系列に並べた状態遷移）。
両層を「乗車割当」変数で結合する。

0.1 トリップ層：アーム別「往復トリップ列挙」方式

固定ルート A→Bx→A→C→D→C→A は、物理的に2種の往復ディスパッチに分解できる。

- B-arm(k)  : A → k → A            （k ∈ {B1,B2,B3}、所要 din[k]+dout[k]）
  到着便で交代者を運び、同一便・同一車両で帰還者を乗せて A へ戻る（spec §19）。
- CD-arm    : A → C → D → C → A     （A_C+C_D+D_C+C_A = 3+1+1+3 = 8h）
  往路で D 行きを運び、復路で D 帰還者を回収する Backhaul（spec §19）。
  C は通過点（乗降・滞在なし、spec §4）。実乗降は A / D / 各 B 島でのみ発生。

各アームに候補トリップ j = 1..J[a] を有限列挙し、used のものだけ運行（便数可変を上限内で表現）。

0.2 乗客訪問層：周回を「訪問スロット列」で表す

各乗客 p に順序付き訪問スロット m = 1..M を持たせる。各スロットは
「未使用」または「ある B 島滞在」または「D 滞在」のいずれか。
スロットは時系列順（スロット間は必ず A に戻る＝ルート構造より自明）。
これにより、同一乗客が B→D→B→… と期間内で繰り返す周回を表現する。
M は乗客あたり最大訪問数（パラメータ。下限滞在から H/最短ループで上限導出）。

0.3 時間の扱い：トリップ・訪問スロットに整数時刻変数を持たせる（location[p,t] は持たない）

トリップ j に出発時刻 dep[a,j]∈{0..H}、訪問スロット m に到着 a[p,m]/出発 d[p,m]∈{0..H}。
滞在制約は時刻変数間の不等式で表す。時刻離散グリッド上の location[p,t] は使わない。

0.4 常駐・カテゴリ制約は「イベント時点」で評価（∀t を回避）

B 島の占有は B-arm トリップ完了時のみ変化（乗降が便単位で離散）。
「全時刻 ≥ min」は「初期 ≥ min ＋ 各トリップ完了直後 ≥ min」に等価（§4.6）。

H = 計画期間総時間（spec §2 の例で約 744h）。

⸻

1. Sets / Indices

P            乗客集合, p ∈ P
Cat          カテゴリ集合, c ∈ Cat（数任意, spec §9）
K            常駐サイト(B島) = {B1,B2,B3}, k ∈ K
S            訪問可能サイト = K ∪ {D}, s ∈ S
Vown / Vrent 保有車両（個体）/ レンタルプール（上限 Nrent）
V            = Vown ∪ Vrent
Arms         = { B-arm(k):k∈K } ∪ { CD-arm }
Trips        = { (a,j) : a∈Arms, j=1..J[a] }
M            乗客あたり訪問スロット数, m = 1..M

派生: cat(p), allowedB(p)⊆K（spec §10）, type(v)

⸻

2. Parameters（値はパラメータファイル供給：spec の二層方針）

H, Hol⊆{0..H}（休日時間帯, spec §3）
din[k],dout[k]; A_C,C_D,D_C,C_A 所要（spec §6,§12）
cap[type], cost[type], rcost[type]（spec §7）
occmin[k], catreq[k][c], smin[k], smax[k], together[k]（spec §12,13,15）
dtable[n]  n=1..max定員（spec §16）
init_loc(p), init_arr(p)（spec §11）; init_veh(v)（spec §8）; Dmax（spec §4）

⸻

3. Decision Variables

3.1 トリップ層
used[a,j] ∈ {0,1}
assignV[a,j,v] ∈ {0,1}            Σ_v assignV[a,j,v] = used[a,j]
dep[a,j] ∈ {0..H}                 A 出発時刻（used=0 で無効化）
（各ノード到着時刻は dep + 所要の加算で導出）

3.2 乗客訪問層
at[p,m,s] ∈ {0,1}                 スロット m が サイト s の滞在（s∈S）
                                  Σ_s at[p,m,s] ≤ 1（0 なら未使用スロット）
a[p,m], d[p,m] ∈ {0..H}           スロット m の到着・出発時刻
（未使用スロットは末尾に詰める：Σ_s at[p,m,s] ≥ Σ_s at[p,m+1,s]）

3.3 結合（訪問スロット ⇔ トリップ）
inB[p,m,k,j] ∈ {0,1}              スロット m(=B島 k 滞在) の到着を B-arm(k) トリップ j で実現（A→k）
outB[p,m,k,j] ∈ {0,1}            スロット m の出発を B-arm(k) トリップ j で実現（k→A）
toD[p,m,j] ∈ {0,1}                スロット m(=D 滞在) の到着を CD-arm トリップ j で実現（A→C→D）
frD[p,m,j] ∈ {0,1}                スロット m の出発を CD-arm トリップ j で実現（D→C→A）

3.4 派生カウント
nAC[j] = Σ_{p,m} toD[p,m,j]       CD-arm トリップ j の A_C 同乗人数（D滞在決定, spec §16）

⸻

4. Constraints

4.1 訪問スロットの整合
(F1) 適格性: at[p,m,k]=1 ⇒ k∈allowedB(p)（spec §10）。D は全員可（spec §4）。
(F2) スロット順序: d[p,m] ≤ a[p,m+1]（スロット間は A に戻ってから次へ）。
(F3) 各使用スロットは到着・出発便を各1つ持つ:
     at[p,m,k]=1 ⇒ Σ_j inB[p,m,k,j]=1 かつ Σ_j outB[p,m,k,j]=1
     at[p,m,D]=1 ⇒ Σ_j toD[p,m,j]=1 かつ Σ_j frD[p,m,j]=1
(F4) 未使用スロットは結合変数すべて 0。
(F5) 自己交代の禁止: 同一乗客は同一トリップ j で入域と出域を兼ねられない。
     Σ_m inB[p,m,k,j] + Σ_m outB[p,m,k,j] ≤ 1。
(F6) A 往復の物理連続性: スロット m を出て A へ戻る時刻 ≤ スロット m+1 で A を発つ時刻。
     a[p,m+1] − din(m+1) ≥ d[p,m] + dout(m)   （atused[p,m+1]=1 のとき）。
     ※(F5)(F6) が無いと「同一便で自分を交代」する非物理解が出る（実装で確認・修正済）。

4.2 時刻の連動（結合変数 → スロット時刻）
(T1) inB[p,m,k,j]=1 ⇒ a[p,m] = dep[B-arm(k),j] + din[k]
(T2) outB[p,m,k,j]=1 ⇒ d[p,m] = dep[B-arm(k),j] + din[k]   （島での交代＝同便・同時刻乗降）
(T3) toD[p,m,j]=1 ⇒ a[p,m] = dep[CD,j] + A_C + C_D
(T4) frD[p,m,j]=1 ⇒ d[p,m] = dep[CD,j'] + A_C + C_D        （復路便 j' の D 発時刻。個別帰還で j≠j' 可）
     ※復路は別便でよい（spec §16 個別帰還）。frD の j は toD の j と独立。

4.3 滞在制約（Hard, spec §13,§16）
各スロットに leaves[p,m]∈{0,1}（horizon 内に帰還するか）を持つ。
(S1) at[p,m,k]=1 ∧ leaves[p,m]=1 ⇒ a[p,m]+smin[k] ≤ d[p,m] ≤ a[p,m]+smax[k]
(S1b) 帰還強制: at[p,m,k]=1 ∧ (a[p,m]+smax[k] ≤ H) ⇒ leaves[p,m]=1。
      期限 a+smax が horizon を超える最後の常駐者は計画末まで残れる（leaves=0 可）。
      ※これが無いと末端で必ず無限交代が必要になり実行不能化する（実装で確認）。
(S2) at[p,m,D]=1 かつ toD[p,m,j]=1 ∧ leaves ⇒ d[p,m] ≥ a[p,m] + dtable[ nAC[j] ]（上限なし, spec §16）

4.4 車両容量（spec §18）— assignV との論理積で only-enforce-if
(C1) Σ_{p,m} inB[p,m,k,j] ≤ cap[type(v)]   (assignV[B-arm(k),j,v]=1)
(C2) Σ_{p,m} outB[p,m,k,j] ≤ cap[type(v)]
(C3) Σ_{p,m} toD[p,m,j] ≤ cap[type(v)]      (assignV[CD,j,v]=1)
(C4) Σ_{p,m} frD[p,m,j] ≤ cap[type(v)]

4.5 車両スケジューリング / Backhaul（spec §19）
(V1) 同一車両が割当たる2トリップの運行区間 [dep,ret] は重ならない（NoOverlap）。
(V2) Backhaul は往復1トリップ構造に内包（B-arm, CD-arm とも）。
(V3) 車両初期位置: 各車両の最初の使用トリップ出発地 = init_veh(v)（既定 A）。
(V4) 対称性除去: dep[a,j] ≤ dep[a,j+1]（同一アーム内で時刻昇順）。

4.6 B島 常駐 / カテゴリ / 交代（spec §12,14、イベント評価 §0.4）
B-arm(k) トリップを時刻順に見て、各完了直後の占有 occ_k・カテゴリ数 catcnt_k,c を遷移で定義。
(O0) 初期 occ_k(0)・catcnt_k,c(0) は初期常駐者（spec §11）から。≥ 下限。
(O1) occ_k(後) = occ_k(前) + Σ_{p,m} inB[p,m,k,j] − Σ_{p,m} outB[p,m,k,j]
(O2) occ_k(各イベント直後) ≥ occmin[k]
(O3) catcnt_k,c(各イベント直後) ≥ catreq[k][c]   ∀c
     ※同便・同時刻乗降のため各完了時に課せば全時刻成立。replacement は下限割れ禁止で内包。

4.7 同乗 together（spec §15）
B-arm(k) 往路便 j、together[k] の各カテゴリ群 G について:
(R1) hasCat[c,k,j]=1 ⇔ Σ_{p,m: cat(p)=c} inB[p,m,k,j] ≥ 1
(R2) G 内の全カテゴリで hasCat 一致（全員存在 or 全員不在）。

4.8 D 在室上限（任意, spec §4）
(D1) Dmax≠null のとき、各イベント時点の D 在室数 ≤ Dmax（到着/出発で増減, §0.4 同様）。

4.9 カレンダー（spec §3）
(H1) 休日全運休: 任意トリップの [dep,ret] が Hol と重ならない。

4.10 初期状態（spec §11）
(I1) init_loc(p)=k: スロット1 = k 滞在、a[p,1]=init_arr(p)、最初の便は outB のみ（到着便なし）。
(I2) init_loc(p)=D: スロット1 = D 滞在、a[p,1]=init_arr(p)、最初の便は frD のみ。
(I3) init_loc(p)=A: スロット1 から通常通り（到着便あり）。

4.11 必須ローテーション（spec §17）
スロットの勤務種別 btype[p,m] ∈ {0,1}（1=B 島滞在, 0=D 滞在）を定義:
  btype[p,m] = Σ_{k∈K} at[p,m,k]
(RO1) 連続する使用スロットは B/D が交互:
  atused[p,m+1]=1 ⇒ btype[p,m] + btype[p,m+1] = 1
  （= 連続スロットで丁度一方が B、他方が D）。
  ※スロットは先詰め（§3.2）かつ m+1 使用なら m も使用なので、これで全勤務列が交互になる。
  ※これが D への需要を生む（限られた Cat 要員が再 B 勤務に就くには間に D が必要）。

⸻

5. Objective（spec §20）

minimize  Σ_(a,j) Σ_v assignV[a,j,v] × trip_hours(a) × unit_cost(v)

  trip_hours(B-arm(k)) = din[k]+dout[k]、trip_hours(CD-arm) = 8
  unit_cost(v) = cost[type(v)]（保有）/ rcost[type(v)]（レンタル）

⸻

6. 規模見積りと上限パラメータ（確定方針）

想定インスタンス: |P| ≈ 数十人、H ≈ 720h（30日）。

上限は「最短ループ長」から導出する（実装時にインスタンスごとに計算）:
  最短B周回 ≈ smin[k] + (din[k]+dout[k])      （例 24+4 = 28h）
  最短D周回 ≈ min_n dtable[n] + 8              （例 24+8 = 32h）

  M           = ⌈ H / 最短B周回 ⌉ 程度（例 ≈26）。乗客ごとに導出してよい（一律にしない）。
  J[B-arm(k)] = 島 k への到着イベント上限（例 ≈60）。
  J[CD-arm]   = D 訪問総数 ÷ 共有率（例 ≈120）。
  Nrent       = ピーク需要から（例 ≈10）。

変数規模（代表値）: 結合変数 toD/frD が支配項（≈ |P|·M·J[CD] ≈ 12.5万×2）。
総 boolean ≈ 37万 + 時刻チャネリング制約。CP-SAT で可解だが重い部類。

採用: (a) スロット方式（本書 §3）を継続する。
  理由: 滞在(24-48h)・D動的滞在・容量・コストがトリップ変数と直結し、業務制約を素直に書ける。
  代替 (b) 時刻インデックス location[p,t] は変数こそ少ない（≈17万）が、
  離散時刻遷移とトリップ/容量の結合が煩雑で制約数が膨らむため不採用。

重さの緩和策（実装で適用）:
  1. M を乗客ごとに導出（現実に取り得る最大訪問数）。
  2. allowedB の絞り込みで k 次元を削減（単一島なら ×1）。
  3. J のタイト化＋対称性除去 dep[a,j]≤dep[a,j+1]（§4.5 V4）。
  4. 段階解法: 実インスタンスでベンチ → 必要なら時刻ウィンドウで結合変数を疎化。

⸻

7. 残課題

Q4（解決済）: frD の復路便 j' は toD の j と独立な別便でよい（spec §16 個別帰還）。
  実装では frD[p,m,j] を toD と独立に持ち、d[p,m]=dep[CD,j']+A_C+C_D で表現済み。
Q5（将来）: 公平性・連続勤務上限は spec 未収集。必要時に Soft 制約として後付け可能。

⸻

8. 実装ステータス & ローリングホライズン定式化

8.1 実装ステータス（route_opt/）
- §3/§4/§5 を route_opt/model.py（FullModel）に実装。B-arm のみの検証版は barm_model.py。
- §0.4 のイベント評価・§4.11 ローテーション・§4.5 NoOverlap 等を含む。
- 規模見積り（§6）と検証は BENCH.md。単一モデルは概ね ~1週間まで可解、長 horizon は §8.2。

8.2 ローリングホライズン分解（長 horizon 用、route_opt/rolling.py）
単一モデルでは ~3週間超で feasible 解の発見が困難なため、時間方向に分割して解く。

- lookahead ウィンドウ W（例 6日）で解き、commit C（例 5日, C<W）までを確定して C 進む。
- トリップ完了は commit 内に限定（追加パラメータ commit_hours）:
    dep[a,j] + trip_hours(a) ≤ C        （used トリップ）
  滞在・占有・(S1b) は lookahead 全体 [0,W] で評価する。
  → 区間 (C,W] にトリップが無いため、commit 時点の常駐者は必ず期限が W より先になり、
    次ウィンドウで余裕をもって交代できる（シーム実行不能の回避）。
  → 条件: overlap = W − C ≤ min_k(smax[k] − dout[k])（tail を単一常駐者で覆える）。
- ウィンドウ終端（commit 時点）の状態を次ウィンドウの初期状態として引き継ぐ:
    location, arrived_at,
    earliest_departure（D 滞在者の残り必要滞在 = arrival + dtable[nAC]）,
    last_duty（A 待機者の直前勤務種別。次ウィンドウ初スロットの btype を交互に固定）。
  ※last_duty が無いと §4.11 ローテーションが境界を越えて破れる（B→B が発生）。実装で確認・修正済。
- 集約出力: route_opt/report.py が乗降イベントから滞在区間を再構成し CSV / Gantt(PNG) を生成。

⸻

本書はパラメータ非依存のモデル定義であり、実データは spec の二層方針どおり別ファイル供給。
