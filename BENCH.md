BENCH.md — スケール検証レポート（2026-06-16）

対象: route_opt の Full モデル（B-arm + CD-arm）、1h 粒度、OR-Tools CP-SAT。
環境: devcontainer（linuxkit）、num_search_workers=8。
生成器: route_opt/bench.py（島ごと別カテゴリ・together なし・各乗客は自島のみ適格）。

⸻

1. 計測結果（代表値、各ケースに求解時間制限あり）

| ケース                  | pax | H(h) | vars   | cons   | build | solve  | status   | obj   |
|-------------------------|-----|------|--------|--------|-------|--------|----------|-------|
| 1isl x2 x7d             | 2   | 168  | 452    | 1.1k   | 0.0s  | 0.4s   | OPTIMAL  | 4400  |
| 3isl x6 x7d (hint)      | 18  | 168  | 4.6k   | -      | -     | 6.0s   | OPTIMAL  | 3600  |
| 1isl x4 x14d            | 4   | 336  | 1.8k   | 5.0k   | 0.0s  | 30s*   | FEASIBLE | 6000  |
| 3isl x4 x14d            | 12  | 336  | 6.0k   | 17k    | 0.1s  | 45s*   | FEASIBLE | 20400 |
| 3isl x6 x10d (hint)     | 18  | 240  | 7.7k   | -      | -     | 50s*   | UNKNOWN  | -     |
| 1isl x3 x21d            | 3   | 504  | 4.1k   | -      | -     | 40s*   | UNKNOWN  | -     |
| 1isl x3 x30d (hint)     | 3   | 720  | 5.8k   | -      | -     | 40s*   | UNKNOWN  | -     |
| 3isl x10 x30d (hint)    | 30  | 720  | 30k    | 95k    | 0.4s  | 150s*  | UNKNOWN  | -     |

\* は時間制限到達（解が閉じていない / 未発見）。

⸻

2. 所見

- モデル規模は線形でスケールし問題ない: 30日×30人でも約 3万変数・9.5万制約、ビルド 0.4s。
  → 当初懸念した「変数爆発」はミティゲーション（allowedB 絞り込みで k 次元collapse、
    M を小さく）で回避でき、規模はボトルネックではない。
- ボトルネックは求解（探索）の難しさ: 1h 粒度の時刻変数 ＋ 長 horizon の密結合
  ローテーション連鎖（占有 min を割らない交代チェーン）を既定探索が解けない。
- 信頼して解けるのは概ね 7 日まで（OPTIMAL）。10–14 日は不安定（FEASIBLE になる場合も
  UNKNOWN の場合もある）。21 日以上は feasible 解すら時間内に見つからない。
- 単純な探索ヒント（B-arm 交代周期の骨格）では不足。完全な feasible 完成を導けない。
- 注: 小プール（島あたり数名）は最悪領域（深い再循環で M が大きく必要）。
  大プール（数十人）は per-worker の再循環が浅く M は小さくて済むが、
  それでも 30 日 horizon の結合スケジューリング自体が重く UNKNOWN。

⸻

3. 推奨（実装すべき本命ミティゲーション）

ローリングホライズン分解（時間方向の分割）。

  問題は本質的に逐次的（ローテーションが時間軸に沿って進む）。
  ~7 日のウィンドウで解き、ウィンドウ終端の状態（誰がどこに・いつ到着）を
  次ウィンドウの initial_state として渡して接続する。
  本モデルは任意の initial_state を既にサポートしているため、接続機構は構築可能。
  各サブ問題が可解ゾーン（~7日）に収まり、30 日を現実的に解ける見込み。

補助策（併用候補）:
  - ウィンドウ重複（数時間〜1日）で境界の最適性劣化を緩和。
  - 各ウィンドウは feasibility 優先 → 局所最適化の2段。
  - J/JCD/M をウィンドウ長から導出してタイトに。
  - 構成的ヒート（貪欲な交代スケジュール）をウィンドウ初期解として AddHint。

