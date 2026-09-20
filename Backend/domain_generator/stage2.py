
import ast
import copy
import difflib
import json
import logging
import py_compile
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (
    DEFAULT_ATTACHMENTS,
    _ATTACHMENT_SCOPE_NOTES,
    _to_snake,
    logger,
)
from .registry_patch import (
    _to_pascal,
)
from .stage1a import (
    check_domain_exists,
)
from .utils import (
    compute_diff,
)




# ─────────────────────────────────────────────────────────────
# Stage 2: コード生成プロンプト（V4.4: 禁止パターンセクション追加）
# ─────────────────────────────────────────────────────────────

# CP Optimizer 禁止パターン（プロンプトに埋め込む）
_CPO_FORBIDDEN_PATTERNS = """\
## CP Optimizer 実装ルール（必ず守ること）

### ❌ 禁止パターン1: no_overlap に配列と transition_matrix を直接渡す
```python
# NG
mdl.add(mdl.no_overlap(interval_var_list, transition_matrix))
```
```python
# OK: sequence_var を経由する
from docplex.cp.modeler import build_cpo_transition_matrix  # 正しいimport

tm = build_cpo_transition_matrix([[0, 10, 20], [10, 0, 15], [20, 15, 0]])
seq = mdl.sequence_var(interval_var_list, types=list(range(n)), name="seq_xxx")
mdl.add(mdl.no_overlap(seq, tm))
```

### ⚠️ 重要: transition_matrix の正しいimport
```python
# ❌ NG: この関数は存在しない
from docplex.cp.modeler import transition_matrix

# ✅ OK: 正しい関数名
from docplex.cp.modeler import build_cpo_transition_matrix
```

### ❌ 禁止パターン2: presence_of() == 1 を論理式として使う
```python
# NG
mdl.presence_of(itv) == 1   # これは boolean expression でない
```
```python
# OK: presence_of() をそのまま使う
mdl.presence_of(itv)   # これが正しい boolean expression
```

### ❌ 禁止パターン3: if_then の第2引数に制約式を渡す
```python
# NG
mdl.if_then(condition, mdl.end_before_start(a, b))  # e2 が boolean でないエラー
mdl.if_then(condition, mdl.start_before_end(a, b))  # 同様
```
```python
# OK: optional interval には直接制約を適用する
# absent のとき自動的に無効になる
mdl.add(mdl.end_before_start(a, b))
```

### ❌ 禁止パターン4: if_then の第2引数に比較式を渡す
```python
# NG
mdl.if_then(condition, mdl.end_of(a) <= mdl.start_of(b))
```
```python
# OK: 補助変数または end_before_start を使う
mdl.add(mdl.end_before_start(a, b))
```

### ❌ 禁止パターン5: presence_of()/start_of()/end_of() をその場で組み立てて get_value() に渡す
```python
# NG: 解抽出時に optional interval_var の presence/start/end を毎回式で組み立てる
# この書き方はdocplexのバージョンによって解の内部マッピングと一致せず
# 毎回 KeyError になり、exceptで握り潰されると「解けているのに
# 全アイテム未割当」になる実際に発生した不具合パターン！
try:
    if msol.get_value(mdl.presence_of(itv)):
        start_val = msol.get_value(mdl.start_of(itv))
        end_val   = msol.get_value(mdl.end_of(itv))
except KeyError:
    continue
```
```python
# OK: get_var_solution() で interval var の解オブジェクトを直接取得する
var_sol = msol.get_var_solution(itv)
if var_sol is None or not var_sol.is_present():
    continue
start_val = var_sol.get_start()
end_val   = var_sol.get_end()
```

### ❌ 禁止パターン6: mdl.minimize()/mdl.maximize() を mdl.add() で包まない
```python
# NG: minimize() の戻り値をモデルに追加していないため、目的関数が一度も
# ソルバーに登録されない
makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])
mdl.minimize(makespan_expr)
```
```python
# OK: 必ず mdl.add() で包む（動作実績のあるTruckDispatcher/NurseShiftWeeklyCapは
# 両方ともこの形）
makespan_expr = mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])
mdl.add(mdl.minimize(makespan_expr))
```

### ❌ 禁止パターン6b: 名前を付けていない式を msol.get_value(expr) でクエリする
```python
# NG: mdl.add(mdl.minimize(makespan_expr)) で目的関数として正しく登録していても、
# makespan_expr 自体は名前付き変数でもKPIでもないため、msol.get_value(makespan_expr) は
# 「Variable or KPI '...' not in the solution」で失敗する。禁止パターン6を修正しただけでは
# 解消しない別問題（2026-07-15 LineChangeoverScheduler登録で、禁止パターン6の修正を
# 適用した後も実際に再現した）。
makespan_val = msol.get_value(makespan_expr)
```
```python
# OK（2026-07-18〜必須）: 添付した solvers/base/solution_extraction.py の
# extract_makespan() / safe_objective_value() を使う。動作実績のある
# TruckDispatcher/NurseShiftWeeklyCapが手書きしていたPython手計算パターンを
# 共通コアに切り出したもの。inline で max(...) や get_objective_value() を
# 自分で書かず、必ずこの2関数を呼ぶこと（層A、独自実装は不可）。
from solvers.base.solution_extraction import extract_makespan, safe_objective_value

makespan_val = extract_makespan(schedule)                       # end_key既定"end"
obj_val      = safe_objective_value(msol, fallback=float(makespan_val))
```

### ❌ 禁止パターン7: no_overlap / cumulative（資源の同時使用制約）を自分で実装する

ヒアリングシート§9「資源の同時使用に関するルール」（a=同時に1つのみ / b=上限付きで
複数）は、添付した solvers/base/constraint_applier.py の `BaseConstraintApplier` を
経由すること。自前で `mdl.no_overlap(...)` や `mdl.pulse(...)` を直接書かない。
```python
# OK: BaseConstraintApplierのサブクラスを作り、constraints DSL配列を渡すだけにする
class {Domain}ConstraintApplier(BaseConstraintApplier):
    def _register_domain_handlers(self):
        pass  # ドメイン固有ハンドラがなければ何もしない

applier = {Domain}ConstraintApplier(mdl, task_itvs)
applier.apply_all(constraints)  # constraints[].type が "no_overlap" または "cumulative"
```
§9 a.（同時に1つのみ）は `{"type": "no_overlap", "params": {"task_ids": [...]}}`、
§9 b.（上限付き複数）は `{"type": "cumulative", "params": {"task_ids": [...], "capacity": N, "requirements": {...}}}`
というconstraints DSL要素に変換すること（converter側の役目）。

### ❌ 禁止パターン8: エンティティの割当先を `== 1` で強制する
```python
# NG: 「各顧客/タスクは必ずどこか1箇所に割り当てる」を等式で強制すると、
# 時間枠・容量等の理由で1件でも物理的に入らないエンティティがプールに
# 含まれているだけで、モデル全体が即座にinfeasibleになる
# （TruckDispatcher実機、2026-07-08〜同種のバグ。LNS Recreateが毎回
# 失敗する原因になった）。
mdl.add(sum(assign_vars[c][v] for v in vehicles) == 1)
```
```python
# OK: <= 1 に緩め、未割当を目的関数のペナルティとして表現する
# （ProductionLotSchedulerが採用している「未割当ペナルティ」方式と同じ考え方）。
mdl.add(sum(assign_vars[c][v] for v in vehicles) <= 1)
# 目的関数側: unassigned_penalty * (1 - sum(assign_vars[c][v] for v in vehicles))
```

### ❌ 禁止パターン9: feasible=False 判定で即returnし、他の検証を丸ごとスキップする
```python
# NG: 「解けなかった（infeasible/タイムリミット到達）」を一括りに早期returnすると、
# 既に組めている部分解に対する他の検証（時間枠逼迫・上限超過等）が一切実行されない
# まま握りつぶされる（TruckDispatcher実機、2026-07-08、Koshoshiとの相談で発覚）。
def check_issues(solution, feasible, ...):
    if not feasible:
        return [{"category": "infeasible", ...}]
    # tw_tight/duty_overtime等のチェックはここに書かれていても、
    # feasible=Falseのときは一度も実行されない
    ...
```
```python
# OK: feasibleの真偽に関わらず、既に組まれている部分解に対する追加検証を必ず実行する。
# 原因の内訳（例: insufficient_fleet_capacity等の分類）はfeasible判定と独立に出す。
def check_issues(solution, feasible, ...):
    issues = []
    if not feasible:
        issues.extend(_classify_infeasible_reason(solution, ...))
    issues.extend(_check_time_window_and_overtime(solution, ...))  # feasibleに関わらず実行
    return issues
```

### ❌ 禁止パターン10: リソースのハード制約を容量/距離のみで判定し、時間軸の累積を見ない
```python
# NG: 車両・スタッフ等のリソースへの割当を、容量や距離だけで判定すると、
# 遠方の顧客/タスクを後から詰め込んだ結果、拘束時間（max_duty_min等）の
# ハード制約を実行時に超過してしまう（TruckDispatcher実機、2026-07-08、
# Koshoshiとの相談⑤で発覚。容量チェックだけでは検出できなかった）。
if remaining_capacity(vehicle) >= demand(customer):
    assign(vehicle, customer)
```
```python
# OK: 各リソースの現在の状態（時刻・直前地点等）を逐次シミュレーションし、
# 割当後の完了予測がハード制約（max_duty_min等）を超えないことを確認してから
# 割り当てる。
projected_finish = simulate_finish_time(vehicle, customer)
if remaining_capacity(vehicle) >= demand(customer) and projected_finish <= vehicle["max_duty_min"]:
    assign(vehicle, customer)
```

### ❌ 禁止パターン11: logical_or()/logical_and() に3個以上の条件を個別の位置引数で渡す
```python
# NG: docplex.cp の logical_or()/logical_and() は位置引数を最大2個
# (e1, e2=None) までしか受け付けない。3個以上を個別の位置引数で渡すと
# TypeError: logical_or() takes from 1 to 2 positional arguments but N were given
# で実行時にクラッシュする（2026-08-03 VesselDeckLoader実機登録で発覚。
# Gate2の動的検証はこれを「実行不可能」と誤解されやすい文言で報告して
# しまい、真因の特定にKoshoshiとの往復が発生した）。
mdl.add(mdl.logical_or(
    x_i + l_i <= x_j,
    x_j + l_j <= x_i,
    y_i + w_i <= y_j,
    y_j + w_j <= y_i,
))
```
```python
# OK: 3個以上まとめる場合は必ずリストで渡す（logical_and()も同様）
mdl.add(mdl.logical_or([
    x_i + l_i <= x_j,
    x_j + l_j <= x_i,
    y_i + w_i <= y_j,
    y_j + w_j <= y_i,
]))
```

### ❌ 禁止パターン12: 時間帯によって単価・コストが変わる場合に、目的関数側だけ平均値で近似する
```python
# NG: ヒアリングシートが「電力の単価は時間帯によって変わる」ことを明示的に
# 要求しているのに、目的関数（最適化の意思決定に使われる側）では
# 時間帯別単価テーブルを平均した定数を使ってしまっている。
# KPI表示用の別関数（_compute_energy_cost_for_orderのような正確な
# 時間帯積分計算）が同じファイル内に既に存在していても、それが目的関数
# 側の意思決定には一切使われず事後表示にしか使われていない場合、
# ソルバーは実質的に時間帯を見ずにスケジューリングしてしまう
# （2026-09-17 EnergyCostAwareScheduler実機投入前レビューで発覚。
# 物理的な実行可能性は常に満たされるため、Gate2の動的検証や独立解
# チェッカーでは検出されない）。
def _average_tariff(tariff_table):
    return sum(tariff_table) / len(tariff_table)

avg_cost_per_kwh = _average_tariff(tariff_table)
for lid, itv in order_itvs[oid].items():
    cost_terms.append(mdl.presence_of(itv) * dur * req_power * avg_cost_per_kwh)
```
```python
# OK: 開始時刻の候補ごとに正確なコストを事前計算し、element()で
# 開始時刻（決定変数）からその値を引く。目的関数自体が時間帯を
# 正しく考慮した上で最適化されるようになる。
max_start = end_latest - dur
cost_by_offset = [
    int(round(_compute_energy_cost_for_order(t, t + dur, req_power, tariff_table, slot_min) * 100))
    for t in range(earliest, max_start + 1)
]
for lid, itv in order_itvs[oid].items():
    offset_expr = mdl.start_of(itv, absentValue=earliest) - earliest
    energy_cost_expr = mdl.element(cost_by_offset, offset_expr)
    cost_terms.append(mdl.presence_of(itv) * energy_cost_expr)
```

## 数量要件（hard/soft）の実装ルール（必ず守ること）

ヒアリングシート§4-1「人数・数量に関するルールの扱い」（a=解なし扱い=hard /
b=欠員許容=soft、未記入時はb=soft）は、添付した solvers/base/quantity_requirement.py
の `apply_quantity_requirement()` を必ず経由すること。過去に、この分岐自体が
存在せず常に無条件のハード等式制約（`== required_count`）を追加していた不具合が
発見されている（`docs/DESIGN_2026-07-18_layer_ab_pattern_library.md` 2-2-2節）。
```python
from solvers.base.quantity_requirement import apply_quantity_requirement

shortfall = apply_quantity_requirement(
    mdl, present_vars, required_count,
    mode="soft",             # ヒアリング§4-1の回答（"hard" or "soft"）
    comparison="at_least",   # hardの場合の既定。過剰配置まで禁止したい場合のみ"exact"
)
if shortfall is not None:
    understaffing_terms.append(PENALTY_WEIGHT * shortfall)  # soft時のみ目的関数に加算
```

## 解が見つからない場合の結果フォーマット規約（必ず守ること）

ソルブが失敗した場合（`msol is None` 等）も、`solve()` の戻り値の**トップレベル**に
明示的に `"feasible": False` を含めること。`solutions` 配列を空にするだけでは、
Gate2動的検証が `result.get("feasible")` をトップレベルから読み取れず
`None`（期待値`False`との不一致）として検出してしまう
（2026-07-15 LineChangeoverScheduler登録で実際に発生した不具合）。
`_make_result()` のようなヘルパーには `feasible: bool` を明示的な引数として持たせ、
呼び出し側の全経路（バリデーションエラー時・ソルブ失敗時・成功時）で明示的に渡すこと。

この `msol is None` 分岐（＝業務上の制約矛盾によるinfeasible判定）で組み立てるissueの
`id` も、下記「想定外例外と業務上のinfeasibleを区別すること」節と同じ理由で、必ず
`"solve_failed"` にすること（`"infeasible"` 等の独自名を付けないこと）。Frontend
(`InfeasibleView.tsx`) は `issues[].id === "solve_failed"` であることだけを見て
Infeasible画面を表示するかどうかを決めており、`feasible` フラグそのものは見ていない。
「想定外例外」と「業務上のinfeasible」はコード上別々の分岐（try/exceptの外と中）に
分かれて実装されることが多いため、片方の分岐にだけこの規約を適用し、もう片方に
適用し忘れる事故が実際に発生している（2026-07-31 NursingWorkloadBalance登録:
`msol is None` 分岐のissue idを独自に`"infeasible"`としてしまい、feasible=Falseは
正しく返っていたにもかかわらずFrontendのInfeasible画面が表示されなかった）。
**この規約は「例外処理」限定ではなく、feasible=Falseを返す全ての分岐に適用される。**

## 想定外例外と業務上のinfeasibleを区別すること（必ず守ること）

`mdl.solve()` 等を囲む `except Exception` ハンドラで、CE-limit（ライセンス上限）
以外の例外を「制約を満たす解が存在しない(infeasible)」と同じ形の結果に丸めて
返さないこと。過去に、`no_overlap()` のAPI誤用によるAssertionError（実装バグ）が
`except Exception: return self._infeasible_result(...)` でそのままinfeasible扱いに
され、「意識して入れた要件がなぜ実装に反映されないのか」の原因調査に長時間を
要した実例がある（NurseShiftEval実機、2026-07-28）。同種の握りつぶしは
car_sequencing_solver.py / store_site_solver.py / meeting_room_solver.py /
nurse_shift_weekly_cap_solver.py / truck_dispatcher_solver.py でも見つかり、
2026-07-28時点で修正済み。

対策: `solvers/base/solver_error_result.py` の `build_solver_crash_issue(exc)` /
`solver_crash_extra_fields(exc)` を使うこと。
```python
from solvers.base.solver_error_result import build_solver_crash_issue, solver_crash_extra_fields

try:
    msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
except Exception as e:
    if is_ce_limit_exceeded(e):
        raise CeLimitExceededError(str(e)) from e   # 既存のCE-limitフォールバック経路へ
    logger.error(f"[YourDomain] mdl.solve() 例外: {e}", exc_info=True)
    result = {
        "status": "ok", "feasible": False,
        "metadata": {"problem_class": "YourDomain"},
        "solutions": [], "issues": [build_solver_crash_issue(e)],
        "_solver_version": "your_domain_v1.0",
    }
    result.update(solver_crash_extra_fields(e))
    return result
```
注意: issueの `id` は必ず `"solve_failed"` のままにすること。Frontend
(`useStudioState.ts` の `hasSolveFailed` 判定、`InfeasibleView.tsx`) は
`issues[].id === "solve_failed"` であることのみを見て「Infeasible画面
（制約見直しタブ・AI緩和提案）」を表示するかどうかを決めており、`feasible`
フラグは見ていない。id を独自の値（`"solver_exception"` 等）にすると
Infeasible画面自体が表示されなくなり、CRITICALな例外が画面上どこにも
明示されない退行になる（car_sequencing_solver.py の2026-07-25付コメント
参照）。`build_solver_crash_issue()` はこの点を踏まえて実装済みなので、
自前でissue dictを組み立てず必ずこのヘルパーを使うこと。

## 能力・互換性フィールドの死データ化防止（必ず守ること）

Business DSLの「割当先」オブジェクト（ライン/プリンター/マシン等）に `supported_types` /
`compatible_products` のような能力・対応種別を表すフィールドを定義した場合、**そのフィールドは
必ずconverterの互換性解決ロジック（割当先候補リストの絞り込み）で実際に参照すること**。
これまで以下2件の実際の不具合が発生している：

1. ProductionLotScheduler: ラインの `compatible_products` を定義したのに、ロットの
   `compatible_lines` しか見ていなかったため、本来割り当てられないはずの組み合わせが
   割り当て可能になっていた。
2. TestDomain: プリンターの `supported_types` を定義したのに、ジョブの `job_type` と
   突き合わせるロジックが一切なく、`compatible_printers` のみで判定されていたため、
   本来不可能な組み合わせ（カラージョブ→白黒専用機）が可能になっていた。

対策: 割当先オブジェクトに能力/対応種別フィールドを定義したら、割当される側オブジェクトの
対応する種別フィールドとの突合をconverter内で必ず計算し、明示的ホワイトリスト（あれば）との
交差を最終的な互換リストとすること。定義したフィールドはどこにも参照されない「死データ」にしないこと。

## 解抽出異常検知（必ず守ること）

`solvers/base/issue_rules.py` に `build_full_unassignment_issue(assigned_count, total_count, entity_label, extra_hint)`
という共通ヘルパーがある。解抽出後の割当済み件数が0件（=入力アイテムが1件以上あるにもかかわらず全件未割当）の場合に
警告 issue を返す。新規ドメインの `_detect_issues()` でも必ずこれを呼び出して issues に含めること（パターンは
`solvers/production_lot_scheduler_solver.py` の `_detect_issues()` を参照）。
これはCP Optimizerの解抽出バグ（禁止パターン5参照）を実際に発生させてしまった場合でも、
ドメイン知識ゼロで異常を検知できる最後の安全網である。

## 目的関数実装ルール（必ず守ること）

ヒアリング資料には「目的関数」セクション（例: "最小化: Σ(...) + Σ(...) + Σ(未割り当て...)"）が
記載されている場合がある。このセクションに列挙された **各項（Σ(...)で表現される要素）は、
1つも欠かすことなくコードの目的関数（penalty_terms / objective 式）に反映すること。**

過去に発生した実際の不具合パターン:
  - ヒアリングには「Σ(未割り当てロット × 大きなペナルティ)」と明記されていたにもかかわらず、
    生成コードでは「緊急(urgent)フラグを持つアイテムのみ」に未割り当てペナルティを限定してしまい、
    非緊急アイテムは「割り当てなくてもコスト0」になった。
  - その結果、ソルバーは「全アイテムを未割り当てにする」解を最適解として選んでしまい、
    solve() 自体は成功 (status=ok, 1 solution found) しているのに、
    実質的に何も割り当てられない実用不可能な結果を返す不具合が発生した。

このパターンを避けるため、以下を必ず守ること:
  1. ヒアリングの目的関数に登場する Σ 項（遅延・コスト・未割り当て等）は、
     "urgent" や "priority" などの一部フラグを持つアイテムに限定せず、
     **原則として全アイテムに適用**すること。
     優先度・緊急度によって重み（ペナルティの大きさ）を変えるのは良いが、
     「対象から完全に除外する」のは要件逸脱になりやすいので避けること。
  2. optional interval_var など「割り当てなし」を許容する変数を使う場合は、
     未割り当てのままだと目的関数上のペナルティが 0 になる設計は避け、
     必ず「割り当てないこと自体に対するペナルティ項」を全アイテムに対して追加すること。
  3. 生成したコードの目的関数部分には、ヒアリングの目的関数セクションとの対応を
     コメントで明記すること（例: `# ヒアリング目的関数 項3: 未割り当てペナルティ（全アイテム対象）`）。

## CE上限（CPLEX Community Edition評価版のモデルサイズ上限）フォールバックの実装ルール（必ず守ること）

CPLEXの無料版（Community Edition）にはモデルサイズ上限があり、業務データの規模が
大きいシナリオで超過すると例外が発生する（docplex.cp: "Problem size limit exceeded"、
docplex.mp: "CPLEX Error 1016"）。この上限は変数・制約の宣言時点ではなく**エンジンを
実際に呼び出す時点（`.solve()`）でのみ**発生する。ヒアリングシートには現れない
実装技術上の要件だが、業務データが小規模な間は気づかれず、後から規模が増えて
初めて発覚することが多いため、新規ドメインは登録時点から必ずこの対応を組み込むこと。
`solvers/base/ce_limit_lns.py`（CP用）/ `solvers/base/ce_limit_mip_fallback.py`（MIP用）を
必ず経由し、独自の分割・フォールバックロジックを書かないこと（下記の import文・呼び出し
コード例の通りに呼ぶだけでよく、内部実装を読む必要はない）。

### MIPドメイン（docplex.mp採用時）: 必ず守ること

`mdl.solve(...)` の呼び出し箇所を、素の呼び出しではなく
`solve_with_ce_fallback(mdl, ...)`（`solvers/base/ce_limit_mip_fallback.py`）に置き換えるだけでよい。
ドメイン側の追加コード・追加ファイルは不要。
```python
from solvers.base.ce_limit_mip_fallback import solve_with_ce_fallback
sol = solve_with_ce_fallback(mdl, log_output=False)  # 素のmdl.solve(...)から置き換えるだけ
```

### CPドメイン（docplex.cp採用時）: 必ず守ること

1. モデル構築関数（`_build_and_solve`等）の `mdl.solve(...)` 呼び出しをtry/exceptで包み、
   `is_ce_limit_exceeded(e)` がTrueの場合のみ `CeLimitExceededError` を投げ直す
   （それ以外の例外は通常通り扱う）。
```python
from solvers.base.ce_limit_lns import is_ce_limit_exceeded, CeLimitExceededError

try:
    msol = mdl.solve(TimeLimit=time_limit, LogVerbosity="Quiet")
except Exception as e:
    if is_ce_limit_exceeded(e):
        raise CeLimitExceededError(str(e)) from e
    raise
```
2. 公開の `solve()` メソッド側で `CeLimitExceededError` を捕捉し、
   `run_solve_with_ce_limit_fallback()` を呼ぶだけにする（再試行回数・バッチサイズの
   段階的縮小・打ち切りロジックは全て層Aが持つため、ここに独自ロジックを書かないこと）。
```python
from solvers.base.ce_limit_lns import run_solve_with_ce_limit_fallback

try:
    solution_data, candidates = self._build_and_solve(entities, tasks, config)
except CeLimitExceededError:
    try:
        from solvers.{snake}_batch_decomposer import (
            PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
        )
    except ImportError:
        # 3.の分割軸が特定できずbatch_decomposerを生成しなかった場合の安全な
        # フォールバック。クラッシュさせず、解なし＋警告として返す。
        return {
            "status": "ok", "feasible": False,
            "metadata": {"problem_class": "{ProblemClass}"},
            "solutions": [], "issues": [{
                "id": "ce_limit_no_adapter", "severity": "CRITICAL",
                "title": "CPLEXの無料版で扱える件数を超えています",
                "message": "データ件数を減らすか、正規ライセンスのご利用をご検討ください。",
                "relatedContainerIds": [],
            }],
            "_solver_version": "{snake}_v1.0",
        }
    return run_solve_with_ce_limit_fallback(
        solver_class=type(self), solver_input=self.dsl,
        primary_entity_key=PRIMARY_ENTITY_KEY,
        build_subset_input=build_subset_input, merge_results=merge_results,
        problem_class="{ProblemClass}", solver_version="{snake}_v1.0",
    )
```
3. `Backend/solvers/{snake}_batch_decomposer.py` を新設し、`PRIMARY_ENTITY_KEY`・
   `QUOTA_FIELDS`・`build_subset_input`・`merge_results` を宣言する。
   **`PRIMARY_ENTITY_KEY`（どのリストを分割軸にするか）の決め方（必ずこの手順で判断すること）**:
   a. ドメインの制約を1つずつ、「ある1種類のエンティティ（例: スタッフ・車両・機械）の
      1インスタンス内だけで完結する制約か（例: 1人のスタッフのno_overlap・1台の車両の
      積載上限）」「複数インスタンスにまたがる集計制約か（例: 1タスクへの必要人数の合計、
      1日の総処理件数）」に仕分ける。
   b. 「1インスタンス内で完結する制約」の対象エンティティで**全ての**そのような制約が
      説明できるなら、そのエンティティのリストフィールド名を`PRIMARY_ENTITY_KEY`にする
      （分割してもそれらの制約は必ず同じバッチ内に収まるため壊れない）。
   c. 残った「複数インスタンスにまたがる集計制約」（通常は必要数・上限数のような
      カウント系）は、`QUOTA_FIELDS`宣言でバッチ間の残数持ち越しとして表現する。
   d. **bで単一のエンティティに全て説明がつかない場合**（例: 2種類のエンティティの
      両方にまたがる制約が複数ある）は、分割軸の選定を安全に自動化できないケースである。
      この場合は`{snake}_batch_decomposer.py`を生成せず、2.のtry/except ImportErrorに
      よる「ce_limit_no_adapter」警告フォールバックに委ねること。無理に分割軸を推測して
      誤った実装をするより、警告を出して規模を理由に断る方が安全（誤った分割は
      クラッシュではなく「一部の制約が静かに破られた解」という気づきにくい不具合になる
      ため）。
   `build_subset_input`/`merge_results`の実装は、`solvers/nurse_shift_weekly_cap_batch_decomposer.py`
   をテンプレートとすること（`reduce_quota_fields()`への宣言＋そのドメイン自身の
   `build_candidates`/`build_task_summary`/`build_metrics`/issue検出関数の呼び直し
   のみで構成し、新規の集計ロジックを書かないこと）。

## ⚠️ 添付の参考資料（既存ドメインのソースコード一式）の扱いについて（必ず守ること）

添付されている参考資料には、このリポジトリに既に登録済みの他ドメイン（別の業務）のソースコード
全体が含まれています。これは以下の目的にのみ使ってよい参考情報です。

- Pythonの実装規約・共通ユーティリティの呼び出し方・上記の禁止パターン集の具体例としての参照
- converter/solver/ui_converterの3層構造やファイル配置など、リポジトリ全体の慣例の把握
- **アルゴリズム・データ構造レベルの実装パターンの再利用**（積極的に行ってよい）:
  例えば「時間軸上のスケジューリングには interval_var + no_overlap（必要なら sequence_var
  で順序も表現）」「経路・訪問順序を伴う割当には sequence_var ベースのルーティング表現」
  「拠点間の連続量の配分には docplex.mp の線形計画」といった、CP Optimizer/MIPの一般的な
  定式化テクニックは、今回の業務構造に合っている限り、参考資料中のどのドメインの実装から
  学んでもよい。これはコードの中身（変数名・アルゴリズム構造）の話であり、以下で禁止する
  「業務構造の借用」（フィールド名・エンティティ名等、ドメイン固有の語彙や概念）とは別物。

**やってはいけないこと**: 今回実装する業務のフィールド構成・エンティティ構造・制約・目的関数を、
参考資料中の別ドメイン（特に表面的に似て見えるもの、例: 「部屋」「割り当て」「スケジューリング」
等の語を含むドメイン）から借用・模倣すること。今回実装すべき業務構造は、後述の「ドメイン定義」
セクションおよび（存在する場合）「技術指示」「業務構造チェックリスト」セクションに書かれている
内容が唯一の正解です。参考資料中の他ドメインと構造的に似ていたとしても、それは偶然の一致に
過ぎず、真似する理由にはなりません。逆に、ヒアリング内容に明記されている構造（例: 1件の依頼が
複数の訪問・工程を要求する、資源の共有に上限がある、目的が段階的優先順位を持つ、等）を、
参考資料に無いからという理由で単純化・省略することも禁止します。

要するに: 「どう実装するか（アルゴリズム・技術パターン）」は参考資料から自由に学んでよいが、
「何を実装するか（業務のエンティティ・構造・ルール）」は今回のドメイン定義・技術指示・
業務構造チェックリストだけを見ること。
"""

