# CSPLib 31問題 参照ドキュメント（CP/MIP対応表＋ソルバー系統樹）

> **2026-08-06統合**: 従来3つに分かれていた人間向け参照資料（`REFERENCE_csplib_cp_mip_table.md`
> 「CP/MIP対応表」、`csplib_solver_family_tree.md`「ソルバー系統樹」）を本ファイル1つに統合した。
> 統合の経緯: 3つの参照ファイル（本ファイルの前身2つ＋`primary_problems.md`）が互いに独立して
> 手動更新されており、実際に内容が不整合を起こす事例（`primary_problems.md`が37問題を掲載する一方、
> 機械可読データである`Backend/dsl_repository/csplib_cp_mip_reference.json`は意図的に31問題へ
> 絞り込み済みだったのに追随されていなかった等）が見つかったため、Koshoshiの指示により整理した。
> 機械可読の一次データは引き続き`Backend/dsl_repository/csplib_cp_mip_reference.json`とし、
> 本ファイルはそれを人間が読みやすい形にした参照ドキュメントという位置づけ。
> `docs/primary_problems.md`は廃止した（`classify_problem()`の参照先は本ファイルへ切り替え済み。
> 詳細はENGINEERING_LOG.md 2026-08-06参照）。

**位置づけ**: CSPLib掲載の約100問題のうち、業務ヒアリングが現実的に成立しうる（パズル・純学術
ベンチマークを除く、興行・スポーツ分野等の対象外顧客層も除く、confidence=medium＝裏取り不十分な
ものも除く）31問題を対象に、(1) 通常CP/MIPどちらで解かれるか、(2) 「1つのソルバーを拡張すれば
別の問題も解けるか」という系譜関係、の2点を整理したもの。

分類基準は業務分野（製造・医療・物流…）ではなく、**解法構造**（決定変数の型、制約の型、目的関数の型）。
同じ業務分野でも構造が違えば別ファミリー、逆に業務分野が違っても構造が同じなら同一ファミリーに入れている。

---

## 重要な注意（裏取りで判明したバイアス）

CSPLibはCP(制約プログラミング)コミュニティが運営・投稿するベンチマーク集であり、掲載されている
参照文献の著者も大半がCP研究者である。そのため「CSPLib上で確認できる解法」にはCP寄りのバイアスが
かかっている可能性が高い。特に以下の3問題は、裏取りの結果、当初「MIP」または「両方」としていた
分類を「CP」に訂正したが、これは**CSPLibに掲載された文献がCPだったから**であり、その業界の実務で
MIPが使われていないことを意味しない。

- **prob047 Supply Chain Coordinations**: 唯一の参照文献が分散制約最適化(DCOP)論文。サプライチェーン
  最適化の実務では一般にMIPも広く使われる。
- **prob065 Optimal Financial Portfolio Design**: 参照文献はCP/局所探索が中心。金融工学の一般文献では
  MIQP/MIPも標準的。
- **prob082 Patient Transportation**: 参照文献はCP系研究室(UCLouvain)の論文のみ。搬送・ルーティング
  問題は業界実務でMIPやメタヒューリスティクスも広く使われる。

**推奨アプローチ列は「CSPLib上で確認できる典型的解法」であり、「その業界における唯一の正解」ではない**。
`classify_problem()`に組み込む際はこの点を踏まえ、CP/MIP判定・軸(a)適合チェックの一根拠として
使うに留めるべき（唯一の正解として扱わない）。

---

## 凡例

- **推奨アプローチ**: CP / MIP / 両方（BOTH）
- **確度**: 高＝CSPLib公式ページのresults/referencesで手法・論文タイトルを確認済み（本表は確度「高」のみを収録）
- **OptiBuddy対応**: 既存ドメインに実装済みならドメイン名、拡張候補なら「拡張候補」、対応なしは「―」
- **ベースドメイン**: 「◯」＝この問題自体が既存実装ドメインと1:1で一致し、それ自体がベース。ドメイン名＝そのドメインをベースにコピー→要件追加生成する拡張候補。空欄＝ベース関係が未整理（今後の検討対象）

---

## CSPLib CP/MIP対応表（31問題）

