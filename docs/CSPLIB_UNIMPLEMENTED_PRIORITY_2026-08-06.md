# CSPLib未実装問題の棚卸し・優先順位付け（2026-08-06）

`Backend/dsl_repository/csplib_cp_mip_reference.json`（31問題版、家族A〜Gの7ファミリー分類）を
棚卸しした。作業に先立ち、同JSONのprob082（Patient Transportation Problem）が
`optibuddy_domain: null`のままだったが、実際には本日（2026-08-06、ENGINEERING_LOG追記21）
`PatientTransportPlanner`として登録成功済みであることが判明したため、先にJSON側を修正した
（`optibuddy_domain: null → "PatientTransportPlanner"`、`is_base_domain: false → true`）。

この修正後の実装状況は **31問題中21問題が実装済み、10問題が未実装**（2026-08-16再追記:
prob060の登録完了により20/11→21/10に更新。この直前の追記はprob051の登録完了による
19/12→20/11への更新。その前はprob059の登録完了による18/13→19/12への更新。その前は
prob004の登録完了による17/14→18/13への更新。その前（2026-08-08追記）はprob089/prob091
の登録完了による15/16→17/14への更新）。

## 実装済み（20問題、参考）

| CSPLib ID | 問題名 | 実装ドメイン |
|---|---|---|
| prob001 | Car Sequencing | CarSequencing |
| prob004 | Mystery Shopper | MysteryShopperScheduler（2026-08-09登録） |
| prob008 | Vessel Loading | VesselDeckLoader |
| prob022 | Bus Driver Scheduling | CrewDutyScheduler |
| prob034 | Warehouse Location | StoreSite |
| prob038 | Steel Mill Slab Design | SteelMillSlabDesign |
| prob040 | Wagner-Whitin Distribution | InventoryReplenishmentPlanner |
| prob046 | Meeting Scheduling | MeetingRoom |
| prob058 | Discrete Lot Sizing | LotSizingScheduler |
| prob059 | Energy-Cost Aware Scheduling | EnergyCostAwareScheduler（2026-08-09登録） |
| prob061 | RCPSP | LineChangeoverScheduler |
| prob063 | Winner Determination (Combinatorial Auction) | AuctionWinnerSelector |
| prob065 | Optimal Financial Portfolio Design | PortfolioOverlapDesigner |
| prob069 | Balanced Nursing Workload | NursingWorkloadBalance |
| prob082 | Patient Transportation Problem | PatientTransportPlanner（本日登録・JSON側の記録漏れを本棚卸しで修正） |
| prob087 | Rotating Rostering | ShiftRotationScheduler |
| prob089 | Medical Appointment Scheduling (MASP) | MedicalAppointmentScheduler（2026-08-08登録） |
| prob051 | Tank Allocation | TankAllocationPlanner（2026-08-10登録） |
| prob091 | Medical Appointment Sequence Scheduling (MASSP) | MedicalAppointmentSequenceScheduler（2026-08-08登録、prob089の拡張） |
| prob060 | Ridesharing | RideshareMatchingPlanner（2026-08-16登録） |
| prob133 | Knapsack | CapitalProjectSelector |

## 未実装11問題の優先順位

判断基準は次の5点の複合評価（単一指標での機械的ソートではなく、既存実績パターンとの類推による判断）。

【2026-08-06修正】初版では「技術適合性」を「CPかMIPか」で評価し、MIP/BOTH判定の問題を
一律に減点していたが、これはKoshoshiの指摘により誤りと判明した。実装済み15ドメインの中には
AuctionWinnerSelector・CapitalProjectSelector・InventoryReplenishmentPlanner・
CrewDutySchedulerなどMIP(docplex.mp)推奨で問題なく実装できた例が複数あり、MIPそのものが
リスク要因ではない。StoreSiteが4回失敗したのは「MIP向きの問題をCP Optimizerで実装しようと
して構造的に無理だった」ことが原因で、docplex.mpに切り替えた時点で成立している。そのため
基準1を「推奨手法が一意に定まっているか」に修正し、CP/MIPどちらが適切かとは独立の軸として
「構造的難易度」を新設した。

1. **技術選定の明確さ**（旧: 技術適合性、2026-08-06に定義修正）: recommended_approachが
   単一の値で言い切れているか（confidence高・`BOTH`でない・`caveat_ja`で実務との食い違いが
   指摘されていないか）。MIP推奨であること自体は減点材料にしない。`BOTH`判定や、CSPLib掲載
   文献がCP寄りだが実務ではMIPが主流の可能性がある、といった注記がある問題は、Stage1a/Stage2
   でどちらの技術指示を出すべきか判断が割れやすく、遠回りになるリスクがある。