# ─────────────────────────────────────────────────────────────
# UI文言のi18n対応指示（Stage2プロンプトに埋め込む、is_dsl4ドメインのみ）
#
# 背景（2026-08-11追加）: ドメイン登録パイプライン（/api/domain/*系）は元々
# i18n対応の対象外だった（Backend/i18n/__init__.py参照）。実際にi18n対応した
# のはNurseShiftWeeklyCap/NursingWorkloadBalanceの2ドメインのみで、いずれも
# 登録完了後の手動追加実装（ヒアリングシート補足欄の自由記述がきっかけ）で
# あり、新規ドメイン全般に自動適用される仕組みはなかった（prob131登録に
# 際してKoshoshiから指摘）。Koshoshi合意（2026-08-11）により、Stage2フル
# 新規生成（is_dsl4のnew_domain登録）ではBackend/i18n/{snake}_messages.py を
# 毎回自動生成する方針に変更する。パターン3（拡張登録、Stage2-lite V2）は
# 別コードパスのため対象外（既存25ドメインの遡及対応も対象外）。
# ドメイン名・snake_case名を含まない全ドメイン共通の指示文のみをここに置き、
# static_part（prompt cachingのプレフィックス対象）に含める。
# ─────────────────────────────────────────────────────────────
_I18N_MESSAGE_DICT_INSTRUCTION = """\
## UI文言の日英i18n対応（必ず守ること）

新規ドメインのUI文言（issue/KPI/テーブル列見出し/プランラベル等、solver.py・
ui_converter.pyがフロントエンドに返すtitle/message/label/sub/unitの値）は、
日本語文字列を直接埋め込まず、必ず Backend/i18n/{snake}_messages.py
（{snake}は後述の「実装方針」セクションのdomain (snake_case)の値）経由の
t(key, **params) 呼び出しで解決すること。

### 実装パターン（既存の nursing_workload_balance_messages.py と同じキー命名規則）

```python
# Backend/i18n/{snake}_messages.py
from __future__ import annotations
from i18n import make_translator

_JA = {
    "issue.no_input.title": "入力データ不足",
    "issue.no_input.message": "...データが空です。",
    "issue.infeasible.title": "制約を満たす割り当てが見つかりませんでした",
    "issue.infeasible.message": "...",
    "issue.unassigned_x.title": "未割り当て...: {name}",  # {} はstr.format用プレースホルダ
    "kpi.xxx.label": "...",
    "kpi.xxx.unit": "件",
    "table.section.xxx.title": "...",
    "table.column.xxx": "...",
    "solver.planLabel": "...",
}
_EN = {
    # _JAと同じキーを全て英訳して用意すること（欠けているキーは自動的に日本語へ
    # フォールバックするが、フォールバック発生時はwarningログが出るため未対応キーを
    # 残さないこと）
    "issue.no_input.title": "Insufficient input data",
    # ...（以下同様、_JAの全キーに対応する英訳を用意）
}
t = make_translator(_JA, _EN, "{snake}")
```

```python
# solver.py / ui_converter.py 側
from i18n.{snake}_messages import t
...
"title": t("issue.no_input.title"),
"message": t("issue.unassigned_x.title", name=name),
```

- キーは生の日本語文字列ではなく、rule_id / KPIのid / テーブル列key / section_id等の
  安定識別子ベースにすること。
- str.format()の埋め込みパラメータ（{name}等）は _JA/_EN 両方の対応キーで
  同じプレースホルダ名を使うこと。
- _JA/_EN は本ドメインで実際に使うキーを両方とも網羅すること（日本語のみを
  直書きした場合、Gate2の静的チェックでadvisory警告として報告される）。
"""

