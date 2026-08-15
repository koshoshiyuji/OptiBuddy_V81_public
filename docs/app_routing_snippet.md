# Backend/app.py — 新ドメイン追加に関わる箇所のみ抜粋

> このファイルは `repomix.domain-addition.json` 用の最小スニペットです。
> app.py 全体（820行）のうち、新ドメイン追加で触れる必要がある箇所のみを掲載しています。

---

## 1. imports（新ドメイン追加で追加不要。参照のみ）

```python
from dsl_transformer import convert_business_to_solver, convert_solver_to_ui
from llm.llm_client import upload_repomix_if_changed, call_llm_with_file
from solvers.registry import SolverRegistry

registry = SolverRegistry()   # ソルバーは registry.py の _register_builtin_solvers() で自動登録
```

---

## 2. `_solve_xxx` 専用ハンドラの雛形

`convert_business_to_solver` を **バイパスして直接ソルバーを呼ぶドメイン**（GhostKitchen方式）はこのパターン。
RCPSP拡張ベースのドメインはこのハンドラが不要で、`registry.solve()` 経路をそのまま使う。

```python
def _solve_ghost_kitchen(dsl: dict, issue_statuses: dict):
    """
    GhostKitchen DSL を直接 GhostKitchenSolver に渡してレスポンスを返す。
    /baseline から problem_class == "GhostKitchen" のとき呼ばれる。
    """
    try:
        from solvers.ghost_kitchen_solver import GhostKitchenSolver

        solver_input = {**dsl, "issue_statuses": issue_statuses}
        result = GhostKitchenSolver(solver_input).solve()

        issues    = result.get("issues", [])
        solutions = result.get("solutions", [])

        return jsonify({
            "status":    result.get("status", "ok"),
            "tasks":     [],          # GhostKitchen はタスクガントなし
            "makespan":  0,
            "issues":    issues,
            "containers": [],
            "solutions": solutions,
            "ui_dsl":    {},
            "resolved":  [],
            "skipped":   [],
            "skip_reasons": {},
        })

    except Exception as e:
        logger.error(f"[GhostKitchen] エラー: {e}", exc_info=True)
        return jsonify({
            "status": "validation_error",
            "issues": [{
                "id": "config-error", "severity": "CRITICAL",
                "title": "GhostKitchen 設定エラー", "message": str(e),
                "relatedContainerIds": [],
            }]
        }), 200
```

---

## 3. `/baseline` エンドポイントのドメイン分岐（★ 新ドメイン追加時に触る唯一の箇所）

```python
@app.route("/baseline", methods=["POST"])
def baseline():
    try:
        payload       = request.json
        dsl           = payload.get("dsl", {})
        issue_actions = payload.get("issueActions", {})
        issue_statuses = normalize_issue_actions(issue_actions)

        # ──────────────────────────────────────────────────
        # ★ ドメイン別早期分岐
        # convert_business_to_solver を通さず直接ソルバーへ渡すドメインはここに追加
        # ──────────────────────────────────────────────────
        problem_class = (
            dsl.get("problem_class")
            or dsl.get("metadata", {}).get("problem_class", "")
        )

        if problem_class == "GhostKitchen":
            return _solve_ghost_kitchen(dsl, issue_statuses)

        # ← 新ドメインを追加する場合はここに1行追加:
        # if problem_class == "NewDomain":
        #     return _solve_new_domain(dsl, issue_statuses)

        # RCPSP拡張ベースのドメイン（YardPlanning / EventStaffing）はここを通る
        # convert_business_to_solver → registry.solve() の通常経路
        ...

    except Exception as e:
        logger.error(f"/baseline error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
```

---

## 3b. 4DSL規約ベース自動ルーティング方式（`_solve_4dsl_generic`、2026-07-09追加）

TruckDispatcher・HospitalShiftPlannerが使っている、現在の新規4DSLドメインの標準パターン。
以下の命名規約に従うファイルを配置するだけで、app.pyに一切手を入れずに`/baseline`が自動的に
ルーティングする。

```
dsl_transformer/{snake}_converter.py     - convert_{snake}_to_solver(business_dsl)
solvers/{snake}_solver.py                - {PascalCase}Solver クラス
dsl_transformer/{snake}_ui_converter.py  - convert_{snake}_to_ui(solver_output, business_dsl=None)
```

`_solve_4dsl_generic(problem_class, dsl, issue_statuses)` がこの3ファイルを規約名でimportし、
変換→ソルブ→UI変換まで一気通貫で実行する。`_DSL4_SOLVERS`辞書への明示登録は不要（登録しても
`_solve_4dsl_generic`より優先されるだけで、動作自体は変わらない）。

**この方式が使えない場合のみ**、GhostKitchen方式（1〜2節）で`_solve_xxx()`を手書きする。
ProductionLotSchedulerが`_DSL4_SOLVERS`に明示登録されているのは命名規約から外れた歴史的経緯に
よるもので、新規ドメインが真似すべきパターンではない。

---

## 4. 新ドメイン追加チェックリスト（app.py で触る箇所）

| 方式 | app.pyで追加する内容 | 追加しない内容 |
|---|---|---|
| **4DSL規約ベース自動ルーティング方式**（TruckDispatcher等、新規4DSLドメインの標準） | **なし**（3b節参照） | imports・分岐ともに不要。app.pyに一切触れない |
| **GhostKitchen方式**（CFLPなど、dsl_transformer不使用） | `_solve_xxx()` 関数 + `if problem_class == "Xxx": return _solve_xxx(...)` の1行 | imports（不要） |
| **RCPSP拡張方式**（YardPlanning/EventStaffing方式。既存ドメイン専用） | **なし**（`convert_business_to_solver`/`convert_solver_to_ui`内の分岐に登録） | imports・`_DSL4_SOLVERS`とも不要 |

> 新規4DSLドメインは原則**4DSL規約ベース自動ルーティング方式**を使う。RCPSP拡張方式は
> YardPlanning/EventStaffingの既存実装で使われている旧方式であり、新規ドメインは真似しない。

---

## 5. `solvers/registry.py` の自動スキャン登録（V7.0以降、2026-07-09時点で確認）

**V7.0で`_register_builtin_solvers()`は廃止済み。** `solvers/`ディレクトリを起動時にファイル
スキャンし、`{snake}_solver.py`が存在し`{PascalCase}Solver`クラスがあれば自動登録される。
**`registry.py`を手動で編集する必要はない**。この自動登録は`/health`での一覧表示等のためのもので、
`/baseline`の実行ルーティング自体は1〜3b節の仕組み（`_DSL4_SOLVERS`/`_LEGACY_SOLVERS`/
`_solve_4dsl_generic`/分岐）が担う。