⸻

4. 結論

設計（spec/model）と実装は正しく、小〜中規模（〜1週間）では最適解が得られる。
実規模（30日）には ローリングホライズン分解 の実装が必要。

⸻

5. ローリングホライズン分解の結果（実装・検証済）

実装: route_opt/rolling.py（solve_rolling）, route_opt/run_rolling.py。
モデルに commit_hours を追加（トリップは [0, commit] のみ、lookahead [commit, H] は
常駐の余裕確保用）。各ウィンドウ終端（commit 時点）の状態を次ウィンドウの
initial_state として渡す（D は earliest_departure で残り必要滞在を引き継ぐ）。

重要な設計値: overlap = lookahead − commit は smax − dout 以下にする必要がある
（tail 区間を単一常駐者で覆えるため）。実用上 overlap = 1 日（24h）で十分。

| ケース                         | window | 結果 | total_cost | wall  |
|--------------------------------|--------|------|------------|-------|
| 30d, 3島x6人（大プール）       | 6d/5d  | 全6窓 OPTIMAL | 20400 | 41s |
| 30d, 2島x3人（タイト, CD多用） | 6d/5d  | 全6窓 OPTIMAL（CD便12） | 23200 | 18s |

→ 単一モデルで feasible 不到達だった 30 日が、分解で全期間を各ウィンドウ最適に解ける。
  CD-arm（D 再循環）も D handoff 込みで正しく機能。

留意点:
- 各ウィンドウ最適 ≠ 全体最適（myopic）。境界の最適性は overlap で緩和。全体最適が要れば
  ウィンドウ長を伸ばす / 重複を増やす / 列生成等の上位手法を検討。
- overlap 上限 smax − dout は B 島の最小 smax に律速される。極端に短い max_hours の島が
  あると overlap を取りづらい。

⸻

6. 固定ダイヤ（AC/CA）導入の効果と「rolling は不要になるか」（2026-06-24）

背景: AC/CA に固定ダイヤ（cd_arm.a_c_departures）を入れて depCD を定数化した。
論点は「時刻固定で計算量が落ち、全期間を単発（rolling なし）で 100秒程度に解けるか」。

6.1 単発（固定ダイヤ）の限界
- depCD を NewIntVar(0,H) → NewConstant に置換すると、CD 側の探索次元と休日リファイが消え、
  7d 単発で branches 538 vs 自由ダイヤ 9789（≈1/18）と明確に軽くなる。
- だが B-arm の depB は IntVar(0,H) のまま（手付かず）で、長 horizon の主因が残る。
  100秒上限・単発（固定ダイヤ）: 14d FEASIBLE/gap100%、21d FEASIBLE、30d↑ UNKNOWN。
  下界がゼロに張り付き（gap=100%）、最適証明も長 horizon の求解も不可。
  → 固定ダイヤだけでは rolling を捨てられない。

6.2 「滞在固定」は逆効果
- B 滞在を min==max の単一値に固定しても、depB は変数のまま（narrow されるだけで pin されない）。
  D 側の動的滞在が残るため depB は浮く。タイト構成（W=2）では INFEASIBLE、コストも悪化。
- 滞在幅 [smin,smax] は「D 側変動・タイミングを吸収する緩衝」。固定はこの緩衝を壊す。
- 互換ワーカーの対称性除去（第0スロット lex）は conflicts を下げるが branches 解消には不足。
  → 現行モデルの延命（滞在固定・対称性除去）は筋が悪い。

⸻

7. 時間展開フロー（匿名ワーカー）への再定式化 — rolling 不要化の本命（2026-06-24）

7.1 診断: 現行モデルは「過剰一般化」
AC/CA はダイヤ、B/D は強制滞在（ルールテーブル）で支配される。本質は固定時間グリッド上の
被覆＋割当問題なのに、現行は全イベント時刻を [0,H] の自由 IntVar として持ち、ワーカーを個体
展開している。これが (a)偽の連続自由度、(b)ワーカー対称性、(c)弱い LP 下界（gap=100%）を生む。