| ID | 問題名 | 概要 | 業務分野 | 推奨 | 確度 | OptiBuddy対応 | ベースドメイン |
|---|---|---|---|---|---|---|---|
| prob001 | Car Sequencing（自動車組立ラインの順序付け） | 各車両のオプション装着工程には「連続n台中m台まで」という処理能力上限がある。ラインを止めない投入順序を決める。 | 製造業(自動車組立) | CP | 高 | CarSequencing（実装済み） | ◯ |
| prob002 | Template Design（印刷版下デザイン） | 複数注文(アイテム×必要枚数)を最小枚数のテンプレート(版)に割り付けるカッティングストック系問題。 | 印刷・製造業 | 両方 | 高 | ― | ― |
| prob004 | Mystery Shopper（覆面調査員スケジューリング） | 覆面調査員を訪問間隔等の制約を満たしつつ各店舗訪問に割り当てる。 | 小売・市場調査 | CP | 高 | MysteryShopperScheduler（実装済み、2026-08-09登録） | ◯ |
| prob008 | Vessel Loading（船舶積み付け） | 船舶へのコンテナ/貨物積み付け計画。容量・配置制約下での積載順序を決める。 | 海運・物流 | CP | 高 | VesselDeckLoader（実装済み） | ◯ |
| prob022 | Bus Driver Scheduling（バス運転士シフト編成） | 作業(ピース)群を要件充足の最小シフト数でカバーする集合分割問題。 | 運輸(バス事業者) | MIP | 高 | CrewDutyScheduler（実装済み） | ◯ |
| prob030 | BACP（バランス学修カリキュラム編成） | 各学期の科目数・単位数のばらつきを抑えつつ先修関係を満たす科目配置。 | 教育(大学) | CP | 高 | ― | ― |
| prob034 | Warehouse Location（倉庫/拠点立地問題） | 開設固定費と顧客割当の変動費の合計を最小化する開設地点・割当を決める。 | サプライチェーン・物流 | MIP | 高 | StoreSite（実装済み） | ◯ |
| prob038 | Steel Mill Slab Design（製鋼スラブ設計） | 受注(色×重量)をスラブ容量・色数制限下で最小廃棄重量に割り付ける。 | 製造業(鉄鋼) | CP | 高 | SteelMillSlabDesign（実装済み） | ◯ |
| prob040 | Wagner-Whitin型配送コスト問題 | 多段階在庫拠点網で保管費・発注費を最小化する各拠点・各期の発注量を決める。 | サプライチェーン・在庫管理 | MIP | 高 | InventoryReplenishmentPlanner（実装済み） | ◯ |
| prob046 | Meeting Scheduling（会議スケジューリング） | 参加者のカレンダー・移動時間制約を満たす会議時間枠を決める。分散CSPとしても定式化。 | オフィス・施設管理 | CP | 高 | MeetingRoom（実装済み） | ◯ |
| prob047 | Supply Chain Coordinations（サプライチェーン協調） | 仕入れ・生産・配送の意思決定を協調させ全体コストを最適化。 | サプライチェーン | CP | 高 | ― | ― |
| prob051 | Tank Allocation（タンク割当） | 化学/液体貨物のタンク割当。互換性制約下のビンパッキング。 | 化学・液体物流 | CP | 高 | TankAllocationPlanner（実装済み、2026-08-10登録） | ◯ |
| prob056 | SONET Problem（光通信網設計） | 通信網(光ファイバーリング)の設計。ノード配置・容量制約。 | 通信インフラ | CP | 高 | ― | ― |
| prob058 | Discrete Lot Sizing（離散ロットサイジング） | 単一機械・複数品目の生産計画。段取り替え+在庫コスト最小化。 | 製造業(生産計画) | 両方 | 高 | LotSizingScheduler（実装済み） | ◯ |
| prob059 | Energy-Cost Aware Scheduling（電力コスト考慮型生産スケジューリング） | 時間帯別電力料金を考慮した生産スケジュール全体のコスト最小化。 | 製造業(エネルギー管理) | CP | 高 | EnergyCostAwareScheduler（実装済み、2026-08-09登録） | ◯ |
| prob060 | Ridesharing（ライドシェアマッチング） | 乗客と運転手のマッチング・ルーティングを制約下で決める。 | モビリティ・配車 | CP | 高 | RideshareMatchingPlanner（実装済み、2026-08-16登録） | ◯ |
| prob061 | RCPSP（資源制約付きプロジェクトスケジューリング） | アクティビティを資源容量・先行関係下でスケジュールしメイクスパン最小化。 | プロジェクト管理・生産切替 | CP | 高 | LineChangeoverScheduler（実装済み） | ◯ |
| prob063 | Winner Determination（組合せオークション落札者決定） | 重複しない入札の組合せで総落札額を最大化。 | オークション・調達 | MIP | 高 | AuctionWinnerSelector（実装済み） | ◯ |
| prob065 | Optimal Financial Portfolio Design（最適金融ポートフォリオ設計） | リスク・リターン等の制約下で分散投資配分を最適化。 | 金融(資産運用) | CP | 高 | PortfolioOverlapDesigner（実装済み） | ◯ |
| prob066 | Distance-Based Constrained Clustering（距離制約付きクラスタリング） | 顧客・店舗のグルーピングを距離等の制約下で行う。 | 小売・マーケティング分析 | 両方 | 高 | ― | ― |
| prob069 | Balanced Nursing Workload（看護師負荷均等化） | 患者の重症度に基づく看護師間の負荷を均等化(標準偏差最小化)。 | 医療(看護師配置) | CP | 高 | NursingWorkloadBalance（実装済み） | ◯ |
| prob077 | Stochastic Assignment and Scheduling（確率的割当・スケジューリング） | 所要時間等に不確実性がある割当・スケジューリング。 | 製造・サービス業 | CP | 高 | ― | ― |
| prob078 | Train Traffic Rescheduling（列車運行再スケジューリング） | 遅延発生時のダイヤ再編成で影響を最小化。 | 鉄道運行管理 | CP | 高 | ― | ― |
| prob082 | Patient Transportation（患者搬送計画） | 病院内/病院間の患者搬送を車両ルーティング+時間枠下で計画。往復ペアの原子性・患者カテゴリ適合・相乗りを伴うDial-a-Ride Problem系の構造（2026-08-06訂正、下記「判断が分かれた・訂正された問題」節参照）。 | 医療(搬送ロジ) | CP | 高 | PatientTransportPlanner（実装済み、2026-08-06登録） | ◯ |
| prob086 | CVRP（容量制約付き配車ルーティング） | 積載容量上限のある車両群で複数顧客への配送を最小コストで行う。 | 運輸・物流(配送) | 両方 | 高 | ―（2026-08-06訂正、下記「唯一の判断が分かれた問題」節参照） | ― |
| prob087 | Rotating Rostering（ローテーション勤務表） | 1名分の勤務表をローテーションで全従業員に展開する交代制シフト編成。 | 医療・小売等(交代制勤務) | CP | 高 | ShiftRotationScheduler（実装済み） | ◯ |
| prob089 | MASP（医療予約スケジューリング） | 患者の診療予約(診療科・医師・時間枠)を制約下で割当。 | 医療(外来予約) | CP | 高 | MedicalAppointmentScheduler（実装済み、2026-08-08登録） | ◯ |
| prob091 | MASSP（医療予約順序スケジューリング） | 予約患者を実際の診療順序に並べる。MASPの拡張。 | 医療(外来運用) | CP | 高 | MedicalAppointmentSequenceScheduler（実装済み、2026-08-08登録） | ◯ |
| prob115 | Tail Assignment（機材割当） | 各便に個別機材を割り当て、整備・運航規則を満たす経路を決める。 | 航空(機材運用) | CP | 高 | ― | ― |
| prob131 | Production Line Sequencing（生産ライン投入順序付け） | 生産ラインへの製品投入順序を工程能力制約下で決める。Car Sequencingの一般化に近い。 | 製造業(生産ライン) | CP | 高 | 拡張候補 | LineChangeoverScheduler（注1） |
| prob133 | Knapsack Problem（ナップサック問題） | 重量上限内で価値合計を最大化するアイテムの組合せを選ぶ。 | 汎用(予算配分・積載計画等) | MIP | 高 | CapitalProjectSelector（実装済み） | ◯ |