# ソルバー戻り値フォーマット仕様（プロンプトに埋め込む）
_SOLVER_OUTPUT_SPEC = """\
## ソルバー戻り値フォーマット（必ず守ること）

solve() メソッドは以下のキーを含む dict を返すこと。
Frontend の汎用 Gantt コンポーネントは `tasks` 配列のみを参照する。

```python
return {
    "status":   "ok",
    "tasks":    [          # ← 必須。Gantt 表示用。空リストでも可
        {
            "id":         str,   # ユニークID
            "name":       str,   # 表示名
            "resource":   str,   # Y軸ラベル（ライン名・機械名・担当者名など）
            "resourceId": str,   # resource の ID
            "start":      int,   # 開始時刻（分）
            "end":        int,   # 終了時刻（分）
            "duration":   int,   # end - start
            "type":       str,   # "LOAD" | "DISCHARGE" | "MOVE" | "OTHER"
        },
        ...
    ],
    "makespan": int,       # 全タスク終了時刻の最大値（分）
    "issues":   list,      # イシューリスト
    "solutions": list,     # ドメイン固有の詳細データ（任意）
}
```
"""

# converter⇔solver キー整合性契約（4DSLドメインのプロンプトに埋め込む）
#
# 背景: HospitalShiftPlanner登録時の solver_time_limit キー名不一致を皮切りに、
# 4DSLドメイン登録のたびに「converterが出力しないキーをsolverが参照する」
# 不具合が、毎回異なるキー名の組み合わせで繰り返し発生している
# （StoreSite: 1回目converter未検出、2回目不一致10キー、3回目不一致4キー、
# 4回目不一致16キー）。Gate2静的field-check（check_converter_solver_field_consistency）
# は生成後に不一致を検出できるが、生成前にLLMへ「キー名を一致させる」よう
# 明示的に指示する仕組みがこれまで存在しなかった。この定数はその穴を埋める。
_CONVERTER_SOLVER_CONTRACT = """
## converter ⇔ solver のキー整合性（必ず守ること・過去に繰り返し発生した不具合）

{snake}_converter.py の convert_{snake}_to_solver() が返す dict のキーと、
{snake}_solver.py の {name}Solver が参照するキーは、完全に一致させること。
これまでの複数ドメイン登録で、converterが出力しないキーをsolverが
.get("キー名") や ["キー名"] で参照し、常時デフォルト値へフォールバックする、
または実行時例外になる不具合が繰り返し発生している。

手順:
1. solver_input dict に含める全キーの一覧を先に決め、両ファイルの冒頭に
   コメントで列挙する（例: # solver_input keys: staff, tasks, config, ...）。
2. converter.py はその全キーを漏れなく出力する（値が空でもキー自体は必ず存在させる）。
3. solver.py はその一覧に無いキーを一切参照しない。
4. 出力前に、converterが出力する全キーとsolverが参照する全キーを
   自己チェックで突き合わせ、一致することを確認してから出力する。
"""


