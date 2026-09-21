# OptiBuddy Development Guide（開発編）
**対象バージョン**: V8.5（Backend V8.5 / domain_generator V4.4 / registry V7.0）
**対象読者**: OptiBuddyで業務最適化アプリを開発する人 / OptiBuddyをBackend APIとして利用する開発者 / OptiBuddy自体を開発・保守する人 / Claude（AI）

> 対になるドキュメント: `OptiBuddyユーザーマニュアル_V8.5.md`（業務担当者・エンドユーザー向け）。
> 本書はそのユーザーマニュアルが「裏側でどう動いているか」「何が自動で何が手動か」「ハマりやすい箇所」を扱う。

---

## 0. この文書の立ち位置

ユーザーマニュアルでは「ユーザーはヒアリングシートに記入するだけで、AIが全部やる」と説明している。これは**エンドユーザーから見た実態として正しい**。本書はその裏側、つまり

- 「AIが全部やる」を実現しているコード（`domain_generator.py`）はどう動いているか
- どこまでが本当に自動化されていて、どこに人間（開発者）の確認・介入が必要か
- 新ドメイン・新シナリオ・新extensionを追加する際に守るべきコーディングルール
- OptiBuddyをBackend APIとして外部システムから叩く場合の制約

を扱う。

---

## 1. 全体アーキテクチャ

### 1-1. 4層DSLパイプライン

```
Business DSL（ユーザー入力 / ヒアリングから生成）
  ↓ dsl_transformer/{domain}_converter.py    or  business_to_solver.py 内分岐
Solver Input DSL
  ↓ solvers/{domain}_solver.py
Solver Output DSL
  ↓ dsl_transformer/{domain}_ui_converter.py  or  solver_to_ui.py 内分岐
UI DSL（フロントエンドが描画する最終形）
```

### 1-2. ドメインの3つの実装方式

新ドメインは以下の3方式のいずれかで実装される。**どの方式を選ぶかが実装量とハマりどころを決める最重要判断**。

| 方式 | 該当ドメイン | converter/ui_converter | app.py登録先 | 特徴 |
|---|---|---|---|---|
| **A: YardPlanning統合方式** | YardPlanning, EventStaffing | `business_to_solver.py` / `solver_to_ui.py` 内の `if problem_class ==` 分岐 | `_try_dynamic_solver`にも到達しない（`_MANAGED`に直接ハードコード） | `BaseConstraintApplier`・`interval_var`ベース・Decomposer対応 |
| **B: Legacy単独方式** | GhostKitchen, ProjectPlanner, BinPacking | 専用converterなし（DSLをそのままソルバーに渡す） | `_LEGACY_SOLVERS` | `integer_var`ベース・`tasks: []`固定・UI専用ビュー必須 |
| **C: 4DSL準拠方式（新世代・推奨）** | ProductionLotScheduler, TruckDispatcher, HospitalShiftPlanner（旧CapacitatedVehicleRoutingProblemは2026-07-09に削除・TruckDispatcherに置き換え済み） | `dsl_transformer/{domain}_converter.py` + `{domain}_ui_converter.py`（専用ファイル） | `_DSL4_SOLVERS` または `_solve_4dsl_generic()` | `ui_dsl`を正しく構築・`is_dsl4`フラグで判定・今後の新規ドメインはこちら一択 |

**新規ドメインを追加する場合、原則C方式を使う。** A/B方式は既存ドメインの保守のために残っている旧設計であり、新規には採用しない。

### 1-3. registry.py（V7.0）の役割と限界 ── 「どこまで自動か」

ここがコーディング上、最も誤解しやすい点なので明確にする。

**自動化されている部分**：
- `Backend/solvers/*_solver.py` を起動時に**ファイルスキャンして自動import・自動登録**する（`registry.py` の `_register_all_solvers()`）。
- ソルバークラス名が `{PascalCase}Solver` であれば、`registry.py` へのコード追記は一切不要。
- `SolverRegistry.solve(solver_input)` を直接呼ぶ経路（`solver_input["metadata"]["solver_hint"]` で名前指定）であれば、これだけで新ソルバーが使える。

**自動化されていない部分（重要）**：
- 実運用上の唯一のエントリポイントである **`POST /baseline` のルーティングは `registry.py` を経由しない。** `app.py` 内の `_DSL4_SOLVERS` / `_LEGACY_SOLVERS` という**辞書**と、それぞれに対応する `_solve_xxx(dsl, issue_statuses)` という**専用関数**が必要。
- つまり「ソルバークラスを書けば動く」は `registry.solve()` 直叩きの場合のみ真。`/baseline` 経由（フロントエンドが実際に使う経路）では、**`app.py`側の配線が別途必要**。
- `registry.py` の自動discoveryは「`/health` での一覧表示」「将来的な直接呼び出し」のためのものであり、本番のリクエストフローを直接は左右しない。（2026-07-09時点では`_DSL4_DISPLAY_ONLY`は空。以前は旧CVRPが表示専用エントリとして手動追加されていたが、ドメイン削除に伴い削除済み）。

```python
# registry.py 抜粋（V7.0、2026-07-09時点は空）
_DSL4_DISPLAY_ONLY: dict[str, str] = {
    # 旧CapacitatedVehicleRoutingProblemエントリは削除済み。表示専用（実行は app.py 側が担う）
}
```

**この`app.py`側の配線は、新規業務登録フロー（`/api/domain/run`）を使う場合は`domain_generator.py`が自動パッチする（後述4章）。手動で新ドメインを足す場合は、この配線を自分で書く必要がある。**

---

## 2. 新ドメイン追加：手動実装する場合のルール

`/api/domain/run`（新規業務登録UI）を使わず、AI（Claude）への直接指示やコード手書きで新ドメインを追加する場合に守るべきルール。C方式（4DSL準拠）を前提とする。

### 2-1. ソルバー出力構造を最初に決める ⚠️ 最重要

フロントエンドは元々 `tasks[]`（時系列タスク配列）を前提に設計されている。