**注1（prob131）**: LineChangeoverScheduler（実装済み・RCPSP系）とCar Sequencing（prob001、実装済み）
の両方と構造が重複する可能性がある。どちらを真のベースにすべきかは追加ヒアリングでの判断が必要。

**集計**: 対象31問題、全問題confidence=高。CP優位22問題／MIP優位5問題／両方4問題。
ベースドメイン＝◯（既存実装と1:1一致、独立したnew_domainとして実装済み）20問題、
拡張候補1問題（prob131）、未実装・未着手10問題（2026-08-10再訂正: prob051が
`TankAllocationPlanner`として登録成功したため、19問題→20問題に更新。この直前の
訂正（18問題→19問題）は、同日prob059が`EnergyCostAwareScheduler`として登録成功した
反映によるもの。その前（17問題→18問題）は、同日prob004が`MysteryShopperScheduler`として
登録成功した反映によるもの。その前（15問題→17問題）は、2026-08-08に
`MedicalAppointmentScheduler`（prob089）・`MedicalAppointmentSequenceScheduler`（prob091）
として登録成功した反映によるもの。その前（14問題→15問題）は、2026-08-06に
`PatientTransportPlanner`として登録成功したprob082の反映によるもの。（13問題→14問題）は、
2026-08-05に追加実装されたVesselDeckLoader/SteelMillSlabDesign/LotSizingSchedulerの3件の
反映漏れによる誤りを修正したもの。またprob086はTruckDispatcherとの対応を取り消したため
◯から除外済み）。

---

## 系統樹全体像