# 2026-07-17: Anthropicのprompt cachingを有効化するためのマーカー。_build_stage2_prompt()
# が返す文字列に埋め込まれ、generate_domain_files()側でこのマーカーの前後を分割する。
# マーカーより前（静的パート）は attachment_section + _CPO_FORBIDDEN_PATTERNS のみで構成され、
# ドメイン名や snake_case 名の埋め込みを一切含まないため、どのドメインを生成する場合でも
# バイト単位で完全に同一の内容になる。これによりAnthropic側でcache_controlを使った
# プレフィックスキャッシュの対象にでき、Stage2呼び出しのコスト・レイテンシを削減できる
# （マーカーより後の動的パートはドメインごとに内容が変わるためキャッシュ対象外）。
# 非Anthropicプロバイダー等、分割せず1本の文字列として使いたい呼び出し元は、
# マーカーを空文字に置換すれば従来通りの結合済みプロンプトになる。
_STAGE2_CACHE_SPLIT = "\n\n<<<STAGE2_DYNAMIC_SECTION>>>\n\n"


def _build_stage2_prompt(domain_def: dict, attachment_texts: dict, is_dsl4: bool = False) -> str:
    name  = domain_def.get("domain_name", "NewDomain")
    snake = domain_def.get("snake_name") or _to_snake(name)

    attachment_section = ""
    for fname, content in attachment_texts.items():
        scope_note = _ATTACHMENT_SCOPE_NOTES.get(fname)
        scope_block = f"\n\n{scope_note}\n" if scope_note else ""
        attachment_section += f"\n\n---\n## 参考: {fname}\n{scope_block}\n{content}"

    if is_dsl4:
        impl_note = f"""- 実装方式: 4DSL準拠（Business DSL → Solver Input DSL → Solver Output DSL → UI DSL）
- converter: Backend/dsl_transformer/{snake}_converter.py を作成
- ui_converter: Backend/dsl_transformer/{snake}_ui_converter.py を作成
- solver: 問題構造に適した技術を選択すること。以下の3択から選び、貪欲法等の独自ヒューリスティックへの
  安易な逃避は避けること（ヒューリスティックはいずれの技術でも定式化できない場合の最終手段）。
    (a) IBM CPLEX CP Optimizer (docplex.cp.model) — interval_var/sequence_var/no_overlap等、
        時間軸に沿ったスケジューリング・割当構造に適する
    (b) IBM CPLEX（docplex.mp, CPLEX MIP/LP） — 施設配置・拠点選定・予算配分等、時間軸を
        持たない選択・割当構造の混合整数計画問題に適する。下記「参考」セクションおよび
        ドメイン定義中の technical_directives に docplex.mp / MIP の使用指示がある場合は
        必ずこちらを選ぶこと
    (c) 純Python — 上記いずれの定式化にも自然に収まらない場合のみ"""
    else:
        impl_note = """- 実装方式: 従来方式（dsl_transformer 非使用、レガシー）
- solver: IBM CPLEX CP Optimizer (docplex.cp.model)"""

    dsl4_files = f"""
===FILE:Backend/dsl_transformer/{snake}_converter.py===
（Business DSL → Solver Input DSL 変換。truck_dispatcher_converter.py を参考に実装）
===END===

===FILE:Backend/dsl_transformer/{snake}_ui_converter.py===
（Solver Output DSL → UI DSL 変換。truck_dispatcher_ui_converter.py を参考に実装）
===END===
""" if is_dsl4 else ""

    # 2026-08-11追加: 上記「UI文言の日英i18n対応」指示に対応するファイル。
    # is_dsl4のみ対象（パターン3/拡張登録・非dsl4レガシー方式は対象外）。
    i18n_messages_file = f"""
===FILE:Backend/i18n/{snake}_messages.py===
（i18nメッセージ辞書。上記「UI文言の日英i18n対応」の指示に従い、_JA/_EN辞書と
make_translator()でt(key, **params)を定義し、{snake}_solver.py・{snake}_ui_converter.py
の該当箇所から実際に呼び出すこと）
===END===
""" if is_dsl4 else ""

    converter_solver_contract = (
        _CONVERTER_SOLVER_CONTRACT.format(snake=snake, name=name) if is_dsl4 else ""
    )

    # 2026-07-20: 以前ここにあった「Backend/app.pyへ_solve_{{snake}}()を追加し
    # _DSL4_SOLVERSに登録する」PATCHブロックを削除した。is_dsl4ドメインは
    # 実際には_solve_4dsl_generic()の規約ベースルーターのみで動作しており、
    # かつ_patch_app_py_dsl4()側のマーカー不一致バグにより_DSL4_SOLVERSへの
    # 辞書登録は常に静かに失敗、関数定義だけがapp.pyに残る一方の状態になって
    # いた（MeetingRoom/StoreSiteで実際に発生・削除済み。docs/ENGINEERING_LOG.md
    # 2026-07-20参照）。今後の新規ドメイン登録で同じデッドコードを再生成しない
    # よう、このPATCHブロック自体を削除した。
    dsl4_patches = f"""
===PATCH:Backend/dsl_transformer/business_to_solver.py===
INSTRUCTION: convert_business_to_solver の 4DSLドメイン分岐ブロックに {name} のルートを追加
===CODE===
    if problem_class == "{name}":
        from .{snake}_converter import convert_{snake}_to_solver
        return convert_{snake}_to_solver(business_dsl)
===END===

===PATCH:Backend/dsl_transformer/solver_to_ui.py===
INSTRUCTION: convert_solver_to_ui の 4DSLドメイン分岐ブロックに {name} のルートを追加
===CODE===
    if problem_class == "{name}":
        from .{snake}_ui_converter import convert_{snake}_to_ui
        return convert_{snake}_to_ui(solver_output)
===END===
""" if is_dsl4 else ""
    # 2026-08-04: 以前ここにあった、is_dsl4=false（従来方式）ドメイン向けの
    # 「Backend/app.pyへ_solve_{{snake}}()を追加し_LEGACY_SOLVERSに登録する」
    # PATCHブロックを削除した。理由は2つ。
    # (1) 実際には app.py の _try_dynamic_solver()（solvers/{{snake}}_solver.py の
    #     {{ProblemClass}}Solver を命名規約ベースで自動検出・呼び出す既存の汎用
    #     フォールバック）が何もしなくても同じ結果を返すため、このPATCHは
    #     完全に不要な重複コードだった（is_dsl4=trueで2026-07-20に
    #     _solve_4dsl_generic()を理由に同種のPATCHを削除したのと同じ構図）。
    # (2) _patch_app_py_legacy()側のrfind()バグ（マーカー直後ではなくファイル内
    #     最後の"}"に新エントリを挿入してしまう）と組み合わさり、
    #     Backend/app.py自体にSyntaxErrorを書き込み、バックエンド再起動まで
    #     誰も気づけないという実害が発生した（SteelMillSlabDesign登録、
    #     2026-08-04。rfind()バグ自体とpy_compile安全網は別途修正済みだが、
    #     そもそも不要なPATCHを生成しなければこのバグの発生条件自体が無くなる）。
    # 詳細は OptiBuddy_V81_devnotes/ENGINEERING_LOG.md 2026-08-04参照。

    # 2026-07-17削除: 以前はここで StudioModeTabs.tsx / StudioShell.tsx に対して
    # 新ドメイン専用のタブ・ルーティングを追加するPATCHをLLMに生成させていたが、
    # 2026-07-11のリファクタでStudio側は既に「未知problem_classは全てGeneric4DSLView/
    # GENERIC_MODESに一本化する」設計に変わっており（TruckDispatcher/NurseShiftも
    # 専用タブ・専用Viewを持たない）、このPATCHは実際には不要だった。
    # それにもかかわらずapply_domain_files()の汎用PATCH適用フォールバック
    # （非Backend/非DSL4ファイル向けの `existing + "\\n\\n# ─── ... ───\\n" + append_code`）
    # がPythonの`#`コメント記法をそのまま.tsxファイルに追記しており、これがtsc型検証で
    # 確実に構文エラー（TS1127 Invalid character等）になり、新規ドメイン登録のapplyが
    # 毎回ロールバックされる原因になっていた（実機ログで確認・特定）。
    # 実際には何もパッチしなくてもGeneric4DSLViewが新ドメインを正しく表示するため、
    # このPATCH生成自体を削除する（{name}View.tsxの個別ビュー生成も同じ理由で削除）。

    # 静的パート: ドメイン名・snake_case名の埋め込みを一切含まない、全ドメイン共通の
    # 参考資料・禁止パターンのみで構成する（prompt cachingのプレフィックス対象）。
    static_part = f"""# OptiBuddy 新ドメイン実装 — 共通参考資料・実装ルール

以下は、どの業務ドメインを新規実装する場合にも共通する参考資料と禁止パターンです。
このあとに続く「新ドメイン実装依頼」の内容を実装する際に踏まえてください。

{_CPO_FORBIDDEN_PATTERNS}
{_I18N_MESSAGE_DICT_INSTRUCTION if is_dsl4 else ""}
{attachment_section}
"""

    # 動的パート: ドメイン固有の内容（毎回変わるためキャッシュ対象外）。
    # 2026-07-18: tightシナリオのFILEブロック・SCENARIOS登録を廃止。
    # baseline/infeasibleの2シナリオのみ生成させる。
    #
    # 2026-08-02追加: family_reference（同CSPLibファミリー内の既存実装の構造サマリ）は
    # domain_def内に混在させず、明示的に区切ったセクションとして提示する（「ドメイン定義」の
    # 一部＝仕様の一部と誤解されないようにするため。あくまで参考情報であり、本ドメイン自身の
    # 構造が兄弟ドメインと異なるならそれに従ってよい）。詳細は lookup_family_reference() および
    # 2026-08-05追加: structural_requirements（ヒアリングテキストのみから機械的に抽出した
    # 業務構造チェックリスト、詳細はderive_structural_requirements()docstring参照）。
    # family_reference/formulation_directiveと異なりCSPLib一致の有無に関わらず全ての
    # new_domain生成で存在しうる。「今回何を実装すべきか」の一次情報として、
    # family_reference/formulation_directive（存在する場合の補助的な技術選定情報）より
    # 前段に、かつ「必須順守」として提示する。
    structural_requirements = domain_def.pop("structural_requirements", None)
    if structural_requirements:
        def _bullets(items):
            items = items or []
            return "\n".join(f"- {i}" for i in items) or "（記載なし）"

        entities = structural_requirements.get("entities") or []
        entities_block = "\n".join(
            f"- {e.get('name','')}（{e.get('role','')}）: {e.get('notes') or '(補足なし)'}"
            for e in entities
        ) or "（記載なし）"

        structural_requirements_section = f"""
## 業務構造チェックリスト（必須順守・ヒアリング内容の構造化要約）

> ヒアリングテキストのみから機械的に抽出した、今回の業務構造の要約です。CSPLibの知識や
> CP/MIPの技術選定は含まれておらず、ヒアリングに書かれている内容の言い換えに過ぎません。
> 添付の参考資料（他ドメインのソースコード）と構造が食い違う場合は、必ず本セクションを
> 優先してください。ここに書かれている構造（特に「通常と異なる割り当て構造」「資源共有」
> 「目的の優先方法」）を単純化・省略しないこと。

### エンティティ
{entities_block}

### 通常と異なる割り当て構造
{structural_requirements.get('assignment_structure_notes') or '（特になし・通常の1対1割り当て）'}

### 絶対に守るべきルール
{_bullets(structural_requirements.get('hard_rules'))}

### できれば守りたいルール
{_bullets(structural_requirements.get('soft_rules'))}

### 目的の優先方法
種別: {structural_requirements.get('objective_priority_type') or 'unknown'}
{_bullets(structural_requirements.get('objective_stages'))}

### 資源の同時使用に関するルール
{structural_requirements.get('resource_sharing_notes') or '（記載なし）'}

### 対応しきれない場合・解けない場合の扱い
{structural_requirements.get('infeasible_handling_notes') or '（記載なし）'}

### その他の構造上の注意点
{_bullets(structural_requirements.get('other_structural_notes'))}
"""
    else:
        structural_requirements_section = ""

    # OptiBuddy_V81_devnotes/DESIGN_2026-08-02_family_structural_reference.md 参照。
    family_reference = domain_def.pop("family_reference", None)
    if family_reference:
        tp = family_reference.get("this_problem") or {}
        this_problem_block = f"""### 対象問題自身のCSPLib参照情報（{tp.get('csplib_id','')} {tp.get('title_ja','')}）
概要: {tp.get('summary_ja') or '(なし)'}
推奨解法: {tp.get('recommended_approach') or '(断定情報なし)'}
根拠: {tp.get('approach_note_ja') or '(なし)'}
"""
        siblings = family_reference.get("siblings", [])
        if siblings:
            sibling_blocks = "\n\n".join(
                f"### 兄弟: {s.get('csplib_id')} {s.get('title_ja')} → {s.get('optibuddy_domain')}"
                f"（採用技術: {s.get('recommended_approach') or '不明'}）\n{s.get('structure_summary')}"
                for s in siblings
            )
        else:
            sibling_blocks = "（同ファミリー内に登録済みの兄弟ドメインはまだありません）"
        family_reference_section = f"""
## 参考情報: CSPLibファミリー構造（{family_reference.get('family_name_ja') or ''}、強制ではない）

> {family_reference.get('note', '')}

{this_problem_block}
{sibling_blocks}
"""
    else:
        family_reference_section = ""

    # 2026-08-03追加: formulation_directive（執筆型、Phase4）。family_referenceは
    # 「参考情報・強制ではない」と明示するのに対し、formulation_directiveは
    # ヒアリング担当者（本エージェント）がヒアリング内容とfamily_referenceを踏まえて
    # 明示的に確定させた技術的決定であるため、「必須順守」として別枠で強く提示する。
    formulation_directive = domain_def.pop("formulation_directive", None)
    if formulation_directive:
        constraints = formulation_directive.get("constraints") or []
        constraints_block = "\n".join(f"- {c}" for c in constraints) or "（記載なし）"
        formulation_directive_section = f"""
## 技術指示（必須順守・formulation_directive）

> ヒアリング担当者がヒアリング内容を精査して確定させた、この業務固有の正しい定式化です。
> 下記の参考情報（同ファミリーの実装例）と矛盾する場合でも、本セクションの内容を優先してください。

決定変数: {formulation_directive.get('decision_variables', '(記載なし)')}
制約:
{constraints_block}
目的関数の型: {formulation_directive.get('objective_type', '(記載なし)')}
目的関数: {formulation_directive.get('objective_expression') or '(記載なし)'}
採用技術: {formulation_directive.get('recommended_technology', '(記載なし)')}
根拠: {formulation_directive.get('rationale') or '(記載なし)'}
"""
    else:
        formulation_directive_section = ""

    dynamic_part = f"""# 新ドメイン実装依頼（V4.4）

## ドメイン定義
{json.dumps(domain_def, ensure_ascii=False, indent=2)}
{structural_requirements_section}
{formulation_directive_section}
{family_reference_section}
## 実装方針
- problem_class: "{name}"
- domain (snake_case): "{snake}"
{impl_note}

{converter_solver_contract}
## 出力形式（マーカー区切り・全ファイルを出力すること）

===FILE:Backend/solvers/{snake}_solver.py===
（完全なソルバー実装。クラス名は {name}Solver とすること）
===END===
{dsl4_files}
{i18n_messages_file}
===FILE:Backend/dsl_repository/scenarios/{snake}_baseline.json===
（標準シナリオJSON。必ず problem_class="{name}", domain="{snake}" を含めること）
===END===

===FILE:Backend/dsl_repository/scenarios/{snake}_infeasible.json===
（実行不可能シナリオJSON。必ず problem_class="{name}", domain="{snake}" を含めること）
===END===

{dsl4_patches}
[2026-09-18追加] 次のSCENARIOSブロックの各シナリオの "description" は、名前の反復
（プレースホルダー）ではなく、この業務が実際に何を扱うか（対象・制約・目的）を1〜2文で
要約した実質的な説明にすること。後日、別の新規ドメイン登録時にこの説明文だけを見て
「既存ドメインとして流用できるか」を判定する仕組みがあり、名前を繰り返しただけの説明では
正しい判定ができない（PatientTransportPlannerがRideshareMatchingPlannerの空同然の
説明文のせいで誤って同一視された実例がある）。
NG例: "description": "{name}の標準シナリオ"
OK例: "description": "約20件の送迎依頼を4台の車両に割り当て、乗車定員内での相乗りを許可しつつ
往復ペア・対応区分を考慮して配車するシナリオ"（実際の業務内容に即して書くこと。この例文を
そのまま使い回さないこと）
===SCENARIOS===
[
  {{"name": "{name} — 標準",   "description": "（この業務の対象・制約・目的を1〜2文で要約すること。上記OK例参照）", "tag": "{name[:6].upper()}", "tag_color": "#8b5cf6", "domain": "{snake}", "file": "Backend/dsl_repository/scenarios/{snake}_baseline.json"}},
  {{"name": "{name} — 解なし", "description": "（同上。実行不可能になる具体的な理由を含めること）", "tag": "{name[:6].upper()}", "tag_color": "#ef4444", "domain": "{snake}", "file": "Backend/dsl_repository/scenarios/{snake}_infeasible.json"}}
]
===END===
"""

    return static_part + _STAGE2_CACHE_SPLIT + dynamic_part