7.2 試作: ダイヤグリッド＋互換クラス内匿名フロー
同一世界（B固定36h・D固定10h・6h毎ダイヤ・cap4・便600・occ1・W=6）で、現行 FullModel と
時間展開フロー（op=便運行、board_out=便乗船、bin/B 占有、d2b/b2d プールで B/D 交互）を比較。

| horizon | フロー版（単発100s） | 現行 FullModel（単発100s） |
|---------|----------------------|----------------------------|
| 14d | OPTIMAL 0.3s gap0% | FEASIBLE 100s（証明不可） |
| 30d | FEASIBLE gap42.9% | UNKNOWN（解なし） |
| 45–90d | FEASIBLE（解は出る） | UNKNOWN（解なし） |

→ 連続時間・個体モデルが解けない領域でフロー版は feasible 解を出し、14d は 0.3s で最適証明。
  匿名化でワーカー対称性が消え、変数・branches も桁減。過剰モデル仮説は実測で裏付け。

7.3 個体（P001…）の復元
匿名フロー解はフェーズ2の経路分解（flow decomposition）で個体タイムラインに復号できる。
14d 最適解から P001…P006 の (B 区間 / D 便) 列を復元し、占有 min=1・B/D 交互を検証（OK, warnings=0）。
→ 「個体が結果に示され続ける」要件は、識別を探索に持ち込まず復号で満たせる。
  匿名化できるのは互換クラス内のみ。属性差（カテゴリ/weight/許可サイト/初期）は多品種で保持。

7.4 下界強化で gap を閉じる
- 失敗: ワーカー総数保存則 ==W（全8状態を数えて課す）→ gap 50% のまま。
  gap は同時実行の架空容量ではなく、フロー LP が分数ワーカーで被覆を満たす整合性ギャップ。
- 成功: 被覆構造からの組合せ下界カット
    nb     = ceil((H-WARM)/SB)          # 連続被覆に要する B 勤務数
    trips  = max(0, nb - W)             # 初期W人超の勤務は D 往復(=ferry)が必須
    Kferry = 2*ceil(trips/CAP)          # 往復ウェーブ別便（W<2cap+occ で兼用不可）
  を sum(op) >= Kferry として追加。

| horizon | カット前 | カット後 |
|---------|----------|----------|
| 30d | FEASIBLE 100s gap50% | OPTIMAL 0.9s |
| 45d | FEASIBLE 100s gap41.7% | OPTIMAL 1.8s |
| 60d | FEASIBLE 100s gap50% | OPTIMAL 14.3s |
| 90d | FEASIBLE 100s gap50% | gap3.4%（下界は最適16800に到達、primal一歩手前）|

→ K は全 horizon で発見済みコストと一致＝発見解は元から最適。gap は下界カット欠落が原因だった。
  フロー再定式化＋被覆カットで、中期 horizon を単発・最適証明・数秒で解ける＝rolling 不要。

7.5 結論と残課題
- 現行の連続時間・個体モデルは過剰。ダイヤグリッド＋互換クラス内匿名フロー＋被覆カットにすれば、
  単発で全期間を（最適証明つきで）解け、個体も復号で完全復元できる。
- 残課題（本番適用）:
  1. カットの一般化: 多島（島別 occ/カテゴリ）、occ≥2、大 W（2cap+occ>W が崩れると便兼用で係数2が緩む）。
  2. 動的 D 滞在: SB/SD 定数前提を、同乗数 nAC 依存の滞在クラス展開へ。
  3. 多品種化: ride_together / weight 別 D 滞在 / 初期ピンをコモディティ境界＋復号で織り込む。

検証スクリプト（scratchpad, 非コミット）: bench_timetable.py, bench_tight.py, bench_workers.py,
bench_sym.py, bench_100s.py, flow_proto.py, flow_decode.py, bench_bound.py。