2. **構造的難易度**（2026-08-06新設）: CP/MIPどちらが適切かとは独立に、問題構造自体が複雑で
   実装に複数回の試行を要しそうか。判断の目安はprob082（患者搬送、Dial-a-Ride型）の実例で、
   往復ペアの原子性・相乗り・カテゴリ適合を同時に満たす必要がある構造のため、5回目の試行で
   ようやく登録成功した。同種の複合構造（マッチング+ルーティング、不確実性下の計画等）を
   持つ問題は、推奨手法がCPであっても構造的難易度は別途高く評価する。
3. **ヒアリング適性**: エンドユーザーが専門用語なしで書けるヒアリングに落とし込みやすい業務構造か。確率分布・協調最適化など抽象度の高い問題は不利。
4. **ファミリー補強効果**: 実装済みドメインの分布に対し、手薄なファミリー（E:タイムスロット割当、F:車両・機材ルーティング）を補うか。
5. **市場適合性**: OptiBuddyの想定顧客層（中小〜中堅企業の個別業務ヒアリング型）に対し、対象業界の裾野が広いか。

### 優先度High（次に着手する価値が高い）

（注: prob089・prob091は2026-08-08、prob004・prob059は2026-08-09、prob051は2026-08-10に実装完了済み。当時の優先度検討記録として以下に残すが、実装ドメイン一覧としては上記「実装済み」表を参照のこと。）

| CSPLib ID | 問題名 | 推奨手法 | 根拠 |
|---|---|---|---|
| prob089 | Medical Appointment Scheduling (MASP) | CP | ✅**2026-08-08実装完了**（MedicalAppointmentScheduler、new_domain登録）。推奨手法はCPで単一（技術選定の明確さ:高）。医療系は既に2ドメイン実装済みでヒアリングパターンに実績あり。ファミリーE（タイムスロット割当）はMeetingRoom1件のみで手薄だった。予約枠割当という構造はヒアリング化が容易。構造的難易度も低い（時間枠割当+重複禁止という単純な制約構造）。 |
| prob004 | Mystery Shopper | CP | ✅**2026-08-09実装完了**（MysteryShopperScheduler、`force_new_domain`によるnew_domain登録）。初回試行はStage1aがMeetingRoom拡張と誤分類したため取りやめ、再試行で登録成功。推奨手法はCPで単一・confidence high。登録後、生成MIPソルバーに実装バグ2件（訪問間隔制約の全ペア列挙によるO(n²)組合せ爆発／CE-limitフォールバック共通コードのハイフン付き変数名による値取得失敗）が見つかり、Claudeが直接コード修正して解消。詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md「2026-08-09追記1」参照。 |
| prob091 | Medical Appointment Sequence Scheduling (MASSP) | CP | ✅**2026-08-08実装完了**（MedicalAppointmentSequenceScheduler、new_domain登録）。prob089（MASP）の直接拡張（parent_problem_id=prob089）として089完了後に続けて登録。登録時に実装バグ3件（候補並び順依存の同期制約／存在しないAPI引数によるクラッシュ／absent候補への配慮漏れ）が見つかり、Claudeが直接コード修正して解消。詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md「2026-08-08追記2」参照。 |
| prob051 | Tank Allocation | CP | ✅**2026-08-10実装完了**（TankAllocationPlanner、`classify_problem()`が自然にnew_domainと判定し一発で登録成功、confidence 0.92）。推奨手法はCPで単一（技術選定の明確さ:高）。ビンパッキング系（ファミリーA）は当社の実装実績が最多で構造的難易度が低い一方、薬品分類間のペア禁止（酸性×アルカリ性等）というカタログ未実装の互換性制約パターンを新規に扱った。生成コードの目的関数に未割当ロット数最小化項が抜けており「何も積まない」解が最適になってしまう構造的バグが見つかり、Claudeが直接コード修正して解消。詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md「2026-08-10追記2」参照。 |

### 優先度Medium

| CSPLib ID | 問題名 | 推奨手法 | 根拠 |
|---|---|---|---|
| prob060 | Ridesharing | CP | ✅**2026-08-16実装完了**（RideshareMatchingPlanner、new_domain登録）。ファミリーF（車両・機材ルーティング系、それまで実質空白）を補強。着手前にprob082の内部エンジニアリングメモ（OptiBuddy_V81_devnotes/test_hesrings/rideshare_matching_planner_engineering_note.md）で「真に新規となる要素は運転手固有の出発・目的地点／乗車時間上限のハード制約化の2点のみ」と見積もった通り、Stage2初回生成コードはこの2点が未実装のままGate2指摘となり、debug_agentの自動修正（5ターン上限）でも解消しなかったため、Claudeが直接CP Optimizerモデル（デポ疑似interval_var＋sequence_var上のfirst/last固定＋type_of_next/elementによる実経路距離目的関数）を書き換えて解消した。登録直後の実UI検証で、write_files_for_dynamic_check()の対象外だったi18nファイルの上書き事故、およびGate2動的検証の対象外だったui_converter.pyのフィールド名不一致という2件のインフラ上の盲点も発見・修正した。詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md「2026-08-16追記1」参照。 |
| prob066 | Distance-Based Constrained Clustering | BOTH | 小売マーケティング分析という業種は明確だが、技術選定の明確さが低い。CP/MIP/列生成/SATが実際に分かれる稀な「真の両方」ケース（confidence high）で、どの技術指示をStage2に出すべきかの検討コストが他問題より高い（MIP自体が難しいわけではなく、選定が一意に決まらないことがリスク）。 |
| prob030 | Balanced Academic Curriculum Problem (BACP) | CP | 推奨手法はCPで単一・構造的難易度も低いが、対象顧客が大学・教育機関に限定され市場規模が他問題より小さい。 |
| prob002 | Template Design | BOTH | 出典論文自体がILP・CP両アプローチを比較検討したものであり、両手法とも実際に妥当（技術選定の不確実性は低い）。ビンパッキング系で構造的難易度も低いが、対象業種（印刷・製造業のカッティングストック）がニッチ。 |