# ─────────────────────────────────────────────────────────────
# Stage 2: ファイル生成・適用
# ─────────────────────────────────────────────────────────────

def _parse_marker_output(raw: str) -> dict:
    files, patches = [], []
    scenario_registrations = []
    for m in re.finditer(r'===FILE:(.+?)===\n(.*?)===END===', raw, re.DOTALL):
        content = m.group(2)
        if content.endswith('\n'): content = content[:-1]
        files.append({"path": m.group(1).strip(), "content": content})
    for m in re.finditer(
        r'===PATCH:(.+?)===\n(?:INSTRUCTION:\s*(.+?)\n)?===CODE===\n(.*?)===END===',
        raw, re.DOTALL
    ):
        append_code = m.group(3)
        if append_code.endswith('\n'): append_code = append_code[:-1]
        patches.append({"path": m.group(1).strip(), "instruction": (m.group(2) or "").strip(),
                        "append_code": append_code, "approved": True})
    m = re.search(r'===SCENARIOS===\n(.*?)===END===', raw, re.DOTALL)
    if m:
        try:
            scenario_registrations = json.loads(m.group(1).strip())
        except json.JSONDecodeError as e:
            logger.warning(f"[parse_marker] SCENARIOS パース失敗: {e}")
    logger.info(f"[parse_marker] files={len(files)}, patches={len(patches)}, scenarios={len(scenario_registrations)}")
    return {"files": files, "patches": patches, "scenario_registrations": scenario_registrations}