```
A. 容量制約付き選択・パッキング系   [Root: prob133 Knapsack]
   ├─ prob063 Winner Determination        (単一アイテム選択 → 排他的バンドル選択＝集合パッキング)
   ├─ prob065 Optimal Financial Portfolio (0/1選択の合計最大化 → 0/1選択(v×b行列)のままミニマックス目的化。
   │    ※2026-08-02訂正: 当初「連続配分＋リスク制約」と記載していたがCSPLib公式仕様で誤りと判明。
   │    詳細はPortfolioOverlapDesigner登録メモ参照)
   ├─ prob002 Template Design             (単一容量 → 複数ビン、ビン数最小化＝カッティングストック)
   │    ├─ prob038 Steel Mill Slab Design (ビンパッキング＋色/重量の両立性制約)
   │    └─ prob051 Tank Allocation        (ビンパッキング＋薬品互換性制約) ※OptiBuddy: TankAllocationPlanner（実装済み、2026-08-10）
   └─ prob008 Vessel Loading              (1次元容量 → 空間配置＋積み下ろし順序制約)

B. 立地・空間配分系   [Root: prob034 Warehouse Location] ※OptiBuddy: StoreSite
   ├─ prob066 Distance-Based Constrained Clustering (固定費なし、距離+バランス制約でのグルーピング)
   └─ prob056 SONET Problem                          (単純割当 → ネットワークトポロジ設計+容量制約)

C. 資源制約スケジューリング系   [Root: prob061 RCPSP] ※OptiBuddy: LineChangeoverScheduler
   ├─ prob058 Discrete Lot Sizing          (資源=単一機械＋段取り替え＋在庫コスト)
   ├─ prob059 Energy-Cost Aware Scheduling (時間帯別コストを考慮した目的関数への置換) ※OptiBuddy: EnergyCostAwareScheduler（実装済み、2026-08-09）
   ├─ prob077 Stochastic Assignment and Scheduling (所要時間に不確実性を導入)
   ├─ prob078 Train Traffic Rescheduling   (資源=軌道/駅、動的な再スケジューリング)
   └─ prob131 Production Line Sequencing   (順序付け制約＋段取りコストの導入)
        └─ prob001 Car Sequencing          (「連続n台中m台まで」というウィンドウ容量制約への特化)

D. 勤務シフト編成・ロスタリング系   [Root: prob022 Bus Driver Scheduling]
   ├─ prob069 Balanced Nursing Workload (カバレッジ目的 → 負荷均等化(分散最小化)目的)
   └─ prob087 Rotating Rostering        (個別シフト決定 → 1パターンの全従業員へのローテーション展開)

E. タイムスロット割当・タイムテーブリング系   [Root: prob046 Meeting Scheduling] ※OptiBuddy: MeetingRoom
   ├─ prob004 Mystery Shopper (参加者可用性制約 → 訪問間隔制約付き多資源割当) ※OptiBuddy: MysteryShopperScheduler（実装済み、2026-08-09）
   ├─ prob030 BACP            (単発の時間割当 → 複数期間の科目配置+先修関係+負荷バランス)
   └─ prob089 MASP            (2資源(部屋+時間) → 医師×時間枠×患者の多次元割当) ※OptiBuddy: MedicalAppointmentScheduler（実装済み、2026-08-08）
        └─ prob091 MASSP      (割当後にさらに実施順序を決める拡張) ※OptiBuddy: MedicalAppointmentSequenceScheduler（実装済み、2026-08-08）

F. 車両・機材ルーティング系   [Root: prob086 CVRP] ※Root自体は実装例なし（2026-08-06訂正、下記「唯一の判断が分かれた問題」節参照）
   ├─ prob060 Ridesharing             (静的配送ルート → 動的マッチング+ルーティング) ※OptiBuddy: RideshareMatchingPlanner（実装済み、2026-08-16）
   └─ prob115 Tail Assignment         (積載車両 → 機材(航空機)の運航規則制約付き経路)

G. 多段階サプライチェーン・在庫フロー系   [Root: prob040 Wagner-Whitin型配送コスト問題]
   └─ prob047 Supply Chain Coordinations (単一階層の発注量決定 → 調達・生産・配送の複数意思決定を協調)

【独立（family未所属）】
prob082 Patient Transportation — 2026-08-06訂正: 従来はFamily F・prob086 CVRPの直接の子
（「容量制約→時間枠制約(VRPTW型)の追加」）として記載していたが、CSPLib公式仕様確認の結果、
往復ペアの原子性・患者カテゴリ適合・相乗りを伴うDial-a-Ride Problem（DARP）系の構造と判明。
車両ルーティング文献の一般的な系譜（VRP→VRPTW→PDPTW→DARP）ではCVRPの2段階以上先にあり、
「prob086の直接の子」と呼ぶには構造差が大きいとKoshoshiの指摘を受け、family_id/parent_problem_id
を両方nullにして独立した問題として扱うことに変更（Koshoshiとの相談、2026-08-06）。
【2026-08-06同日追記】上記の方針転換の後、`force_new_domain`を使わない自然な分類のみで
`PatientTransportPlanner`として実際に登録成功した（過去4連敗の後の5回目の試行で初成功）。
independent（family未所属）のまま実装済み扱いとする。詳細は下記
「判断が分かれた・訂正された問題」節参照。
```

7ファミリー × 30問題（ファミリー所属分。ルート7問題＋拡張23問題）＋独立1問題（prob082）＝31問題。
各ファミイルの内部合計: A=7 / B=3 / C=7 / D=3 / E=5 / F=3 / G=2（2026-08-06訂正: prob082をFamily Fから
独立させたためF=4→3に変更）。

---

## ルート選定の理由

| Family | ルート | なぜこれが最も基礎的か |
|---|---|---|
| A | prob133 Knapsack | 決定変数が0/1のみ、制約が容量式1本、目的が線形和。全ソルバーの中で最少の要素数。 |
| B | prob034 Warehouse Location | 「開設/非開設」の2値決定＋割当という最小構成の施設配置問題。ネットワークやクラスタリングはこれに次元を足したもの。 |
| C | prob061 RCPSP | 「活動・先行関係・資源容量・メイクスパン」という資源制約スケジューリングの最小骨格。段取り/在庫/コスト/不確実性/ネットワークはすべてこの上に載る拡張要素。 |
| D | prob022 Bus Driver Scheduling | シフト編成の最も基本形＝作業ピースの集合被覆/分割。バランス目的やローテーション展開はこの上位拡張。 |
| E | prob046 Meeting Scheduling | 「時間枠×資源の可用性」という最小のスロット割当CSP。多資源化・多期間化・順序拡張はここから枝分かれ。 |
| F | prob086 CVRP | 容量制約付き配送ルーティングという教科書的最小形。時間枠・動的マッチング・規則制約はすべて追加レイヤー。 |
| G | prob040 Wagner-Whitin型 | 単一階層・単一目的（発注費+保管費）の在庫ロットサイジングという最小形。多段階協調はこの上位拡張。 |

---