### 優先度Low

| CSPLib ID | 問題名 | 推奨手法 | 根拠 |
|---|---|---|---|
| prob078 | Train Traffic Rescheduling | CP | 推奨手法はCPで単一・構造的難易度も低いが、対象顧客が鉄道事業者に限定され裾野が狭い。 |
| prob115 | Tail Assignment | CP | 推奨手法はCPで単一だが、航空機材運用という専門領域で対象顧客が航空会社に限定される。当社の想定顧客層（中小〜中堅の個別業務ヒアリング型）とややミスマッチ。 |
| prob056 | Synchronous Optical Networking (SONET) | CP | 推奨手法はCPで単一だが、通信網設計というインフラ技術者向けの専門領域で、業務ヒアリング形式との相性が低い。 |
| prob047 | Supply Chain Coordinations | CP（caveatあり） | CSPLib文献は分散制約最適化（DCOP）が出典だが、`_meta.caveat_ja`が明記する通り実務ではMIPが主流の可能性が高く、CP前提での実装が実務要件とずれるリスクがある。抽象度も高く業務ヒアリングへの翻訳が難しい。 |
| prob077 | Stochastic Assignment and Scheduling | CP | 不確実性下の計画という構造が、現行DSL/ヒアリング形式（決定論的入力を前提）と根本的に相性が悪い。確率分布等の追加入力を扱うにはヒアリングシート自体の設計変更が必要になる可能性が高く、単独ドメイン追加以上のコストがかかる。 |
| prob086 | Capacitated Vehicle Routing Problem (CVRP) | BOTH | 配送ルーティングという業務領域は、既存のTruckDispatcher（CP Optimizer実装）が既にカバーしている。CSPLib準拠の別実装を追加する実利が薄い。 |
| prob131 | Production Line Sequencing | CP | `extension_of: "LineChangeoverScheduler"`と既に紐づけ済み。新規ドメイン登録という枠ではなく、既存ドメイン拡張（パターン3、extension_gaps経由）で扱う方が構造的に適切。 |

## 次のアクション（提案）

1. ~~まずprob089（MASP）を単独で1件登録し、パイプラインの現状の完成度（structural_requirements等の改善効果）を確認する。~~ → **2026-08-08完了**（MedicalAppointmentScheduler登録済み。詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md参照）。
2. ~~089が完了したため、同じ顧客層への追加提案としてprob091（MASSP）を続けて検討する（未着手）。~~ → **2026-08-08完了**（MedicalAppointmentSequenceScheduler登録済み）。
3. ~~prob004・prob051はリスクが低いため、登録の合間の「速度検証」枠を兼ねて着手しても良い。~~ →
   **prob004は2026-08-09完了**（MysteryShopperScheduler登録済み。hearing_dsl_gaps差分渡し
   検証のベースデータとしても活用、詳細はENGINEERING_LOG.md参照）。
4. **prob059は2026-08-09完了**（EnergyCostAwareScheduler登録済み。debug_agent edit_file
   A/B実測・hearing_dsl_gaps差分渡し本番配線の検証も兼ねた、詳細はENGINEERING_LOG.md
   2026-08-09追記4参照）。
5. **prob051は2026-08-10完了**（TankAllocationPlanner登録済み。Gate2ルーティング修正
   （#30：動的検証系の指摘が混在していても他カテゴリの指摘はdebug_agentへ回すよう修正）と
   登録後修正フローの文脈保存（#31設計3-2の最小実装）を、実際の登録フローで検証する
   目的も兼ねた。詳細はENGINEERING_LOG.md 2026-08-10追記2参照）。**優先度High表は全件完了**。
6. ~~prob060（Ridesharing）はprob082の経験（構造的ギャップの事前チェック）を必ず先に参照してから着手すること。~~ → **2026-08-16完了**（RideshareMatchingPlanner登録済み。運転手固有デポ・実距離目的関数はClaudeがCP Optimizerモデルを直接記述して解消。詳細はENGINEERING_LOG.md 2026-08-16追記1参照）。次の候補としては優先度Medium表のprob066・prob030・prob002が残っている。