⸻

7.6 一般化の続報: 多島は動く / 強い下界カットは一般化しない（2026-06-24, 続）

flow_model.py で多島（ferry・D を全島共有, 島別 occ/B滞在/ワーカープール）へ拡張し検証。

- 多島フロー＋島別 flow decomposition は正しく動く（検証済）:
  2島(B1:SB36/W4, B2:SB24/W4, SD10, cap4) で 14d/30d とも、各島 occ_min 充足・B/D 交互・
  decode_warn=0 で P{島}_{i} のタイムラインを復元。連続時間・個体モデルが解けない 30d でも
  feasible 解＋個体復元が出る、という核の利点は多島でも保持。

- ただし 7.4 の強い下界カット 2*ceil(T/cap) は一般には非妥当（重要な訂正）:
  2島30d で cut=True が cost 13200 を「最適」と証明したが、cut=False は 12600 を発見。
  すなわちカットがより安い実行可能解を誤って除外していた。原因は「島をまたいで便が往復を
  兼用できる」ため、単島タイト regime（W < 便兼用に要る人数）で偶然 tight だった 2× が
  多島では成り立たないこと。検証（cut=True>cut=False）が無ければ見逃す不具合だった。

- 妥当な安全カット N>=ceil(T/cap)（総席需要 2T ≤ 総供給 2*cap*N から）は常に妥当だが、
  下界が LP 緩和と同値で gap を上げない（2島30d gap ~47% のまま）。

結論（下界の一般化は未解決の本丸）:
  最適性証明の gap-closing は、単島の timing 特異な 2× に依存しており一般化しない。
  多島で gap を閉じるには timing 認識の下界（時間窓ごとの必須トリップ数、波の離散性を捉える
  カット）か、列生成/branch-and-price 等の上位手法が要る。一方「高速 feasible＋個体復元」は
  多島でも確立済みで、rolling 置換の実用価値（速い良質解）はそのまま成立する。

次イテレーションの実装課題（優先順）:
  1. [済] 動的 D 滞在: 便を積載クラス z[tau,n] に分割し SD(n)=d_stay_table[n] を表現（flow_dynd.py）。
     単島 14d で OPTIMAL（ソルバは n=4 相乗り・滞在22h を選び便数最小化）、個体も (n,滞在) 付きで
     復号・占有・交互を検証 OK。グリッド整合のため SD(n)≡(period-(A_C-D_C)) (mod period) を要する。
     30d は feasible だが gap 50%（下界は安全カットのみ＝下記3が本丸なのは多島と同様）。
  2. [済] 多品種化: 品種=weight で分け、便を積載クラス z[tau,n] + weight内訳 bown[w,tau,n] に
     展開（flow_mc.py）。weight 別 D 滞在（同便でも small/large で滞在・復路便が別）、初期ピン
     （初期B在室者を bin[w,0] の free entry として実フローに乗せる）、ride_together（グループ
     weight の便単位 全乗船 or 全不在）を実装。単島 14d で OPTIMAL、占有 viol=0・B/D 交互・
     滞在整合（small n4→22 / large n4→28）・together（small乗船⇔large乗船）・個体復号を検証 OK。
     30d は feasible だが gap（下界は安全カットのみ＝下記3が本丸）。
  3. 下界の一般化（本丸）: 調査の結果「単純カットでは閉じない」が確定（下記 7.7）。

⸻

7.7 下界一般化の調査結論: bin-packing 型ギャップ（2026-06-25）

便コストは「往路 cap 席・復路 cap 席（別脚）」の固定費で、本質は ferry 次元の bin-packing。
LP 緩和の下界が弱く、CP-SAT 既定では閉じない。実測と解析で次を確定:

- 診断（安全カットのみ, 300s）: 単島30d bound=2400 / 2島30d bound=6600 とも 300 秒間 1 ミリも
  改善せず。cost も改善しない（単島4800/2島13200 固定）⇒ gap は dual 側、かつ primal はほぼ最適。