## 既存OptiBuddy実装との整合性

CP/MIP対応表の「ベースドメイン＝◯」問題は、7ファミリーのルートとすべて一致した
（2026-08-06訂正: 以前はprob086にTruckDispatcherを一致例として含め4件としていたが、
TruckDispatcherをprob086の実装例として扱わない方針に訂正したため3件になった）。

| CSPLib問題 | 既存ドメイン | 本分析でのファミリー | 一致 |
|---|---|---|---|
| prob034 Warehouse Location | StoreSite | B（立地・空間配分系）ルート | ✅ |
| prob046 Meeting Scheduling | MeetingRoom | E（タイムスロット割当系）ルート | ✅ |
| prob061 RCPSP | LineChangeoverScheduler | C（資源制約スケジューリング系）ルート | ✅ |

また拡張候補とされていた問題も、本分析のツリー上でそれぞれのルートの子孫として自然に位置づけられた。

- prob069 / prob087 → NurseShiftWeeklyCap拡張候補 = 本分析ではFamily D（Bus Driver Schedulingルート）の子孫。
  既存ドメインNurseShiftWeeklyCapはCSPLib外の実装だが、構造的な最基礎形はprob022であり、
  拡張時にprob022の集合被覆構造を意識すると再利用性が高い可能性がある。
- prob131 → LineChangeoverScheduler拡張候補（注1でCar Sequencingとの重複可能性が指摘されていた）
  = 本分析で **prob061 → prob131 → prob001** という一直線の系譜として解消。
  LineChangeoverSchedulerをベースにprob131を拡張し、その先にprob001（ウィンドウ容量制約への特化）を
  さらに拡張する、という順序が妥当と判断できる。
- **prob082 → TruckDispatcher拡張候補（2026-08-06に取り消し）**: 従来はFamily Fの直接の子（VRPTW拡張）
  として一致すると記載していたが、これは誤りだった。取り消し後、独立new_domainとして自然な分類のみで
  `PatientTransportPlanner`として同日中に登録成功している。詳細は下記「判断が分かれた・訂正された問題」節、
  および`Backend/dsl_repository/csplib_cp_mip_reference.json`のprob082エントリを参照。

**含意**: A（Knapsack系）・D（ロスタリング系）・G（在庫フロー系）はCSPLib31問題の中では独立したファミリーだが、
現行OptiBuddyにはこれらをルートとする実装ドメインがまだ存在しない。将来ドメインを拡充する場合、
prob133 Knapsack／prob022 Bus Driver Scheduling／prob040 Wagner-Whitin型を新規ベースドメインの
候補として検討する価値がある。

---

## 実装状況（2026-08-10時点、git log確認済み。2026-08-10にprob051の行を追加）

| CSPLib問題 | ファミリー | 状態 | 実装ドメイン |
|---|---|---|---|
| prob034 Warehouse Location | B root | ✅実装済み | StoreSite |
| prob046 Meeting Scheduling | E root | ✅実装済み | MeetingRoom |
| prob061 RCPSP | C root | ✅実装済み | LineChangeoverScheduler |
| prob086 CVRP | F root | 未実装（TruckDispatcherは既存の独立ドメインだが、prob086の実装例としては扱わない。2026-08-06訂正） | ― |
| prob001 Car Sequencing | C（prob131の子） | ✅実装済み | CarSequencing |
| prob069 Balanced Nursing Workload | D（prob022の子） | ✅実装済み | NursingWorkloadBalance（NurseShiftWeeklyCap拡張） |
| prob133 Knapsack | A root | ✅実装済み | CapitalProjectSelector |
| prob022 Bus Driver Scheduling | D root | ✅実装済み | CrewDutyScheduler |
| prob040 Wagner-Whitin型配送コスト | G root | ✅実装済み | InventoryReplenishmentPlanner |
| prob063 Winner Determination | A（prob133の子） | ✅実装済み | AuctionWinnerSelector |
| prob065 Optimal Financial Portfolio Design | A（prob133の子） | ✅実装済み | PortfolioOverlapDesigner |
| prob008 Vessel Loading | A（prob133の子、Family Fとの境界事例） | ✅実装済み | VesselDeckLoader |
| prob038 Steel Mill Slab Design | A（prob002の孫、孫世代のスコープ外方針の例外） | ✅実装済み | SteelMillSlabDesign |
| prob051 Tank Allocation | A（prob002の孫、孫世代のスコープ外方針の例外） | ✅実装済み（2026-08-10登録） | TankAllocationPlanner |
| prob058 Discrete Lot Sizing | C（prob061の子） | ✅実装済み | LotSizingScheduler |
| prob087 Rotating Rostering | D（prob022の子） | ✅実装済み | ShiftRotationScheduler |
| prob082 Patient Transportation | 独立（family未所属） | ✅実装済み（2026-08-06登録） | PatientTransportPlanner |
| prob089 MASP | E（prob046の子） | ✅実装済み（2026-08-08登録） | MedicalAppointmentScheduler |
| prob091 MASSP | E（prob089の孫、孫世代のスコープ外方針の例外） | ✅実装済み（2026-08-08登録） | MedicalAppointmentSequenceScheduler |
| prob004 Mystery Shopper | E（prob046の子） | ✅実装済み（2026-08-09登録） | MysteryShopperScheduler |
| prob059 Energy-Cost Aware Scheduling | C（prob061の子） | ✅実装済み（2026-08-09登録） | EnergyCostAwareScheduler |