| パターン | 該当 | 対応 |
|---|---|---|
| **tasks[]が自然に使える** | スケジューリング系（開始・終了時刻がある） | `ui_dsl` の `tasks` にそのまま乗せられる |
| **tasks[]が合わない** | 施設配置・選択・ルーティング系 | `tasks: []` を固定で返し、`solutions[0]` 配下にドメイン固有構造を持たせ、専用Viewで直接読む |

判断基準：「ソルバー出力を、開始・終了時刻のあるタスクとして表現できるか？」できなければ後者。

### 2-2. 作成・修正ファイル一覧（C方式）

**Backend**

| ファイル | 内容 | 自動 / 手動 |
|---|---|---|
| `Backend/solvers/{snake}_solver.py` | ソルバー本体。`{Pascal}Solver` クラス、`solve()` が `status: "ok"|"infeasible"` を返す | 手動 or LLM生成 |
| `Backend/dsl_transformer/{snake}_converter.py` | Business DSL → Solver Input DSL | 手動 or LLM生成 |
| `Backend/dsl_transformer/{snake}_ui_converter.py` | Solver Output DSL → UI DSL（`ui_dsl["domain"]` に snake_case をセット） | 手動 or LLM生成 |
| `Backend/app.py` | `_solve_{snake}()` 関数 + `_DSL4_SOLVERS` への1エントリ追加 | **手動配線必須**（2-3節参照） |
| `Backend/solvers/base/issue_rules.py` | `ISSUE_RULES["{Pascal}"]` + `build_{snake}_contexts()` | 手動 or LLM生成 |
| `Backend/solvers/base/objective_terms.py` | `OBJECTIVE_RECIPES["{Pascal}"]`（直接実装なら空配列でよい） | 手動（最小1行） |
| `Backend/dsl_repository/scenarios/{snake}_baseline.json` 等2本 | シナリオJSON（baseline/infeasible。2026-07-18よりtight廃止） | 手動 or LLM生成 |

**Frontend**

| ファイル | 内容 |
|---|---|
| `Frontend/src/app/studio/types.ts` | `StudioMode` に新モードID追加 |
| `Frontend/src/app/studio/components/StudioModeTabs.tsx` | タブ定義 + `problemClass` 分岐 |
| `Frontend/src/app/studio/views/{Pascal}View.tsx` | 専用ビュー新規作成 |
| `Frontend/src/app/studio/StudioShell.tsx` | import + レンダリング分岐 |
| `Frontend/src/app/studio/views/OverviewView.tsx` | `isXxx` フラグ + `IssueMonitorPanel` 分岐 |
| `Frontend/src/app/HomeScreenNew.tsx` | `TAG_MAP` への1エントリ追加 |

### 2-3. app.py への配線（手動の場合の正しい書き方）

```python
# ① 専用関数を _DSL4_SOLVERS マーカーの直前に追加
def _solve_new_domain(dsl, issue_statuses):
    try:
        from dsl_transformer.new_domain_converter import convert_new_domain_to_solver
        from solvers.new_domain_solver import NewDomainSolver
        from dsl_transformer.new_domain_ui_converter import convert_new_domain_to_ui

        solver_input = convert_new_domain_to_solver({**dsl, "issue_statuses": issue_statuses})
        result       = NewDomainSolver(solver_input).solve()
        ui_dsl       = convert_new_domain_to_ui(result, business_dsl=dsl)

        return jsonify({
            "status": result.get("status", "ok"), "tasks": [], "makespan": 0,
            "issues": result.get("issues", []), "containers": [],
            "solutions": result.get("solutions", []), "ui_dsl": ui_dsl,
            "resolved": [], "skipped": [], "skip_reasons": {},
        })
    except Exception as e:
        logger.error(f"[NewDomain] エラー: {e}", exc_info=True)
        return jsonify({"status": "validation_error", "issues": [
            {"id": "config-error", "severity": "CRITICAL", "title": "NewDomain 設定エラー",
             "message": str(e), "relatedContainerIds": []}]}), 200

# ② _DSL4_SOLVERS 辞書に1行追加
# 注（2026-07-09）: 命名規約（{snake}_converter.py/{snake}_solver.py/{snake}_ui_converter.py）に
# 従うドメインは _solve_4dsl_generic() が自動ルーティングするため、この辞書への明示
# 登録は本来不要（TruckDispatcherがその例）。ここでの手動配線が必要なのは命名規約に
# 従わない例外的なケースのみ（ProductionLotSchedulerなど）。
_DSL4_SOLVERS = {
    "ProductionLotScheduler": _solve_production_lot_scheduler,
    "NewDomain":              _solve_new_domain,   # ← 追加
}
```

**注意**: `app.py` 冒頭のコメントに「新規4DSLドメインはここのルーティングに1行追加するパターンで拡張」と明記されている。これは設計上の規約であり、勝手に別の分岐方式（`if/elif`の追加など）を作らないこと。`_DSL4_SOLVERS` / `_LEGACY_SOLVERS` の2つの辞書とマーカーコメント（`# ── 新規4DSLドメインはここに追加`）を維持すること。**このマーカーコメントは `domain_generator.py` の自動パッチが目印として使うため、削除・改変すると新規業務登録フローが壊れる。**

### 2-4. issue_rules.py への追記パターン

```python
ISSUE_RULES["NewDomain"] = [
    IssueRule(
        rule_id="solve_failed",
        condition=lambda ctx: ctx.get("solution_data") is None,
        build=lambda ctx: {
            "id": "solve_failed", "severity": "CRITICAL",
            "title": "最適解なし",
            "message": "制約を満たす解が見つかりませんでした。",
            "relatedContainerIds": [],
        },
    ),
]

def build_new_domain_contexts(solution_data, ...) -> list[dict]:
    contexts = [{"solution_data": solution_data}]
    # ドメイン固有のチェック対象をcontextとして追加
    return contexts
```

### 2-5. ナップサック系ドメインの「常に解がある」問題