def _build_scenario_registrations_from_diffs(approved_diffs: list) -> list:
    # 2026-07-18: tightを廃止（baseline/infeasibleの2シナリオ原則）。
    # 既存ドメインのtightファイルがdiffsに混ざっていても単に無視されるだけで、
    # 過去の登録・DB行には影響しない（このヘルパーは新規/追加登録時のみ使用）。
    registrations = []
    suffix_label = {"baseline": "— 標準", "infeasible": "— 解なし"}
    suffix_color = {"baseline": "#8b5cf6", "infeasible": "#ef4444"}
    for item in approved_diffs:
        path    = item.get("path", "")
        content = item.get("new_content", "")
        m = re.match(r"Backend/dsl_repository/scenarios/(.+)_(baseline|infeasible)\.json$", path)
        if not m:
            continue
        snake  = m.group(1)
        suffix = m.group(2)
        try:
            dsl_json      = json.loads(content)
            problem_class = dsl_json.get("problem_class", _to_pascal(snake))
            label         = f"{problem_class} {suffix_label[suffix]}"
            registrations.append({
                "name":        label,
                "description": f"{problem_class} の{suffix_label[suffix].strip('— ')}ケース",
                "tag":         problem_class[:6].upper(),
                "tag_color":   suffix_color[suffix],
                "domain":      snake,
                "file":        path,
            })
        except Exception as e:
            logger.warning(f"[fallback] シナリオJSON パース失敗: {path} — {e}")
    if registrations:
        logger.info(f"[fallback] approved_diffs から {len(registrations)} 件構築")
    return registrations