- 解析: 各便は往 cap＋復 cap（別脚）= 総供給 2*cap*N、総需要 2T ⇒ N>=ceil(T/cap) が唯一クリーンに
  valid。これは LP 緩和と同値で gap を上げない（=安全カット）。
- カット試行の妥当性（検証で峻別）:
    prefix（valid: 便は可能にする勤務より前に運行, sum_{f<=t-HOME} op>=ceil((L(t)-W)/cap)）
      → nested のため下界を上げない（30d bound 2400 のまま）。
    window/2× （下界を上げるが非妥当）: 窓需要 Lreq(b)-Lreq(a) はワーカーの D 往復の時間自由度を
      無視して過大評価 → より安い feasible 解を除外（単島30d で cost 5400>4800 を誤って「最適」, 7.4
      の 2× も 2島で 12600 を除外）。検証（cut>no-cut の矛盾）が無ければ気付けない不具合。

結論: 最適性証明の gap-closing は単純な閉形カットでは不可能。bin-packing/cutting-stock 同様、
  列生成/branch-and-price か問題固有のファセットカットが要る（別プロジェクト規模）。
  ただし primal は延長探索でも改善せず＝事実上ほぼ最適。実運用（rolling 置換＝速い良質解＋個体）には
  十分で、緩い証明書（cost vs 安全下界）付きで運用可能。タイト証明が要る場合のみ列生成を実装する。

追加スクリプト（scratchpad, 非コミット）: bound_diag.py, test_prefix.py。

到達点（フロー再定式化の構成要素）:
  feasible 求解＋個体復号は 多島・動的D滞在・多品種(weight/together/初期ピン) まで実装・検証済。
  連続時間・個体モデルが解けない長 horizon でも、速い良質解＋個体復元（P{品種}_{i} の B/D/便
  タイムライン）が得られる＝rolling 置換の実用価値は確立。残るは最適性証明用の一般下界のみ。

⸻

7.8 正式モジュール化（route_opt/flow.py, 2026-06-25）

試作群を route_opt/flow.py（FlowModel / FlowSolution）として正式実装。既存 schema.Instance に接続。
- 入力: Instance（固定ダイヤ cd_arm.a_c_departures 必須）。コモディティ=(B サイト, カテゴリ, weight)。
- 対応: 多島（島別 occ/category_requirements/B 滞在ウィンドウ[min,max]/乗降所要）、動的 D 滞在
  （積載クラス z[tau,n] + 復路便スナップで一般ダイヤ・任意滞在）、weight 別 D 滞在、ride_together、
  D 同時在室上限、初期ピン（B 在室/A 待機）、車両タイプ別台数選択でコスト最小化、安全下界カット。
- 未対応は明示エラー（FlowUnsupported）: ダイヤ未指定 / 1 乗客が複数 B サイト適格 / 初期 location が
  D・島間移動中。
- decode(): モデルの実 ein/eout/bo 値を使い入場↔退出を FIFO 対応付けして個体を復元
  （滞在ウィンドウの可変退出を忠実に再構成、徒歩遅延 din/dout を考慮）。
- テスト: tests/test_flow.py（6 件）。固定ダイヤ必須・多サイト拒否・単島/多島の占有＆カテゴリ充足・
  個体重複なし・B/D 交互・weight 別 D 滞在・初期ピン を検証。全 13 件（schema 含む）緑。
  実測: 14d 単島 OPTIMAL、14–30d 単島/多島で feasible＋復号妥当（占有違反0・重複0・交互OK）。

残課題: 最適性証明用の一般下界（7.7, 列生成規模）。fleet_limit（NoOverlap 台数制約）は実装済だが
  既定 off。B 滞在ウィンドウのハンドオフ厳密化（初期 B 在室者の経過滞在）は近似。

追加スクリプト（scratchpad, 非コミット）: flow_model.py, test_flow_model.py, flow_dynd.py, flow_mc.py。