ProjectPlanner（多次元ナップサック）のように「何も選ばない」が常に実行可能解になってしまう問題クラスでは、制約が厳しすぎてもソルバーが `status: "ok"` で「全非採択」を返してしまい、Infeasibleとして検知されない。これを防ぐため、ソルバー内で以下のような最低選択数制約を入れること。

```python
mdl.add(mdl.sum(list(select_vars.values())) >= 1)
```

選択・割り当て・配置系の新ドメインを実装する際は、同様の「自明な空解」が存在しないか必ず確認する。

---

## 3. CP Optimizer 実装ルール（既知のバグパターン）

LLMがソルバーコードを生成する際、繰り返し発生する典型的なバグがある。`domain_generator.py` の `_sanitize_solver_code()` が一部を静的に自動修正するが、**自動修正されないパターンもあるため、手書き・レビュー時は以下を確認すること。**

### 3-1. 自動修正される（サニタイザーがカバー）

| バグ | 誤り | 正しい形 |
|---|---|---|
| `presence_of()` を論理式として比較 | `mdl.presence_of(x) == 1` | `mdl.presence_of(x)` |
| `if_then` に制約式を渡す | `mdl.if_then(cond, mdl.end_before_start(a, b))` | `mdl.end_before_start(a, b)`（optional intervalなら absent 時に自動無効化される） |

### 3-2. 自動修正されない（手動レビュー必須）

| バグ | 誤り | 正しい形 |
|---|---|---|
| `no_overlap` に interval リストとtransition matrixを直接渡す | `mdl.no_overlap(interval_list, transition_matrix)` | `sequence_var` を経由する：`seq = mdl.sequence_var(interval_list, types=...)` → `mdl.no_overlap(seq, transition_matrix)` |
| `if_then` の第2引数に非boolean式（`end_of(a) <= start_of(b)` など）を渡す | エラーになる | `if_then` を使わず、optional intervalの `presence_of` を利用した制約に書き換える、または該当制約をハード制約として直接 `mdl.add()` する |

サニタイザーは `no_overlap` の誤用を検知した場合、自動修正はせず**ログに警告を出すだけ**である（`_CPO_WARN_PATTERNS`）。生成後は必ずログを確認すること。

### 3-3. ステージ2プロンプトへの反映

新規にLLMへソルバー生成を依頼する際（手動プロンプトでもUI経由でも）、上記の禁止パターンをプロンプトに明示すること。`domain_generator.py` の `_build_stage2_prompt()` は既にこれをシステムプロンプトに含めているため、`/api/domain/run` 経由であれば自動的に注意喚起される。手動でClaudeに依頼する場合は、本章の表をそのまま貼って伝えるとよい。

---

## 4. 新規業務登録フロー（`/api/domain/run`）の内部実装

ユーザーマニュアルでは「ヒアリングシートを書けばAIが全部やる」と説明しているが、内部では以下の3段階で処理される。開発・デバッグ時にどの段階で失敗しているかを特定するために必要な知識。

### 4-1. Stage 1a：問題分類（`interpret_hearing`）

ヒアリング内容をLLMに渡し、以下の3パターンに分類する。

| match_type | 判定基準 | 結果 |
|---|---|---|
| `existing_domain` | 固定4ドメイン（YardPlanning, TruckDispatcher, NurseShiftWeeklyCap, LineChangeoverScheduler）にそのまま当てはまる、または追加候補ドメイン一覧（DB起点で動的に提示される既存登録ドメイン）に具体的に一致する（後者が優先） | Stage 1b へ（シナリオ生成のみ） |
| `base_problem` | CSPLib基底問題（`docs/CSPLIB_REFERENCE.md`参照。旧`primary_problems.md`は2026-08-06に統合・廃止）のうち、既存ドメインへの転用先が明記されているもの（2026-08-06時点ではRCPSP→LineChangeoverSchedulerの1件のみ）に当てはまる | Stage 1b へ |
| `new_domain` | どちらにも当てはまらない | Stage 2 へ（コード生成） |

【2026-08-06本修正】上記2点（固定ドメイン名・base_problem対応）は、実際に分類ロジックを
動かしている `domain_generator.py` の `_STAGE1A_SYSTEM`（classify_problem()のシステム
プロンプト、2914行目付近）の記述に基づく最新状態。旧EventStaffing/GhostKitchen/
ProjectPlanner/BinPackingは全て削除済み、旧CapacitatedVehicleRoutingProblemは
TruckDispatcherに統合済み。CVRP→TruckDispatcherのbase_problem対応（prob082 DARP系）は
一度追加されたが、構造上拡張が成立しないと判明し撤回されている（`_STAGE1A_SYSTEM`内の
2026-08-06コメント参照）。

**`BASE_PROBLEM_MAP`（`domain_generator.py` 133行目付近）について**: 以前は基底問題→実装
ドメインの対応表として本節が紹介していたが、実際には**この辞書はどこからも参照されていない
死んだコード**であることが判明した（`grep`で`BASE_PROBLEM_MAP`の全参照箇所を確認したところ、
定義箇所とコメント以外に読み出し箇所が無い）。実際の分類ロジックは`_STAGE1A_SYSTEM`の
プロンプト文字列に直接ハードコードされており、上記の表はこちらの内容に基づく。そのため
辞書自体の内容（現在は`{"RCPSP": "LineChangeoverScheduler", "CVRP": "TruckDispatcher"}`）は
`_STAGE1A_SYSTEM`側の最新状態（CVRP側は撤回済み）と食い違ったまま残っている。新たに基底問題の
対応を追加・変更する場合は、この未参照の辞書ではなく`_STAGE1A_SYSTEM`のプロンプト文字列
（および必要なら`csplib_cp_mip_reference.json`）を直接編集し、本書・ユーザーマニュアルの
両方を更新すること。`BASE_PROBLEM_MAP`辞書自体の削除（死んだコードの整理）はスコープ外として
別タスクに残す。

ヒアリングに付加できる技術情報（CSPLib ID・formulation_directive・構造要件チェックリスト等）の
正式な一覧・優先順位は `docs/HEARING_TECHNICAL_INFO_FLOW.md` を参照すること。