20件が既存実装（2026-08-10訂正: prob051が本日登録成功したため19件→20件。この直前の訂正
（18件→19件）はprob059が同日登録成功した反映。その前（17件→18件）はprob004が同日登録
成功した反映。その前（15件→17件）はprob089/091が本表に未反映だった漏れの修正）。
残る9件が未実装ギャップ（prob086 CVRPは2026-08-06にTruckDispatcher対応を取り消したため
未実装扱いのまま）。
PatientTransportPlannerは2026-08-05の4回の登録失敗を経て、extension_of判断の訂正
（独立new_domainとして扱う方針転換）後、2026-08-06に自然な分類のみで5回目の試行にして
初めて登録成功した（詳細はENGINEERING_LOG.md 2026-08-06追記21参照）。
MysteryShopperSchedulerは2026-08-09に`force_new_domain`での再登録を経て登録成功した
（初回試行はStage1aがMeetingRoom拡張と誤分類したため取りやめ、詳細はENGINEERING_LOG.md
2026-08-09追記1参照）。EnergyCostAwareSchedulerは同日、`classify_problem()`が自然に
`match_type=new_domain`（confidence 0.85）と判定して一発で登録成功した。生成コードの
`mdl.pulse()`にfloat値を渡す実装バグ（CP Optimizerのpulse()高さ引数は整数必須）が
あり、baselineシナリオが誤ってinfeasible判定されていたが、Claudeが直接コードを修正して
解消した（詳細はENGINEERING_LOG.md 2026-08-09追記4参照）。
TankAllocationPlanner（prob051）は2026-08-10に`classify_problem()`が自然に
`match_type=new_domain`（confidence 0.92）と判定して一発で登録成功した。生成コードの
目的関数に「未割当ロット数の最小化」項が抜けており、容量・相性制約が「全ロットを
未割当にする」ことで自動的に満たされてしまうため、タンク台数最小化のみの目的関数では
「何も積まない」解が常に最適になってしまう構造的バグがあった（ヒアリング1節の
「全ロットを積み込みたい」という大前提が目的関数に反映されていなかった）。Gate2の
動的検証で「infeasibleシナリオがfeasible=Trueと判定（coverage_rate取得不可）」という
形で表面化し、Claudeが直接lexicographic目的関数の最優先項として未割当ロット数最小化を
追加、`metrics.coverage_rate`も追加して解消した（詳細はENGINEERING_LOG.md
2026-08-10追記2参照）。

---

## 全7ファミリー実装状況一覧（ルート＋直接子孫まで、孫世代は対象外）

孫世代（例: prob038/051、prob091）は分類キーの希薄化リスクの判断により意図的にスコープ外と
している——粒度を細かくしすぎるとStage1aの分類精度が悪化するリスクがあるため、当面はルート→
直接子孫の1段までを実装対象の基本単位とする方針を維持する。

| Family | ルート（CSPLib） | ルート状態 | 直接子孫（CSPLib） | 子孫状態 |
|---|---|---|---|---|
| A 容量制約付き選択・パッキング系 | prob133 Knapsack → **CapitalProjectSelector** | ✅ | prob063 Winner Determination → **AuctionWinnerSelector** | ✅ |
| | | | prob065 Optimal Financial Portfolio → **PortfolioOverlapDesigner** | ✅ |
| | | | prob002 Template Design | 未着手 |
| | | | prob008 Vessel Loading → **VesselDeckLoader** | ✅ |
| B 立地・空間配分系 | prob034 Warehouse Location → **StoreSite** | ✅（既存） | prob066 Distance-Based Constrained Clustering | 未着手 |
| | | | prob056 SONET Problem | 未着手 |
| C 資源制約スケジューリング系 | prob061 RCPSP → **LineChangeoverScheduler** | ✅（既存） | prob058 Discrete Lot Sizing → **LotSizingScheduler** | ✅ |
| | | | prob059 Energy-Cost Aware Scheduling → **EnergyCostAwareScheduler** | ✅（2026-08-09登録） |
| | | | prob077 Stochastic Assignment and Scheduling | 未着手 |
| | | | prob078 Train Traffic Rescheduling | 未着手 |
| | | | prob131 Production Line Sequencing | 未着手※1 |
| D 勤務シフト編成・ロスタリング系 | prob022 Bus Driver Scheduling → **CrewDutyScheduler** | ✅ | prob069 Balanced Nursing Workload → **NursingWorkloadBalance** | ✅ |
| | | | prob087 Rotating Rostering → **ShiftRotationScheduler** | ✅ |
| E タイムスロット割当・タイムテーブリング系 | prob046 Meeting Scheduling → **MeetingRoom** | ✅（既存） | prob004 Mystery Shopper → **MysteryShopperScheduler** | ✅（2026-08-09登録） |
| | | | prob030 BACP | 未着手 |
| | | | prob089 MASP → **MedicalAppointmentScheduler** | ✅（2026-08-08登録） |
| | | | prob091 MASSP → **MedicalAppointmentSequenceScheduler** | ✅（2026-08-08登録） |
| F 車両・機材ルーティング系 | prob086 CVRP | 未着手（TruckDispatcherは既存の独立ドメインだが実装例としては扱わない。2026-08-06訂正） | prob060 Ridesharing → **RideshareMatchingPlanner** | ✅（2026-08-16登録） |
| | | | prob115 Tail Assignment | 未着手 |
| G 多段階サプライチェーン・在庫フロー系 | prob040 Wagner-Whitin型 → **InventoryReplenishmentPlanner** | ✅ | prob047 Supply Chain Coordinations | 未着手 |
| 独立（family未所属） | ― | ― | prob082 Patient Transportation → **PatientTransportPlanner** | ✅（2026-08-06登録。2026-08-06訂正: 従来Family Fの子として掲載していたが、独立問題への分類変更に伴い本表もFamily Fの行から独立行へ移動） |