def generate_domain_files(domain_def: dict, attachment_overrides: dict = None) -> dict:
    from llm.llm_client import upload_repomix_if_changed, call_llm_with_file, call_llm, LLM_PROVIDER

    domain_name  = domain_def.get("domain_name", "NewDomain")
    # 2026-08-04: classify_problem()の is_dsl4_candidate をそのまま使わず、
    # 常に4DSL準拠（converter/ui_converter/solverの3点セット）で生成するよう固定した。
    # 背景: Stage1aプロンプト自身が「新規ドメインは原則true（4DSLが現行の標準
    # アーキテクチャ）」と明記しているにもかかわらず、is_dsl4_candidate=falseと
    # 判定されるケース（SteelMillSlabDesign、2026-08-04）が実際に発生した。
    # false側の「従来方式（レガシー）」はconverter/ui_converterを生成しないため
    # ui_dslが常に空になり、Overview画面が「UI DSLデータがありません」になる
    # （ソルバー自体は正しく動いていても画面に何も出ない、実害あり）。
    # コード上にfalseを正当化する明確な基準も見当たらなかったため、
    # is_dsl4_candidateの値に関わらず常にTrueとして扱うことにした
    # （詳細はOptiBuddy_V81_devnotes/ENGINEERING_LOG.md 2026-08-04参照）。
    # classification自体のis_dsl4_candidateフィールドはログ・記録用にそのまま残す。
    is_dsl4      = True
    exists_check = check_domain_exists(domain_name)

    attachments = dict(DEFAULT_ATTACHMENTS)
    if attachment_overrides:
        for key, val in attachment_overrides.items():
            if val is None: attachments.pop(key, None)
            else: attachments[key] = val

    repomix_path     = attachments.pop("repomix-domain-addition.xml", None)
    attachment_texts = {}
    for fname, fpath in attachments.items():
        try:
            attachment_texts[fname] = Path(fpath).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"添付ファイル読み込み失敗 {fpath}: {e}")

    prompt = _build_stage2_prompt(domain_def, attachment_texts, is_dsl4=is_dsl4)
    system = "あなたはOptiBuddyの開発アシスタントです。指定されたマーカー形式のみで出力。説明文不要。"

    if _STAGE2_CACHE_SPLIT in prompt:
        static_text, dynamic_text = prompt.split(_STAGE2_CACHE_SPLIT, 1)
    else:
        static_text, dynamic_text = "", prompt

    if LLM_PROVIDER == "anthropic" and static_text.strip():
        # 静的パート（全ドメイン共通の参考資料・禁止パターン）を独立したcontentブロックとして
        # cache_control付きで送る。動的パート（ドメイン固有内容）は別ブロックでキャッシュ対象外。
        user_content = [
            # 2026-08-04: デフォルトTTLが5分に変更されたため明示的に1hへ延長
            # （詳細はllm_client.py _cacheable_system のコメント参照）。
            {"type": "text", "text": static_text, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
            {"type": "text", "text": dynamic_text},
        ]
    else:
        # 非Anthropicプロバイダー、または静的パートが空の場合は従来通り1本の文字列。
        user_content = static_text + dynamic_text

    messages = [{"role": "user", "content": user_content}]

    # 2026-08-05変更: 16000→24000に引き上げ。structural_requirements（業務構造チェックリスト）
    # セクション追加によりStage2の出力が伸び、PatientTransportPlanner再登録実機テストで
    # output=16000ちょうどで打ち切られ、SCENARIOSブロック（baseline/infeasible JSON・
    # ファイル登録用パッチ）が生成されないまま終わる不具合が発生した（ENGINEERING_LOG.md
    # 2026-08-05追記12参照）。converter/solver/ui_converterのファイル本体を出力し切った後に
    # SCENARIOSブロックが来る出力順のため、打ち切りは静かな「一部ファイルだけ書き込まれ、
    # 登録は完了しない」という気づきにくい不具合になる。
    # 2026-09-09変更: プロバイダー分岐をllm_client.call_llm_with_file側に集約した。
    # 従来はAnthropic以外だとrepomix添付が丸ごと無視されていたが、Anthropic以外でも
    # repomix_pathを渡せば（Files API相当が無い分は）インライン添付にフォールバック
    # してコードベースのコンテキストを渡すようになった。ただしそのXML自体の再生成
    # （repomix実行）は引き続きAnthropic時にしか行われない（Koshoshi合意: 現状
    # Anthropic以外は実運用で使っていないため許容し、実際に切り替える段になったら
    # そのベンダー向けに作り込む）。
    if repomix_path and Path(repomix_path).exists():
        file_id = upload_repomix_if_changed()  # Anthropic以外では""が返る想定通りの挙動
        raw = call_llm_with_file(
            messages, file_id, system=system, max_tokens=24000, repomix_path=repomix_path
        )
    else:
        raw = call_llm([{"role": "system", "content": system}] + messages, max_tokens=24000)

    parsed = _parse_marker_output(raw)
    diffs  = [compute_diff(f["path"], f["content"]) for f in parsed["files"] if f.get("path")]
    return {"status": "ok", "diffs": diffs, "patches": parsed["patches"],
            "scenario_registrations": parsed["scenario_registrations"], "exists_check": exists_check}