### 4-2. Stage 1b：既存ドメインの変種（シナリオ生成のみ）

`apply_scenarios()` が呼ばれ、シナリオJSONをファイル書き込み + DB登録するだけで完了する。コード生成は発生しない。**ここが「数十秒で完了」する理由。**

### 4-3. Stage 2：完全新規ドメイン（コード生成）

`generate_domain_files()` がLLMにプロンプトを送り、以下のマーカー形式で応答を受け取る。

```
===FILE:Backend/solvers/new_domain_solver.py===
（ファイル内容全文）
===END===

===PATCH:Backend/app.py===
INSTRUCTION: _DSL4_SOLVERS に追加
===CODE===
def _solve_new_domain(dsl, issue_statuses):
    ...
===END===

===SCENARIOS===
{"baseline": {...}, "infeasible": {...}}
===END===
```

`_parse_marker_output()` がこの形式を正規表現でパースする。**LLMにこの新ドメイン関連の指示を出す際、この出力形式を厳守させること。** マーカーの綴り（`===FILE:`、`===PATCH:`、`===SCENARIOS===`、`===END===`）がずれるとパースに失敗し、何も生成されない。

### 4-3b. Gate 2：検証（2026-07-09追加）

`generate_domain_files()` 直後、`apply_domain_files()` の前に自動検証が入る。

1. 静的チェック（`scan_diffs_for_warnings()`）: converter出力キーとsolver参照キーのAST突き合わせ、CP Optimizer既知バグパターンの検知
2. 動的チェック（`run_gate2_dynamic_verification()`）: baseline/infeasibleを2ファイルを一時書き出しして実際にソルブし、feasibility期待値との不一致を確認（検証後、一時ファイルは削除）。
   2026-07-18追記: 従来はtightシナリオも含めた3ファイルで、tightのKPIがbaselineと完全一致していないかも確認していたが、tightは設計意図が曖昧になりやすく登録所要時間の増加に見合う効果が薄いため廃止した（baseline/infeasibleの2シナリオ原則に変更）。既存ドメインのtightシナリオは遡及削除していない。

両方の警告はGate1のmissing_infoと同じ`needs_confirmation`の`questions`に合流し、人間の確認画面は1回のまま。詳細と、警告が出た場合の再投入手順は `docs/DESIGN_2026-07-09_registration_gates_and_hard_soft.md`（7節）を参照。

**設計原則（2026-09-21追加）: 「コードを読まないと判断できない」検出はLLMの一次判定を挟んでからblockingにする。**
静的チェックの中には、大半のカテゴリ（数量hard/soft、フィールド未使用等）のように業務担当者が
自分の入力・意図と照合して答えられるものと、「no_overlapとcumulativeが同一資源にかかっているか」
のようにコードの構造そのものを読まないと判定できないものが混在する。後者をそのままblockingで
確認画面に出しても、業務担当者には原理的に判断材料が無く、正しい応答を期待できない
（2026-09-21、RideshareMatchingPlannerの`resource_sharing_conflict`誤検知で発覚、Koshoshi指摘）。

このタイプのチェックを新設・昇格する場合は、正規表現/ASTによる一次検出の後段に、実際のコードを
読んで意味的に判定するLLM検証関数を挟み、その判定が「真に問題あり」の場合のみblocking_questions
に回す設計とする（参考実装: `humanize.py`の`_verify_resource_sharing_conflict_with_llm()`）。
LLM呼び出し失敗・パース不能時は必ず安全側（blocking維持）にフェイルセーフすること。

人間側の確認画面・`force_apply`による上書き機構（4-3c参照）は新設不要——既存のGate2 blocking
フローがそのまま使える。LLM一次判定を挟むことで、人間が画面で行うのは「実質的な技術判断」ではなく
「LLMが既に判定した内容の形式的な承認（必要ならforce_applyで上書きも可能）」になる、という位置づけ
の変化である。

### 4-3c. Confirm／自動操縦モード（auto_resolve）（2026-09追加）

`needs_confirmation` に到達した後の確認フローには2種類ある。

1. **手動確認**: 人間が画面で指摘事項を読み、回答またはforce_applyを選択する。
2. **自動操縦（`auto_resolve=True`）**: `_run_auto_pilot()` がジョブをポーリングし、`_auto_decide_confirm_action()`（LLM）に「続行／force_apply／取りやめ」を判断させ、`_run_confirm_job()` へディスパッチする。最大2ラウンドまで自動続行。

開発時に踏んだ落とし穴（2026-09-19実機テストで発見・修正、詳細は `claude/STATUS_2026-09-20_patient_transport_planner_autopilot_investigation.md` 参照）:

- **生成コードの切り捨て**: `_auto_decide_confirm_action()` が判断材料としてLLMに渡す生成コードのdiffは、以前は先頭4,000文字で機械的に切り捨てていた。実際のsolverファイルは11,979〜70,459文字あり、ほぼ確実に途中で切れた状態で判断LLMに渡っていた（自動判断理由に「ソルバーコードも途中で切れていて検証できない」と出る症状で発覚）。現在は80,000文字に引き上げ済み（`_format_diff_for_autopilot()`）。今後solverファイルがさらに肥大化した場合は再度上限を見直すこと。
- **debug_agentのmax_turns**: `_run_confirm_job()` の続行ラウンドは、以前は `max_turns` が常に固定8だった。原因不明系の指摘（`_DYNAMIC_STRUCTURAL_PREFIXES` に該当するもの＝実行時例外やfeasible不一致など、debug_agent自身が原因調査してから直す必要があるもの）が含まれる場合、指摘件数がわずかでもターン切れ（`reason=max_turns`）することがある。現在は該当する場合のみ `_DEBUG_AGENT_MAX_TURNS_WITH_DIAGNOSTIC_FINDING`（12）に動的に引き上げている（値は実測に基づく初回見積もり、今後調整前提）。
- **フロントエンドのポーリング停止**: `RegisterModal.tsx` で、`needs_confirmation`／`waiting_for_agent_question` 到達後もauto_resolveジョブはポーリングを継続する設計だったが、React `useEffect` のcleanupが自身が直後にセットした継続用タイマーを `clearTimeout` してしまい、画面が最初の確認画面のまま固まっていた（バックエンドは正常にdone/cancelledへ到達していても画面に反映されない）。`suppressNextPollCleanupRef` フラグで対策済み。