※1 prob131自体は未着手だが、その孫（prob001 Car Sequencing）は`CarSequencing`として実装済み。
孫世代を対象外とする方針の例外——実案件のヒアリングをきっかけに、直接の親（prob131）を経由せず
先に孫が実装された経緯によるもので、意図的な先取りではない。

---

## ファミリー間の類似性が低いことの確認

7ファミリーの決定変数・制約の型を比較すると、意図的に重複が小さくなっている。

| Family | 主決定変数 | 主制約 | 典型目的関数 |
|---|---|---|---|
| A 選択・パッキング | 0/1選択（アイテム→ビン） | 容量・両立性 | 価値最大化 / 廃棄最小化 |
| B 立地・空間配分 | 0/1開設＋割当 | 需要充足 | 固定費+変動費最小化 |
| C 資源制約スケジューリング | 開始時刻・順序 | 先行関係・資源容量 | メイクスパン/コスト最小化 |
| D ロスタリング | 0/1シフト割当 | 被覆・労基制約 | シフト数最小化/負荷均等化 |
| E タイムスロット割当 | 時間枠×資源割当 | 可用性・間隔 | 実行可能性/負荷バランス |
| F 車両ルーティング | 経路（順序＋接続） | 容量・時間枠 | 総移動コスト最小化 |
| G 在庫フロー | 期別発注量 | 需要充足フロー保存 | 発注費+保管費最小化 |

決定変数の型（選択/開設/時刻/順序/割当/経路/数量）が7ファミリーですべて異なっており、
「同じソルバーコアを拡張すれば別の型の問題も解ける」という誤った統合を避けられている。

---

## 判断が分かれた・訂正された問題

### prob008 Vessel Loading（分類判断が分かれた事例）

Vessel Loadingは「船倉への積み付け（空間パッキング）」と「積み下ろし順序（ルーティング/スケジューリング
的な順序制約）」の両方の性質を持つ。本分析では主たる決定（何をどこに積むか＝空間割当）を重く見て
Family A（パッキング系、Knapsackの拡張）に分類したが、複数港をまたぐ積み下ろし順序が支配的な要件の場合は
Family F（ルーティング系）側の拡張として扱う方が適切になる可能性がある。

**結論（較正実験により決着）**: 第3の分類経路（`hybrid_domain`）を新設する必然性は低いと判断した。
prob008を含むヒアリングにFamily A（2次元パッキング・危険物分離マージン）とFamily F（積み込み順序に
依存する接触/支持制約）の2ファミリー分の制約を1つのformulation_directiveへ明示的に統合して実機の
新規ドメイン登録フローに投入したところ、Stage2は両ファミリーの制約を欠落・混同なく1つのCPモデルへ
正しく実装した（生成コードは`Backend/solvers/vessel_deck_loader_solver.py`）。「人間（ヒアリング
担当者）が主たる決定・副次的な決定の両方を判断し、formulation_directiveに両ファミリー分の制約を
書き込む」という執筆型運用で、単純な単一親モデルの拡張では表現できないケースにも対応できることが
実証された。

### prob082 Patient Transportation（2026-08-06、extension_of判断の訂正）

従来「TruckDispatcherと構造的に近縁、拡張として実装するのが妥当」（`extension_of: "TruckDispatcher"`）
としていたが、これは誤りだった。CSPLib公式仕様（csplib.org/Problems/prob082）を直接確認したところ、
prob082には以下がすべて必須要件として明記されている。

- 各依頼はforward（起点→医療機関）・backward（医療機関→終点）のいずれか、または両方から成り、
  **両方を要する依頼は両方が完了して初めて達成とみなす**（原子性）
- 患者にはカテゴリがあり、**乗せられる車両が制限される**
- 患者は**付き添いを伴う場合があり**、追加の座席（load）を消費する
- 乗降に所要時間がかかる
- 車両には容量上限があり、**複数患者の同時乗車（相乗り）を前提**とした容量管理（"load"と"capacity"の
  別建て）
- 往路・復路それぞれに独立した時間窓があり、"sameVehicleBackWard"フィールドで往復を同一車両に
  すべきか任意に指定できる

これは配車ルーティング文献でいうDial-a-Ride Problem（DARP）系の構造であり、単純な容量制約付き
ルーティング（CVRP=prob086）に時間枠を足しただけのVRPTW型とは質的に異なる。TruckDispatcherの
既存コード（route/depotベースのCVRP実装）を土台にした軽量な関数パッチでの拡張は実際には成立せず、
独立したnew_domainとして扱うべきと判断した。2026-08-05のPatientTransportPlanner登録試行では、
複雑さを大きく削った最小構成のヒアリングに対してすら`detect_extension_gaps()`が5件以上の構造的
ギャップ（往復ペア構造、順序制約、相乗り、対応区分マッチング、車両別稼働時間帯）を検出しており、
これも上記の判断を裏付けている。