### 4-4. Apply：ファイル書き込みと自動パッチ（`apply_domain_files`）

ここが「ユーザーはコードを書かない」を実現している中核。承認された差分（`approved_diffs`）と追記パッチ（`approved_patches`）を実際のファイルに書き込む。

**自動で行われること**：

1. `*_solver.py` には `_sanitize_solver_code()` が自動適用される（3章のバグ修正）
2. `Backend/app.py` への追記は、`instruction` 文字列に `"_DSL4_SOLVERS"` が含まれていれば `_patch_app_py_dsl4()`、`"_LEGACY_SOLVERS"` が含まれていれば `_patch_app_py_legacy()` が呼ばれ、専用関数の挿入 + 辞書への1行追加を**マーカーコメント基準で**自動実行する
3. `business_to_solver.py` / `solver_to_ui.py` への分岐追加は `_patch_converter_dispatch()` が同様にマーカー（`# ── 新規4DSLドメインはここに追加 ──`）基準で自動挿入する
4. `Frontend/HomeScreenNew.tsx` の `TAG_MAP` への追加は `_patch_home_screen_tag_map()` が、ファイル内の `// ↓ 自動登録ドメインはここに自動追加される` というマーカーコメントを目印に自動挿入する
5. 同名パッチが既に適用済みの場合（追記コードの先頭60文字が既存ファイル内に存在する場合）はスキップされ、二重適用を防ぐ
6. `EXISTING_DOMAINS` / `FOUR_DSL_DOMAINS`（メモリ上の辞書）もシナリオファイル・converterファイルの存在から自動更新される

**自動化されないこと（開発者が知っておくべき制約）**：

- 上記の自動パッチはすべて**マーカーコメントの存在に依存する**。`Backend/app.py` 内の `# ── 新規4DSLドメインはここに追加` や `Frontend/HomeScreenNew.tsx` 内の `// ↓ 自動登録ドメインはここに自動追加される` を誤って削除・改変すると、それ以降の新規ドメイン追加で「マーカーが見つかりません。手動で追加してください」という警告が出て、その部分だけ手動対応が必要になる。
- `Frontend`側のビュー（`{Pascal}View.tsx`）・タブ定義（`StudioModeTabs.tsx`）・`StudioShell.tsx`の分岐・`OverviewView.tsx`の分岐は、**LLMが生成したコードを`approved_diffs`としてファイル丸ごと書き込む**形であり、`app.py`のような「既存コードへの行挿入パッチ」ではない。新規ファイルなら問題ないが、既存ファイルへの追記が必要な箇所（`StudioModeTabs.tsx`の分岐追加など）は、LLMが正しく既存コードを読み取った上で全文を再生成する必要がある。**Stage2プロンプトに渡すrepomix（添付コード一式）が古いと、ここで既存の他ドメイン分岐を消してしまうリスクがある。**
- `repomix-domain-addition.xml` はハッシュベースで差分管理されており（`upload_repomix_if_changed`）、コードベースに変更があった場合のみ再アップロードされる。**ローカルでコードを直接編集した直後にUI経由の新規業務登録を行うと、repomixが古いままLLMに渡ることがある**。`_ensure_repomix()` が `repomix --config repomix.domain-addition.json` を自動実行して再生成を試みるが、`repomix` コマンド自体がPATHに無い環境では黒く失敗してログ警告のみで処理が継続する点に注意。

### 4-5. デバッグ時の確認ポイント

新規業務登録が失敗した場合、どの段階かをまず特定する。

| 症状 | 疑うべき段階 |
|---|---|
| ヒアリングを送っても応答がない / タイムアウト | Stage 1a（LLM呼び出し自体の失敗、`llm_client.py`のプロバイダ設定） |
| `match_type` が想定と違う（既存ドメインなのに新規扱いになる等） | Stage 1a の分類プロンプト（`_STAGE1A_SYSTEM`）の判定基準 |
| Stage 1bなのにシナリオが登録されない | `apply_scenarios()` の `_register_scenarios()`、DB側の重複チェック（`existing_names`） |
| Stage 2でコードは生成されるが`app.py`に反映されない | `instruction` 文字列に `"_DSL4_SOLVERS"` / `"_LEGACY_SOLVERS"` の文言が含まれているか、マーカーコメントが残っているか |
| 生成は完了するが自動でapplyまで進まず`needs_confirmation`で止まる | Gate 2（4-3b節）の静的・動的チェックが警告を出している。`questions`の「（自動検知）」/「（自動検知・動的検証）」接頭辞を確認 |
| ソルバーコードがCP OptimizerでエラーになるI | 3章のサニタイザーがカバーしない `no_overlap` / `if_then` パターンを手動確認 |
| 自動操縦（auto_resolve）の画面が確認待ちのまま進まない | フロントエンドのポーリング継続バグ（4-3c節）。バックエンドのjob状態は正常な場合が多いので、まずDBの `domain_jobs` テーブルで実際のstageを確認する |
| ホーム画面にタグが出ない | `TAG_MAP` パッチのマーカーコメント、または `problem_class` 文字列の不一致（大文字小文字・スペース） |

---

## 5. extensions（拡張モジュール）の実装ルール

ユーザーマニュアルではextensionsを「概念」としてのみ説明し、ユーザーが直接編集することは想定していない。ここでは開発者がextensionを追加・実装する際のルールを記載する。

### 5-1. extensionsの仕組み

extensionsはYardPlanning（A方式）のみで使われている仕組みで、`BaseConstraintApplier` に対する制約ブロックの動的な組み合わせとして実装されている。Business DSLの `extensions: []` 配列に列挙された名前に応じて、対応する制約適用関数が呼ばれる。

```json
{
  "problem_class": "YardPlanning",
  "extensions": ["physical_space", "phase_separation", "crane_interference", "attribute_zones", "shift"]
}
```

### 5-2. 新しいextensionを追加する場合

1. `Backend/solvers/base/` 配下に制約ロジックを実装する関数を追加する（既存の `physical_space` 等の実装パターンに揃える）
2. `BaseConstraintApplier` の適用ディスパッチにextension名を追加する
3. `issue_rules.py` に、そのextensionが生む可能性のあるイシューの検知ルールを追加する（例: `attribute_zones` → `attr_reefer` / `attr_imo` / `attr_oog`）
4. extensionはYardPlanning専用の仕組みであり、B方式・C方式のドメインには存在しない。B/C方式のドメインで同様の「制約の組み合わせ」をしたい場合は、ソルバー内で直接if分岐するか、DSLの `config` フィールドにフラグを持たせる方式を取ること（無理にextensions機構を流用しない）。

### 5-3. extensionsをユーザー向けに開放しないこと

ユーザーマニュアルの方針上、extensionsはAIが業務登録時に自動選択するものであり、エンドユーザーがJSON上で直接編集する運用は前提としていない。開発者がデバッグ目的でDSLのJSONを直接編集するのは問題ないが、UIから直接extensionsを編集できる機能を追加する場合は、ユーザーマニュアルとの整合性（「ユーザーはextensionsを意識しない」という説明）を崩さないよう、AIチャット経由の自然文インターフェースとして実装すること。

---

## 6. シナリオ追加のルール

### 6-1. シナリオJSONの必須フィールド

```json
{
  "problem_class": "DomainName",
  "domain": "domain_name_snake",
  "meta": { "instance_name": "...", "note": "..." },
  "config": { ... },
  "...ドメイン固有データ..."
}
```

`problem_class` と `domain` は両方必須。`app.py` のルーティングは `problem_class` を見るが、UI側（タグ表示等）は `domain`（snake_case）も参照するため、両方を一致させて記載すること。

### 6-2. シナリオ原則と命名規則 ⚠️ 重要

新ドメインには最低2シナリオ（標準的な条件・明らかに無理な条件）を用意し、正常系・Infeasible処理の2パターンを一通り確認できるようにする。

2026-07-18追記: 従来は「厳しめの条件（tight）」を加えた3シナリオ原則だったが、tightは「際どいが解ける」の設計意図がLLM生成時に曖昧になりやすく、Gate2の再検証・デバッグエージェントの往復にかかる時間の増加に見合う効果が薄いと判断し、tightの生成・検証を廃止した（新規ドメインのみ。既存ドメインのtightシナリオは遡及削除しない）。以下の命名規則自体（結果を含意する名前を付けない）は2シナリオでも変わらず適用する。

**ただし、ファイル名・`instance_name`に `baseline` / `tight` / `infeasible` のような「期待される結果」を含む名前を付けてはいけない。**

理由: シナリオ作成時点ではあくまで「狙い」（こういう条件にすればこうなるはずだ）でしかなく、実際にその通りの結果になるかはソルバーを実行してみないと分からない。さらにソルバーのロジック（ヒューリスティック・割り当て順序など）が後から修正されると、同じシナリオでも結果が変わりうる。結果を含意する名前を付けると、「ラベル通りに動かない＝バグ」という誤解を招き、実際にはソルバーが正しく動いている場合（例: `tight`という名前だが実は容量に余裕があり楽に解けた）にまで無用な調査コストが発生する。逆に本物のバグが起きていても「名前通りなので問題ない」と見過ごされるリスクもある。

CVRPドメイン（旧CapacitatedVehicleRoutingProblem実装。2026-07-09に削除されているが、命名規則の教訓としては引き続き参考になるので事例として残す）で実際に以下が発生した（V8.5時点の修正記録）。

- `cvrp_baseline.json`（「標準」の名前）が、Step4（車両割り当て）の貪欲法バグにより実際にはInfeasibleになっていた。バグ修正後はPlanによってfeasible/infeasibleが分かれる結果になった。
- `cvrp_tight.json`（「タイト」の名前）は、総容量に対し総需要が比較的余裕のある構成だったため、修正前のバグの影響を受けず、修正後も全Plan feasibleのまま変化がなかった。つまり「タイト」という名前ほどタイトな条件にはなっていなかった。
- `cvrp_infeasible.json`は車両1台に対し総需要が容量を大幅に超える構成のため、修正の前後を問わず常にInfeasibleだった（これは正しい「解なし」であり、修正対象のバグとは無関係）。

**命名規則**: ファイル名は `{snake}_scenario_01.json` のように意味を持たない連番にする。`instance_name` も同様に `"CVRP シナリオ #1"` のように中立化する。

| シナリオ | ファイル名の例 | 設計意図（作成時に書く） |
|---|---|---|
| シナリオ1 | `{snake}_scenario_01.json` | 標準的な条件を狙う |
| シナリオ2 | `{snake}_scenario_02.json` | 厳しめの条件（ストレステスト）を狙う |
| シナリオ3 | `{snake}_scenario_03.json` | 明らかに解けない条件を狙う |

**検証結果は `meta.note` に書き足す。** 新フィールドは追加しない（スキーマ変更や全シナリオ巡回更新のような追加の保守負担を発生させないため）。既存の `note` フィールドに「設計意図」と「検証結果」を両方書く運用とする。

```json
{
  "meta": {
    "instance_name": "CVRP シナリオ #1",
    "note": "【設計意図】標準的な配送条件を狙って作成。\n【検証結果】2026-06時点: Plan B/Cはfeasible、Plan A（旧来ヒューリスティック）はinfeasible。"
  }
}
```

`note` は「いつでも自由に上書きしてよいコメント欄」であり、ソルバーバージョンやテスト日時のような構造化フィールドは持たせない。ソルバーが更新されるたびに全シナリオを巡回して構造化フィールドを更新する運用は保守負債になるため、あえて自由記述に留める。検証結果を書く・書かないも含めて厳密な運用ルールにはしない。