【2026-08-06追記・確定】当初はfamily_id="F"（車両・機材ルーティング系）・parent_problem_id="prob086"を
系統樹上の構造的近縁性を示す参考情報として維持する方針だったが、Koshoshiより再度指摘を受け撤回した。
配車ルーティング文献の一般的な系譜（VRP→VRPTW→PDPTW→DARP）で見ると、DARP系のprob082はCVRP(prob086)の
2段階以上先に位置し、「prob086の直接の子」と呼ぶには構造差が大きすぎるという判断。family_id・
parent_problem_idを共にnullとし、prob082をどのファミリーにも属さない独立した問題として扱う
（`Backend/dsl_repository/csplib_cp_mip_reference.json`のprob082エントリで反映済み。`extension_of`は
既にnullに修正済み）。

なお、TruckDispatcher自体の「prob086の実装例」扱いも2026-08-06に取り消した。TruckDispatcherは
過去の実案件ヒアリングから個別に構築された独立ドメインであり、CSPLibのCVRP定義に忠実な参照実装
として設計されたものではないため、prob086の代表実装として扱うべきではないという判断
（Koshoshiとの相談、2026-08-06）。TruckDispatcher自体は実装済みドメインとして引き続き存在し、
削除や無効化はしていない。

【2026-08-06追記・登録成功】上記のextension_of取り消し（独立new_domainとして扱う方針転換）を
受け、Koshoshiの提案により同日中に`force_new_domain`を使わない自然な`classify_problem()`分類のみで
PatientTransportPlannerの再登録を試行した。結果、`classify_problem()`が`match_type=new_domain`と
正しく判定し（過去4回の失敗はTruckDispatcher/CVRPへの拡張だとバイアスした結果、`force_new_domain`
での強制上書きが必要だった）、5回目の試行にして初めて自然な分類だけで登録成功した。これは、
本節で述べたextension_of誤判定の根本原因訂正（prob082をprob086の「子」から独立させたこと）が
実際に効いていることを直接示す証拠である。生成過程ではGate2 self-repairがconverter/solverの
キー不一致を2回の修正で自動修正し、`hearing_dsl_gaps`チェックがJSONパース失敗でスキップされる
軽微な既知の不具合が発生したが、いずれもブロッキングにはならなかった。`/baseline`・解なしシナリオ
双方の直接検証により正しい挙動を確認した上で登録している（詳細はENGINEERING_LOG.md
2026-08-06追記21・追記22参照）。

---

## 「ハイブリッド」問題（複数ファミリーにまたがる要件）についての既知の限界

実際の持ち込み案件が、最初から2つ以上のファミリーの性質を同時に要求してくるケース（例: 倉庫立地の
決定と、その後の配送ルート決定を同時に最適化したい＝B×F等）には、現在の「単一の基底問題を選んで
そこから拡張する」という単一親モデルでは対応できない可能性がある。prob008の較正実験により、
「人間がformulation_directiveに複数ファミリー分の制約を明示的に書き込む」執筆型運用で当面は
対応可能と判断しているが、3ファミリー以上が絡む、あるいは2つの基底ドメインの出力を多段で接続する
必要がある、より複雑な合成が必要になった場合は、改めて第3の分類経路（`hybrid_domain`）の要否を
検討する。

---

## 分類キーの希薄化リスクについて

優先着手キューを進めてファミリー内の子孫まで細かく登録していくと、Stage1a（LLMによる自然言語分類）が
判定に使える「決定変数の型・制約の型・目的関数の型」という軸の記述が、登録ドメイン数の増加に
追いつかなくなるリスクがある。ファミリー内の兄弟・子孫が増えるほど「そこそこ似ている」候補が増え、
分類プロンプト側の判定基準（`_STAGE1A_SYSTEM`等）を同じペースで拡充しない限り、誤分類率はキューが
進むほど悪化する可能性がある。粒度を増やす作業と分類キーを増やす作業は不可分であり、後者を怠った
まま前者だけ進めるべきではない。

---

## 出典・注意事項

- 出典: [csplib.org/Problems](https://www.csplib.org/Problems)（CC BY 4.0）の問題一覧・仕様本文、
  各問題ページのresults/references（個別確認したものはconfidence=high、本表はhighのみ収録）。
- 機械可読版: `Backend/dsl_repository/csplib_cp_mip_reference.json`
  （`is_base_domain`/`extension_of`/`family_id`/`parent_problem_id`フィールドを保持）。
- `classify_problem()`（Stage1a、`Backend/domain_generator.py`）はこの機械可読版と本ファイルの
  両方ではなく、本ファイルの内容をプロンプトに埋め込む形で参照する（2026-08-06〜、旧
  `docs/primary_problems.md`から切り替え）。
- 技術選定の最終判断（特に新規ドメイン追加時、または既存ドメインへの拡張の技術適合性確認時）では、
  本表を一次情報としてではなく「CSPLib上でどちらが確認できたか」という参考情報として扱うべき。