ナップサック系ドメイン（5-5節参照）では、「解けない条件を狙ったシナリオ」が本当にInfeasibleになるか必ず実機確認すること。「最低1件選択」制約を入れていない場合、厳しい制約を設定したつもりでも空解で `status: "ok"` を返してしまう。これも上記と同じ理由（名前と実態の乖離）でファイル名による含意を避けるべき典型例である。

### 6-3. シナリオ追加API経由の場合

`/api/domain/run` で `match_type: "existing_domain"` または `"base_problem"` と判定された場合、Stage 1bのみが走り `apply_scenarios()` が直接ファイル書き込み + DB登録する。この経路ではコード生成（Stage 2）は発生しないため、既存のconverter/ui_converterのスキーマに収まらないシナリオ（新しいフィールドが必要等）を作りたい場合は、`match_type` が `existing_domain` と誤判定されないよう、ヒアリング内容に新規性を明示するか、手動で `new_domain` 相当のフローに乗せる必要がある。

---

## 7. OptiBuddyをBackend APIとして使う場合

ユーザーマニュアルのセクション10で案内している「カスタムUI構築」のための開発者向け詳細。

### 7-1. 主要エンドポイント

| エンドポイント | メソッド | 用途 |
|---|---|---|
| `/baseline` | POST | 最適化実行。`{"dsl": {...}, "issueActions": {...}}` を送る |
| `/relax` | POST | 制約緩和案の生成。`{"dsl": {...}, "issues": [...]}` |
| `/apply_patch` | POST | JSON Patch形式でDSLを更新 |
| `/ask` | POST | AIチャットへの質問。`{"question": "...", "dsl": {...}, "solution": {...}, "history": [...]}` |
| `/analyze` | POST | DSL全体の自動分析 |
| `/dsl_repository/scenarios` | GET / POST | シナリオ一覧取得 / 新規作成 |
| `/dsl_repository/registry` | GET | DSL定義・extension・進化ログの一覧 |
| `/health` | GET | 稼働確認 + 登録済みソルバー一覧（`registry.py`が自動discoveryした内容） |
| `/api/domain/run` | POST | 新規業務登録フルフロー（ヒアリング→分類→生成→適用を一括実行） |
| `/api/domain/interpret` / `/generate` / `/apply` | POST | `domain_run` の各段階を個別に呼ぶ場合（UIでステップごとに承認を挟みたい場合） |

### 7-2. レスポンス構造（`/baseline`）

```json
{
  "status": "ok",
  "tasks": [...],
  "makespan": 0,
  "issues": [...],
  "containers": [...],
  "solutions": [...],
  "ui_dsl": { "domain": "...", ... },
  "resolved": [...], "skipped": [...], "skip_reasons": {...}
}
```

C方式（4DSL準拠）のドメインでは `ui_dsl` に構造化データが入る。B方式（Legacy）のドメインでは `ui_dsl: {}` の固定で、`solutions[0]` 配下を直接読む必要がある。**外部システムからAPIを叩く場合、対象ドメインがどちらの方式か（本書1-2節の表）を事前に確認すること。**

### 7-3. 自前フロントエンドを構築する際の注意

- `tasks: []` が固定で返るドメイン（B/C方式の多く）では、KPI表示やガントチャート的なUIは使えない。`solutions[0]` の構造はドメインごとに異なるため、対象ドメインの `{domain}_ui_converter.py` を読んでフィールド名を確認すること。
- 認証機構は現状実装されていない。社内利用前提のAPIであり、外部公開する場合はリバースプロキシ等で認証層を追加すること。
- `/baseline` は1リクエストで完結する同期APIであり、大規模シナリオでは内部で `Decomposer`（Horizon/Spatial/Staff）による分割処理が発生し応答が遅くなることがある。タイムアウト設定は十分に長く取ること。

---

## 8. 制約緩和・再最適化のバックエンド実装

ユーザーマニュアルのセクション7で説明した「2つの再最適化経路」のバックエンド実装。

### 8-1. ① Issues / AIチャット経由（FIX/ACCEPT・`/ask`）

- `issueActions`（FIX/ACCEPT）は `/baseline` 呼び出し時に同送され、`normalize_issue_actions()` で正規化後、`ConflictResolver` が実際の緩和処理（YardPlanningの場合は `yard_swaps` 生成）を行う。
- AIチャット（`/ask`）からの提案適用は、AIが返す `dsl_patch`（JSON Patch形式）を `/apply_patch` で適用し、その結果のDSLで再度 `/baseline` を呼ぶ、という2段階のクライアント側フローになっている。バックエンド側で自動的に再最適化まで行うわけではない。

### 8-2. ② 制約構造の見直し（`/relax`）

- `suggest_relaxations()`（`llm/relax_interface.py`）がLLMに2〜3個の緩和候補を `dsl_patch` 形式で生成させる。
- フロントエンドはHuman-in-the-Loopで候補をプレビューし、ユーザーが選択したものを `/apply_patch` → `/baseline` の順で適用する。
- `localPatchedDsl` がクライアント側で複数回の緩和パッチを累積管理する。バックエンドは毎回ステートレスにパッチ適用結果を返すのみ。

### 8-3. それでもInfeasibleになる場合

①②いずれの経路でも、ソルバー自体は「与えられた制約のもとで解が存在するか」を都度ゼロから判定する。緩和や提案適用を繰り返してもInfeasibleが解消しない場合は、`issue_rules.py` の `solve_failed` ルールが再度発火する。これは設計上想定された挙動であり、バックエンド側に「N回緩和したら必ず解を返す」というフォールバックは存在しない（意図的に持たせていない。解が存在しない制約に無理やり解を作るとビジネス上の意味を失うため）。

GhostKitchenのみ、`KitchenParamPanel` 用に最大拠点数・カバー率目標・ソルブ時間上限をDSLの `config` 経由で直接調整できるショートカットがある。これは「Infeasible→AI対話→緩和提案」のループを通さずに済む、唯一のドメイン固有の例外的UIである。

---

## 9. 既存ドメイン実装メモ（リファレンス）

### 9-1. ProductionLotScheduler / TruckDispatcher（C方式の参考実装、2026-07-09更新）

最新の4DSL準拠ドメイン。ProductionLotSchedulerは `app.py` 内の `_solve_production_lot_scheduler()` を雛形として読むこと。TruckDispatcherは `_DSL4_SOLVERS` への明示登録を持たず、`_solve_4dsl_generic()` の規約ルーター経由で自動ルーティングされる（`truck_dispatcher_converter.py` → `TruckDispatcherSolver` → `truck_dispatcher_ui_converter.py` という 3段の呼び出し構造自体は変わらない）。app.pyへの手動配線が不要な分、こちらの方が今後の新規ドメインの標準パターンに近い。
（旧CapacitatedVehicleRoutingProblemおよびその `_solve_capacitated_vehicle_routing_problem()` 関数は2026-07-09に削除済み）

### 9-2. ProjectPlanner（B方式・ナップサック）

「最低1件選択」制約（2-5節）の実例。`Backend/solvers/project_planner_solver.py` を参照。期間制約は並列実行前提のため、合計でなく最長プロジェクトの期間で評価している点も実装上の注意点。

### 9-3. GhostKitchen（B方式・CFLP）

`KitchenParamPanel` というドメイン固有のInfeasible対応UIを持つ唯一の例。新ドメインで同様のパラメータ調整UIが必要かどうかは、設計判断チェックリスト（1-3節「Infeasible緩和の設計」相当）の時点で決めておくこと。

### 9-4. is_dsl4フラグの伝播経路

`domain_generator.py` の `domain_run()` 内で、Stage1aの分類結果 `classification.get("is_dsl4_candidate", True)` が `domain_def["is_dsl4"]` にマップされ、Stage2のプロンプト生成（`_build_stage2_prompt(..., is_dsl4=is_dsl4)`）に渡る。このフラグがLLMへの指示内容を分岐させ、C方式（専用converter生成）かB方式（直接実装）かを決める。**新規ドメイン追加でB/C方式どちらが生成されるか不安定な場合、まずこの`is_dsl4_candidate`判定がStage1aで正しく出ているかを確認すること。**

---

## 10. 実装チェックリスト（手動実装時）

### バックエンド
- [ ] `Backend/solvers/{snake}_solver.py` を作成（`{Pascal}Solver` クラス、`solve()` が `status` を返す）
- [ ] `Backend/dsl_transformer/{snake}_converter.py` / `{snake}_ui_converter.py` を作成（C方式の場合）
- [ ] `Backend/app.py` に `_solve_{snake}()` を追加し `_DSL4_SOLVERS`（or `_LEGACY_SOLVERS`）に登録
- [ ] `Backend/solvers/base/issue_rules.py` に `ISSUE_RULES["{Pascal}"]` と `build_{snake}_contexts()` を追記
- [ ] `Backend/solvers/base/objective_terms.py` に `OBJECTIVE_RECIPES["{Pascal}"]` を追記（空でも可）
- [ ] 選択・割り当て系の場合、「自明な空解」が存在しないか確認し、必要なら最低件数制約を追加
- [ ] 3章のCP Optimizer禁止パターンをコードレビューで確認（特に `no_overlap` / `if_then`）
- [ ] シナリオJSON2種類（baseline / infeasible。2026-07-18よりtight廃止）を作成し、`problem_class` と `domain` を一致させる

### フロントエンド
- [ ] `types.ts` の `StudioMode` に新モードIDを追加
- [ ] `StudioModeTabs.tsx` にタブ定義と `problemClass` 分岐を追加
- [ ] `Frontend/src/app/studio/views/{Pascal}View.tsx` を作成
- [ ] `StudioShell.tsx` に import とレンダリング分岐を追加
- [ ] `OverviewView.tsx` に新ドメイン分岐（`isXxx` フラグ + `IssueMonitorPanel`）を追加
- [ ] `HomeScreenNew.tsx` の `TAG_MAP` に追加

### 動作確認
- [ ] baseline シナリオで `status: "ok"` の解が出る
- [ ] Issues タブにイシューが表示される
- [ ] infeasible シナリオが本当にInfeasible判定される（空解での誤判定がないか）
- [ ] Overview タブが正しい分岐で表示される
- [ ] 専用ビュータブが正しく表示される
- [ ] `/health` で新ソルバーが一覧に出る（registryの自動discoveryの確認）

---

## 11. AI（Claude）への作業依頼時の注意

本書を読むAI自身、および本書を使ってAIに指示する人向けの運用ルール。

- 新規ドメイン追加をClaudeに依頼する際は、本書1-2節の「3方式」のどれを採用するかを最初に明示する。指定がなければC方式（4DSL準拠）をデフォルトとする。
- ソルバーコードを生成する際は、3章の禁止パターン（`no_overlap`の直接渡し、`if_then`への非boolean式渡し）を必ず守らせる。生成後はコードレビューでこの2点を重点確認する。
- `app.py` への配線を生成する際は、`_DSL4_SOLVERS` / `_LEGACY_SOLVERS` のマーカーコメントを保持したまま、その直前に1行追加する形にする。既存の辞書定義やマーカーを書き換えたり削除したりしない。
- ナップサック・選択・配置系の問題では、「何も選ばない」が自明な解にならないか必ず確認し、必要な場合は最低選択数制約を入れる。
- `repomix-domain-addition.xml` を前提にコード生成を依頼する場合、対象ファイルがローカルで直近編集されていないか確認する（4-4節のrepomix鮮度の問題）。古いrepomixに基づく生成は、既存ドメインの分岐を誤って消す・古い実装を前提にするなどのリスクがある。
- Koshoshiの作業スタイルとして、**解決策の提示より先に根本原因の特定を優先する**こと。特にCP Optimizerのエラーやフロントエンドのクラッシュは、表面的な対症療法ではなく、本書4章・8章の構造（どの段階の自動化が何を前提にしているか）に立ち返って原因を特定すること。

---

*OptiBuddy Development Guide — V8.5*
