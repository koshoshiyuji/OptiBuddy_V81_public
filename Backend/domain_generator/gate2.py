
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
    _SCENARIOS_DIR,
    logger,
)
from .build_checks import (
    _sanitize_solver_code,
)
from .family_reference import (
    check_family_technology_conformance,
)
from .field_repair import (
    _field_check_total,
    attempt_field_consistency_repair,
)
from .hearing_gaps import (
    build_code_diff,
    detect_hearing_dsl_gaps,
    detect_hearing_dsl_gaps_incremental,
    extract_domain_artifacts_from_diffs,
)
from .humanize import (
    _humanize_exception_findings,
    _humanize_required_gap_findings,
    humanize_technical_findings,
    scan_diffs_for_warnings,
)
from .registry_patch import (
    _to_pascal,
)
from .static_checks import (
    _check_i18n_message_coverage,
    check_converter_solver_field_consistency,
)
from .utils import (
    _resolve_path,
)




# ─────────────────────────────────────────────────────────────
# Gate2動的チェック: ドメイン別バリデータ registry
#
# 背景: Backend/validators/ 配下にドメイン固有のDSLバリデータ（例:
# NurseShiftValidator）を追加した際、Gate2動的チェックから自動的に
# 呼び出せるようにするための最小限のレジストリ。
# 「snake_case ドメイン名 → Validatorクラス」の対応をここに1行足すだけで、
# run_gate2_dynamic_verification() が該当ドメインのシナリオ変換直後に
# .validate() を実行し、errors/warnings を report に合流させる。
# 未登録ドメインは従来通りスキップされる（solver.solve()のみで検証）。
# ─────────────────────────────────────────────────────────────

_DOMAIN_VALIDATORS: dict = {}


def _register_default_domain_validators() -> None:
    """既知ドメインの検証器を _DOMAIN_VALIDATORS に登録する。

    ここでのimport失敗（未登録ドメインのvalidatorsパッケージが存在しない等）は
    Gate2全体を止めないよう握りつぶす。新しいドメインバリデータを追加する場合は
    ここに setdefault() を1行足すだけでよい。

    【2026-07-13】旧"nurse_shift"登録（NurseShiftValidator）を削除した。
    NurseShift（週次夜勤上限なしの旧ベース版）自体をdelete_nurse_shift_base_domain.py で
    完全削除した際、このバリデータ登録・Backend/validators/nurse_shift_validator.py・
    Backend/dsl_repository/run_gate2_nurse_shift.py が削除対象漏れで孤立していたため、
    今回まとめて削除した。現時点で _DOMAIN_VALIDATORS に登録されたドメインはない
    （NurseShiftWeeklyCap用の専用バリデータは未実装）。
    """
    pass


_register_default_domain_validators()


# ─────────────────────────────────────────────────────────────
# Gate2動的チェック（2026-07-27追加）: CE上限（CPLEX Community Edition評価版
# モデルサイズ上限）の実機ストレス検証
#
# 背景: solvers/base/ce_limit_lns.py の汎用CE上限フォールバックは、これまで
# 各ドメインのユニットテスト（solve()をmonkeypatchして偽のCeLimitExceededError
# を発生させる）でしか配線を検証しておらず、「実際に上限に当たった時に本物の
# CP Optimizerエンジンが検知・フォールバックまで正しく動くか」は
# DESIGN_2026-07-26_generic_ce_limit_fallback.md 決定事項#12で「今後の課題」
# とされていた。2026-07-27にこのサンドボックスで実際にCP Optimizerエンジン
# （cpoptimizerバイナリ）が動くことを確認できたため、ここで実装する。
#
# 対象: solver.pyが solvers.base.ce_limit_lns をimportしているCPドメインのみ
# （TruckDispatcher/YardPlanningは専用の分解器(RouteDecomposer/
# DecomposerFactory)を持つため対象外。StoreSite等のMIPドメインは
# ce_limit_mip_fallback.py側で別のシグネチャ・別の対応方式を使うため、
# 本チェックのスコープ外＝今回は対象にしない）。
#
# 手法: {snake}_baseline.json を読み込み、ドメインごとに宣言した「分割軸に
# 相当するリストフィールド」を複製・拡大しながら実際にsolve()を呼び、本物の
# CE上限例外（"Problem size limit exceeded"等）を発生させる。発生したら、
# 結果が (a) ce_limit_unresolvable issue で graceful に終わっている
# （本ドメインにbatch_decomposerが無い場合）か、(b) _decompose_meta付きで
# 実際にバッチ分割フォールバックが機能している（batch_decomposerがある場合）
# かを確認する。CE上限の実機チェック自体は変数・制約の宣言数に対する
# ライセンス側の静的チェックのため、実際に探索を回すより先に即座に失敗する
# （2026-07-27に実機確認済み: LineChangeoverScheduler 0.02秒、
# CarSequencing 1.1秒）。そのため多少大きいサイズを試しても登録フロー全体の
# 所要時間への影響は小さい。
# ─────────────────────────────────────────────────────────────

# ドメインごとの「CE上限を発火させるために複製するリストフィールド」の宣言。
# ここに無いドメインは本チェックをスキップする（non-applicable扱い、
# 新しいCPドメインを追加する際はここに1行足すだけでよい）。
_CE_LIMIT_STRESS_INFLATE_FIELD: dict[str, tuple] = {
    # snake名: (フィールド名, そのリスト内エンティティのid キー名)
    "nurse_shift_weekly_cap":   ("staff", "id"),
    "meeting_room":             ("rooms", "id"),
    "line_changeover_scheduler": ("tasks", "id"),
    "car_sequencing":           ("car_types", "car_type_id"),
}


def _inflate_list_field(entities: list, target_count: int, id_field: str) -> list:
    """
    entities（baselineシナリオ由来の実データ）を複製し、target_count件になる
    まで増やす。複製元は既に実際に動くデータであることが保証されているため、
    スキーマ不整合のリスクなしにサイズだけを膨らませられる。
    id_fieldはユニーク性が必要な識別子フィールド（複製時にサフィックスを付与）。
    """
    if not entities or target_count <= len(entities):
        return copy.deepcopy(entities)
    out = list(copy.deepcopy(entities))
    i = 0
    while len(out) < target_count:
        base = entities[i % len(entities)]
        clone = copy.deepcopy(base)
        clone[id_field] = f"{clone.get(id_field, 'x')}_stress{i}"
        out.append(clone)
        i += 1
    return out


def run_gate2_ce_limit_stress_check(
    snake: str,
    convert_fn,
    solver_class,
    max_attempts: int = 6,
    max_total_seconds: float = 20.0,
    require_cp_engine: bool = True,
) -> dict:
    """
    {snake}_baseline.jsonを複製・拡大しながら実際にCE上限を発火させ、
    ドメインのCE上限フォールバック配線が実機で正しく動くかを検証する。

    2026-07-27追記（実機で発覚したハングの修正）: ドメインのsolve()が例外を
    自前でcatchして"solver_error"等のissueに変換して返す実装（MeetingRoom/
    CarSequencing等）の場合、CP Optimizerエンジン自体が使えない環境
    （cpoptimizerバイナリ不在）でも例外が外に伝播しないため、本関数の
    except Exceptionでは検知できない。この場合「CE上限がまだ発火していない
    だけ」と誤認して次のtargetでリトライを繰り返してしまい、docplexの内部
    エージェント生成処理が失敗を重ねることで**単発のsolve()呼び出し自体が
    ブロックする**（ループ外からの経過時間チェックでは検知できない）事象を
    実機（test_post_registration_fix.py経由）で確認した。このため、根本対策
    として require_cp_engine=True（既定）の場合は実行前に
    shutil.which("cpoptimizer") でエンジンの実在を確認し、無ければ即座に
    "engine_unavailable" を返して一切solveを試みない。壁時計時間の予算
    （max_total_seconds、既定20秒）は複数attempt間のみをチェックする
    セカンダリの安全弁（単発のsolve()自体がハングするケースは防げない）。
    テストでフェイクのsolver_classを使う場合はrequire_cp_engine=Falseを
    指定してこのチェックをバイパスできる。

    Returns:
        dict: {
          "status": "not_applicable" | "missing_baseline" | "engine_unavailable"
                    | "ce_limit_not_triggered" | "timed_out" | "graceful_no_adapter"
                    | "fallback_succeeded" | "exception",
          "sizes_tried": [...],       # 試行したエンティティ件数（該当時のみ）
          "triggered_at": int,        # CE上限が実際に発火した件数（該当時のみ）
          "issue_ids": [...],         # 発火時のresult["issues"]のidリスト（該当時のみ）
          "decompose_meta": dict,     # fallback_succeeded時のresult["_decompose_meta"]
          "traceback": str,           # exception時のみ
        }
    """
    import traceback as tb_module

    entry: dict = {"status": None}

    inflate_spec = _CE_LIMIT_STRESS_INFLATE_FIELD.get(snake)
    if inflate_spec is None:
        entry["status"] = "not_applicable"
        return entry

    if require_cp_engine and shutil.which("cpoptimizer") is None:
        entry["status"] = "engine_unavailable"
        return entry

    field_name, id_field = inflate_spec
    baseline_path = _SCENARIOS_DIR / f"{snake}_baseline.json"
    if not baseline_path.exists():
        entry["status"] = "missing_baseline"
        return entry

    baseline_dsl = json.loads(baseline_path.read_text(encoding="utf-8"))
    base_entities = baseline_dsl.get(field_name, [])
    if not base_entities:
        entry["status"] = "missing_baseline"
        return entry

    sizes_tried: list = []
    target = max(50, len(base_entities) * 10)
    start_time = time.time()

    for _attempt in range(max_attempts):
        if time.time() - start_time > max_total_seconds:
            entry["status"] = "timed_out"
            entry["sizes_tried"] = sizes_tried
            return entry

        sizes_tried.append(target)
        stress_dsl = copy.deepcopy(baseline_dsl)
        stress_dsl[field_name] = _inflate_list_field(base_entities, target, id_field)

        # 2026-07-27追記: CE上限がまだ発火しない小さすぎるtargetの場合、
        # ソルバーが本物のTimeLimit（既定30〜60秒）いっぱいまで探索してから
        # 次のtargetへ進んでしまい、max_attempts回×TimeLimit分の遅延に
        # なりうる（実機テストでタイムアウトを確認）。CE上限の検知自体は
        # 変数・制約の宣言数に対する静的チェックでTimeLimitに関係なく
        # 即座に発生するため、ここでは「発火しなかった場合の通常探索」の
        # 時間だけを短く抑える（発火した場合の判定結果には影響しない）。
        stress_config = dict(stress_dsl.get("config", {}))
        for time_key in ("time_limit_sec", "solve_time_sec", "time_limit"):
            if time_key in stress_config:
                stress_config[time_key] = 3
        stress_dsl["config"] = stress_config

        try:
            solver_input = convert_fn(stress_dsl) if convert_fn else stress_dsl
            solver_input.setdefault("issue_statuses", {})
            result = solver_class(solver_input).solve()
        except Exception:
            entry["status"] = "exception"
            entry["traceback"] = tb_module.format_exc()
            entry["sizes_tried"] = sizes_tried
            return entry

        issue_ids = {i.get("id") for i in result.get("issues", [])}
        if "ce_limit_unresolvable" in issue_ids:
            entry["status"] = "graceful_no_adapter"
            entry["sizes_tried"] = sizes_tried
            entry["triggered_at"] = target
            entry["issue_ids"] = sorted(issue_ids)
            return entry
        if "_decompose_meta" in result:
            entry["status"] = "fallback_succeeded"
            entry["sizes_tried"] = sizes_tried
            entry["triggered_at"] = target
            entry["decompose_meta"] = result["_decompose_meta"]
            return entry

        target *= 4

    entry["status"] = "ce_limit_not_triggered"
    entry["sizes_tried"] = sizes_tried
    return entry


# ─────────────────────────────────────────────────────────────
# Gate2動的チェック: 実ソルブ検証（baseline/infeasible）
#
# 背景: Gate1/Gate2設計合意（docs/DESIGN_2026-07-09_registration_gates_and_hard_soft.md）
# で定めた最小構成。例外を握り潰さない（HospitalShiftPlanner bug#1のように
# KeyErrorがexceptで飲み込まれ「解けているのに無言で失敗扱い」になることを
# 二度と起こさない）。期待ステータス（baseline→feasible, infeasible→infeasible）
# を確認する。
#
# 注記（2026-07-18）: 従来はここに tight シナリオ（「際どいが解ける」ケースを
# 想定し、baselineとのKPI比較で劣化方向を検出する仕組み）が含まれていたが、
# 実機で「tightの設計意図が曖昧になりやすく、デバッグエージェントがその解釈の
# 確認に往復を使うばかりで、登録所要時間の増加に見合う効果が薄い」という
# Koshoshiの判断により廃止した（MeetingRoom登録時の実例で判明）。
# 新規ドメインはbaseline/infeasibleの2シナリオのみを生成・検証する。
# 既存ドメインが持つtightシナリオファイル・DB登録は遡及削除しない
# （このロジック変更は新規登録のみに影響する）。
#
# 注記（V4.7）: このチェックは app.py の _run_domain_job（new_domain登録フロー）に
# 自動接続されている。write_files_for_dynamic_check() で承認前のsolver/converter/
# シナリオファイルを実ファイルとして書き出した上でこの関数を呼び、warningsを
# 静的Gate2と同じneeds_confirmationゲートに合流させることで、人間の確認画面は
# 1回のまま増やさない。Flaskは長時間稼働プロセスであるため、同一snake名の
# ドメインを複数回検証すると importlib のモジュールキャッシュが古いコードを
# 参照し続ける恐れがあり、既に sys.modules にある場合は importlib.reload() で
# 強制的に再読み込みする。
#
# 注記（V4.8）: solver.solve()を呼ぶ直前に、_DOMAIN_VALIDATORS に登録された
# ドメイン固有バリデータ（変換後のsolver_input DSLに対する構造的・意味的検証）
# があれば実行する。ソルバーが「例外なく解を返してしまう」ため feasible/infeasible
# 判定だけでは検出できない不具合（例: 資格要件を満たすスタッフの絶対数が
# required_countに足りていない）を、ソルブ前に機械的に洗い出す狙い。
# バリデータのerrorsもGate2の他の警告と同様にneeds_confirmationへの警告
# 追加であり、登録を直接ブロックしない。
# ─────────────────────────────────────────────────────────────

# Gate2動的チェック: 退化解検知（簡易版、2026-08-08追加）
#
# 背景: baselineシナリオはsolve()がfeasible=Trueを返しさえすれば「OK」と
# 判定されてきたが、実際には「割当対象も資源も存在するのに、ほとんど何も
# 割当されていない」退化解（例: coverage_rate=0付近）でもfeasible=Trueには
# なり得る。制約の書き方次第では「何も割当しない」ことが目的関数上最も
# 安価な解になってしまうケースがあり、この種の不具合はfeasible/infeasible
# 判定だけでは検出できない。
#
# 「簡易版」とした理由: ドメインごとにmetricsのフィールド名は自由（Stage2が
# 都度生成するため）で、汎用的に「割当対象の総数」「資源の総数」を機械的に
# 取り出す共通スキーマは無い。そこで、(1) 各ドメインのKPI慣習として広く
# 使われている`coverage_rate`キー（0.0〜1.0）がmetricsにある場合のみ判定対象とし、
# (2) 誤検知防止として、solver_input（converter出力）のトップレベルに
# 空でないlistフィールドが2種類以上ある場合のみ「割当対象・資源とも
# 存在する」の代理指標とみなす。この2条件を満たさない場合は判定をスキップし、
# 何も警告しない（false negativeを許容し、false positiveを避ける設計）。
#
# infeasibleシナリオは対象外（意図的に資源を逼迫させ未割当を多く出す設計の
# ため、同じ閾値で判定すると誤検知になる。Koshoshi合意、2026-08-08）。
#
# 適用範囲: 今後の新規登録（new_domain・拡張とも）から。既存登録済み
# ドメインへの遡及チェックは対象外（別タスク）。
_DEGENERATE_COVERAGE_RATE_THRESHOLD = 0.05


def _check_baseline_degenerate_solution(solver_input: dict, metrics: dict | None) -> str | None:
    """
    baselineシナリオの退化解検知（簡易版）。該当すれば警告文字列を、
    判定対象外・問題なしならNoneを返す。
    """
    if not isinstance(metrics, dict):
        return None
    rate = metrics.get("coverage_rate")
    if not isinstance(rate, (int, float)):
        return None
    if rate > _DEGENERATE_COVERAGE_RATE_THRESHOLD:
        return None
    if not isinstance(solver_input, dict):
        return None
    nonempty_lists = [k for k, v in solver_input.items() if isinstance(v, list) and len(v) > 0]
    if len(nonempty_lists) < 2:
        # シナリオ自体が薄く（割当対象・資源のいずれかが実質0件の可能性があり）、
        # 判定材料が不足しているため誤検知回避のためスキップする。
        return None
    return (
        f"[Gate2 退化解検知] baselineシナリオでcoverage_rate={rate}です。"
        f"割当対象・資源に相当するフィールド（{sorted(nonempty_lists)}）が空でないにも"
        "かかわらず、割当がほぼ0件の退化解になっている疑いがあります。制約の書き方次第で"
        "「何も割当しない」ことが目的関数上最も安価になっていないか確認してください。"
    )


def run_gate2_dynamic_verification(
    snake_name: str, scenario_suffixes: tuple = ("baseline", "infeasible")
) -> dict:
    """
    指定ドメイン（snake_case）の baseline/infeasible シナリオを
    converter → solver.solve() まで通しで実行し、例外を握り潰さずに検証する。
    _DOMAIN_VALIDATORS に登録があれば、solve()の直前にDSLバリデータも実行する。

    2026-07-18: scenario_suffixesの既定値からtightを外した（新規ドメインは
    baseline/infeasibleのみ生成するため）。既存ドメインに対してtightも含めて
    検証したい場合は、呼び出し側でscenario_suffixesを明示的に指定すればよい
    （このシグネチャは既存ドメイン向けの呼び出しとも互換性を保っている）。

    返り値の scenarios[suffix] 各エントリ:
      status:     "ok" | "exception" | "missing_file"
      feasible:   bool | None
      metrics:    dict | None（solver出力 solutions[0]["metrics"]、取得できれば）
      traceback:  str | None（例外発生時のみ、フルトレースバック）
      validation: dict | None（{"errors": [...], "warnings": [...]}、
                  対応するValidatorが登録されている場合のみ）

    warnings には、期待ステータスとの不一致、および登録済みドメインバリデータが
    検出したerrors/warningsを追加する。

    返り値の lexicographic_mechanism_check（2026-07-22追加、該当ドメインのみ）:
      OBJECTIVE_RECIPES[pascal] が mode="lexicographic" の場合にのみ存在する。
      solvers.base.objective_terms.verify_lexicographic_mechanism() の戻り値
      そのもの（{"ok", "true_lex_objectives", "expected_objectives",
      "big_m_small_diverged", "warnings", "errors"}）。ok=False の場合、
      errorsの内容は warnings にも "[Gate2 lexicographic機構チェック]" 接頭辞
      付きで合流させている。
    """
    import importlib
    import sys
    import traceback as tb_module

    snake  = snake_name
    pascal = _to_pascal(snake)
    report = {"domain": pascal, "snake": snake, "scenarios": {}, "warnings": []}

    def _import_fresh(mod_name: str):
        # 既にimport済みなら reload して、直前に書き出したばかりの新しい
        # ファイル内容を確実に反映させる（同一プロセス内での再検証対策）。
        if mod_name in sys.modules:
            return importlib.reload(sys.modules[mod_name])
        return importlib.import_module(mod_name)

    convert_fn = None
    try:
        converter_module = _import_fresh(f"dsl_transformer.{snake}_converter")
        convert_fn = getattr(converter_module, f"convert_{snake}_to_solver")
    except Exception as e:
        report["warnings"].append(f"converter読み込み失敗（4DSL非準拠ドメインの可能性、素通しで続行）: {e}")

    try:
        solver_module = _import_fresh(f"solvers.{snake}_solver")
        solver_class  = getattr(solver_module, f"{pascal}Solver")
    except Exception as e:
        report["warnings"].append(f"solver読み込み失敗（検証中止）: {e}")
        return report

    validator_class = _DOMAIN_VALIDATORS.get(snake)

    # 【2026-07-22追加】lexicographicモード目的関数のメカニズム回帰ゲート。
    # OBJECTIVE_RECIPES[pascal] が mode="lexicographic" を宣言している場合、
    # このdocplex/CP Optimizer環境で mdl.minimize_static_lex() が正しく解ける
    # ことを、solvers.base.objective_terms.verify_lexicographic_mechanism() の
    # ドメイン非依存識別テストで確認する。過去にlexicographic目的がBig-M近似で
    # 偽装されていた反省から、実装しただけでは信用せず実ソルブで検証する
    # （objective_terms.py の「Big-M代替の禁止」節・Key Learnings参照）。
    # 未登録ドメインやmode未指定（weighted_sum）のドメインはスキップされる。
    try:
        from solvers.base.objective_terms import OBJECTIVE_RECIPES, verify_lexicographic_mechanism
        recipe = OBJECTIVE_RECIPES.get(pascal)
        if recipe is not None and recipe.get("mode") == "lexicographic":
            lex_report = verify_lexicographic_mechanism()
            report["lexicographic_mechanism_check"] = lex_report
            if not lex_report.get("ok"):
                for err in lex_report.get("errors", []):
                    report["warnings"].append(f"[Gate2 lexicographic機構チェック] ERROR: {err}")
            for warn in lex_report.get("warnings", []):
                report["warnings"].append(f"[Gate2 lexicographic機構チェック] WARNING: {warn}")
    except Exception as e:
        report["warnings"].append(f"[Gate2 lexicographic機構チェック] 実行に失敗（非ブロッキング）: {e}")

    for suffix in scenario_suffixes:
        scenario_path = _SCENARIOS_DIR / f"{snake}_{suffix}.json"
        entry = {"status": None, "feasible": None, "metrics": None, "traceback": None, "validation": None}
        if not scenario_path.exists():
            entry["status"] = "missing_file"
            report["scenarios"][suffix] = entry
            continue
        try:
            business_dsl = json.loads(scenario_path.read_text(encoding="utf-8"))
            solver_input = convert_fn(business_dsl) if convert_fn else business_dsl
            solver_input.setdefault("issue_statuses", {})
            # 2026-07-24追加: 解チェッカー（DESIGN_2026-07-21 3-1節）。
            # Gate2登録時はコストを気にせずフルチェックしてよいため、各ソルバーの
            # 解チェッカー（solvers/base/solution_checker.run_or_defer）に
            # 「閾値を無視して常に同期フル実行する」ことを伝える。本番solve時
            # （このフラグが立っていない通常の/baseline呼び出し）とはここでのみ
            # 挙動を変える、最小限のフラグ渡し。
            solver_input.setdefault("_gate2_full_check", True)

            if validator_class is not None:
                v_result = validator_class(solver_input).validate()
                entry["validation"] = {"errors": v_result.errors, "warnings": v_result.warnings}
                for err in v_result.errors:
                    report["warnings"].append(f"[Gate2 domain-validator/{suffix}] ERROR: {err}")
                for warn in v_result.warnings:
                    report["warnings"].append(f"[Gate2 domain-validator/{suffix}] WARNING: {warn}")

            result = solver_class(solver_input).solve()
            entry["status"]   = "ok"
            entry["feasible"] = result.get("feasible")
            entry["issues"]   = result.get("issues", [])
            solutions = result.get("solutions", [])
            entry["metrics"] = solutions[0].get("metrics") if solutions else None

            # 2026-08-08追加: 退化解検知（簡易版）。baselineのみ対象
            # （infeasibleは意図的に資源を逼迫させるため対象外。上部コメント参照）。
            if suffix == "baseline" and entry.get("feasible"):
                degenerate_warning = _check_baseline_degenerate_solution(solver_input, entry["metrics"])
                if degenerate_warning:
                    report["warnings"].append(degenerate_warning)

            # 2026-08-03追加: solver出力 → UI DSL変換（{snake}_ui_converter.py）も
            # 検証範囲に含める。背景: DepotRoutePlanner較正実験（B×Fハイブリッド）で、
            # solver.pyの出力キー（例: travel_distance）と、別ファイルとして生成された
            # ui_converter.pyが期待するキー（travel_distance_km）が一致せずKeyErrorで
            # 実機クラッシュするバグを発見したが、従来この関数はconverter→solver.solve()
            # までしか検証しておらず、solver↔ui_converter間のスキーマ不一致を一切
            # 検知できていなかった。app.py _solve_4dsl_generic()と同じ呼び出し規約
            # （convert_{snake}_to_ui(plan_output, business_dsl=dsl)）を模倣し、solver結果を
            # 実際にUI DSLへ変換できることまで確認する。ui_converter.py自体が存在しない
            # （4DSL非準拠）ドメインは ImportError/AttributeError として無視しスキップする。
            try:
                ui_converter_module = _import_fresh(f"dsl_transformer.{snake}_ui_converter")
                convert_to_ui = getattr(ui_converter_module, f"convert_{snake}_to_ui")
                solutions_for_ui = result.get("solutions", [])
                if solutions_for_ui:
                    for plan in solutions_for_ui:
                        plan_output = {**result, "solutions": [plan]}
                        convert_to_ui(plan_output, business_dsl=business_dsl)
                else:
                    convert_to_ui(result, business_dsl=business_dsl)
            except (ImportError, AttributeError):
                pass  # ui_converter未実装（4DSL非準拠ドメイン）は対象外
        except Exception:
            entry["status"]    = "exception"
            entry["traceback"] = tb_module.format_exc()
        report["scenarios"][suffix] = entry

    # baselineは従来通り厳格に「feasible=True」を期待する。
    baseline_entry = report["scenarios"].get("baseline")
    if baseline_entry is not None and baseline_entry.get("status") == "ok":
        if baseline_entry.get("feasible") is not True:
            report["warnings"].append(
                f"baseline: 期待feasible=True だが実際は {baseline_entry.get('feasible')}"
            )

    # 2026-08-08追加（Koshoshi合意）: infeasibleシナリオの期待値を
    # 「必ずfeasible=False」から「feasible=False、または深刻な未割当
    # （coverage_rateが低いfeasible=True）のいずれかを許容」に緩和する。
    #
    # 背景: MedicalAppointmentScheduler登録で発覚したfalse positive。
    # ヒアリング§4-1で「人数不足を許容し、できるだけ近づける形で他を最適化」
    # （＝資源不足は解なしではなく未割当として表現する設計）が既に確定して
    # いるドメインでは、CPモデルがoptional interval_var等で「全件未割当」を
    # 常に有効な解として許すため、構造上どれだけ資源を逼迫させても本物の
    # infeasible（feasible=False）にはならない。infeasibleシナリオを
    # 「必ずinfeasibleになるべき」と一律に期待する従来のチェックは、この種の
    # 未割当許容型ドメインの正しい設計と噛み合わずfalse positiveを生む。
    #
    # ここでは、Item①（退化解検知）で既に前提としているcoverage_rateという
    # 業界（プロジェクト内）慣習KPIを再利用する。infeasibleシナリオが
    # feasible=Trueでも、coverage_rateが十分低ければ「意図通り資源を逼迫
    # させ、未割当という形で表現できている」とみなし許容する。coverage_rate
    # が無い（その慣習を使っていない）ドメインでは、判定材料が無いため
    # 従来通り厳格にfeasible=Falseを期待する（false negativeよりfalse
    # positiveを避ける今回の目的には合わないため、フォールバックは変えない）。
    _INFEASIBLE_SCENARIO_MAX_ACCEPTABLE_COVERAGE_RATE = 0.8

    infeasible_entry = report["scenarios"].get("infeasible")
    if infeasible_entry is not None and infeasible_entry.get("status") == "ok":
        feasible = infeasible_entry.get("feasible")
        if feasible is False:
            pass  # 期待通り（本物のinfeasible）
        elif feasible is True:
            metrics = infeasible_entry.get("metrics")
            coverage_rate = metrics.get("coverage_rate") if isinstance(metrics, dict) else None
            if (isinstance(coverage_rate, (int, float))
                    and coverage_rate <= _INFEASIBLE_SCENARIO_MAX_ACCEPTABLE_COVERAGE_RATE):
                pass  # 未割当許容型ドメインとして許容（深刻な未割当を確認できた）
            else:
                report["warnings"].append(
                    "infeasible: 期待feasible=False だが実際は True"
                    + (f"（coverage_rate={coverage_rate}）" if coverage_rate is not None
                       else "（coverage_rateが取得できず、未割当許容型ドメインかどうか判定不能）")
                )
        else:
            report["warnings"].append(f"infeasible: 期待feasible=False だが実際は {feasible}")

    # 2026-07-31追加: "solve_failed" issue id 規約チェック。
    # Frontend（useStudioState.tsの hasSolveFailed 判定、InfeasibleView.tsx）は
    # feasibleフラグではなく issues[].id === "solve_failed" のみを見て
    # Infeasible画面（制約見直しタブ・AI緩和提案）を表示するかどうかを決めている
    # （この規約はStage2システムプロンプトにも明記されているが、想定外例外の
    # ハンドリング箇所にしか書かれておらず、業務上のinfeasible判定（msol is None等）
    # を手で書く箇所には注意書きが及んでいなかった。NursingWorkloadBalance登録で、
    # solver.pyがこの分岐で独自のid（"infeasible"）を使ってしまい、feasible=Falseで
    # 正しく解なし判定していたにもかかわらずFrontendがInfeasible画面を出さない
    # 不具合が実際に発生した）。feasible=False の結果を返しているのに
    # id="solve_failed" のissueが1件も無ければ、Frontendの表示が壊れている可能性が
    # 高いため警告する。
    for suffix, entry in report["scenarios"].items():
        if entry.get("status") != "ok" or entry.get("feasible") is not False:
            continue
        issues = entry.get("issues") or []
        if not any(i.get("id") == "solve_failed" for i in issues):
            found_ids = [i.get("id") for i in issues]
            report["warnings"].append(
                f"[Gate2 solve_failed規約チェック] {suffix}: feasible=Falseですが、"
                f"issuesにid='solve_failed'が含まれていません（実際のid: {found_ids}）。"
                "Frontend（InfeasibleView.tsx等）はこのidだけを見てInfeasible画面の"
                "表示可否を決めているため、画面上はFeasible扱いのまま表示される"
                "退行が起きている可能性があります。"
            )

    # 2026-07-27追加: CE上限（CPLEX Community Edition評価版）の実機ストレス検証。
    # solver_classがロードできた場合のみ実行（converter読み込み失敗時はconvert_fn=None
    # のまま続行、素通し）。_CE_LIMIT_STRESS_INFLATE_FIELDに登録の無いドメイン
    # （TruckDispatcher/YardPlanning等、専用分解器を持つドメイン）はnot_applicableで
    # 即座に返るため、対象外ドメインへのコスト増加はない。
    try:
        ce_limit_report = run_gate2_ce_limit_stress_check(snake, convert_fn, solver_class)
        report["ce_limit_stress"] = ce_limit_report
        status = ce_limit_report.get("status")
        if status == "exception":
            report["warnings"].append(
                f"[Gate2 CE上限ストレス検証] 例外が発生しました（CE上限を握り潰さず"
                f"伝播させてしまっている可能性）: {ce_limit_report.get('traceback', '').splitlines()[-1] if ce_limit_report.get('traceback') else ''}"
            )
        elif status == "ce_limit_not_triggered":
            report["warnings"].append(
                f"[Gate2 CE上限ストレス検証] sizes_tried={ce_limit_report.get('sizes_tried')}まで"
                f"複製しましたが、CPLEXの無料版のモデルサイズ上限を発火させられませんでした"
                f"（本チェックの限界の可能性もあるため非ブロッキング。詳細な実機検証が"
                f"必要な場合は手動でより大きいシナリオを試してください）。"
            )
        elif status == "timed_out":
            report["warnings"].append(
                f"[Gate2 CE上限ストレス検証] 壁時計時間の予算（既定20秒）を超えたため"
                f"打ち切りました（sizes_tried={ce_limit_report.get('sizes_tried')}）。"
                f"CP Optimizerエンジン自体が利用できない環境（cpoptimizerバイナリ不在）で、"
                f"solve()が内部で例外を握り潰して通常のエラー結果を返す実装のドメインの場合に"
                f"発生しうる（非ブロッキング。実機でCP Optimizerが利用可能な環境で"
                f"再実行して確認してください）。"
            )
    except Exception as e:
        report["warnings"].append(f"[Gate2 CE上限ストレス検証] 実行に失敗（非ブロッキング）: {e}")

    return report


def write_files_for_dynamic_check(diffs: list, snake: str) -> list[str]:
    """
    run_gate2_dynamic_verification() が実際にimport・実行できるよう、
    承認前のdiffsのうち動的検証に必要な最小限のファイル（solver.py / converter.py /
    baseline・infeasibleシナリオJSON）だけを実ファイルとして書き出す。

    apply_domain_files() と異なり、patches適用・TAG_MAP登録・DB上のシナリオ登録は
    一切行わない（人間確認前の段階のため）。人間が承認すれば、その後の
    apply_domain_files() が同じ内容を再度書き込み（idempotent）し、
    残りの登録処理（patches/TAG_MAP/DB）を行う。
    """
    written = []
    target_suffixes = ("baseline", "infeasible")
    wanted_paths = {
        f"Backend/solvers/{snake}_solver.py",
        f"Backend/dsl_transformer/{snake}_converter.py",
        f"Backend/i18n/{snake}_messages.py",  # 2026-08-21追加: 2026-08-11のi18n必須化(_I18N_MESSAGE_DICT_INSTRUCTION)
                                                  # 以降、solver.pyがsolve()内でこのモジュールに依存するため、
                                                  # 動的検証時点でもディスクに存在させる必要がある
                                                  # (ReviewDocument登録時にModuleNotFoundErrorで発覚)。
        *(f"Backend/dsl_repository/scenarios/{snake}_{suf}.json" for suf in target_suffixes),
    }
    for item in diffs:
        path = item.get("path", "")
        if path not in wanted_paths:
            continue
        content = item.get("new_content", "")
        try:
            if path.endswith("_solver.py"):
                content = _sanitize_solver_code(content, path)
            abs_path = _resolve_path(path)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            written.append(path)
        except Exception as e:
            logger.warning(f"[gate2_dynamic_prep] 書き込み失敗（動的検証をスキップ）: {path} — {e}")
    return written


def cleanup_dynamic_check_files(paths: list[str]) -> None:
    """
    write_files_for_dynamic_check() が書き出したファイルを削除し、人間確認前（承認前）は
    ディスクに何も書き込まれていない、という従来の不変条件を保つ。
    動的検証はこの関数呼び出し前に完了している前提。削除対象は
    write_files_for_dynamic_checkが新規作成したファイルのみ（_run_domain_job側で
    check_domain_existsで事前に重複なしを確認済みのため、上書きリスクはない）。
    承認後はapply_domain_files()が同じ内容を正式に再度書き込む。
    """
    for path in paths:
        try:
            abs_path = _resolve_path(path)
            if abs_path.exists():
                abs_path.unlink()
        except Exception as e:
            logger.warning(f"[gate2_dynamic_prep] クリーンアップ失敗: {path} — {e}")


def refresh_diffs_from_disk(diffs: list, written_paths: list[str]) -> list:
    """
    2026-07-16追加（デバッグチャット構想の再設計）: write_files_for_dynamic_check() が
    書き出したdraftファイル（written_paths）の「今の」ディスク上の内容を読み直し、
    diffs内の対応エントリのnew_contentをそれで上書きする。

    指摘事項が出た後、Stage1a/Stage2を自動で再実行するのではなく、実ファイルとして
    残っているdraftを人間（実質Claude）が直接編集して直す設計に変えたため、
    「直った後の状態」をdiffsに反映してrun_gate2_checks()の再検証やapply_domain_files()
    に渡すために必要。
    """
    written_set = set(written_paths)
    updated = []
    for item in diffs:
        path = item.get("path", "")
        if path in written_set:
            abs_path = _resolve_path(path)
            if abs_path.exists():
                item = {**item, "new_content": abs_path.read_text(encoding="utf-8")}
        updated.append(item)
    return updated


def run_post_registration_fix(snake: str, domain_name: str, warnings: list,
                               should_stop=None, hearing_texts: list | None = None) -> dict:
    """
    2026-07-18f追加: 登録が完了した後でも、Gate2の残存警告（result.gate2_warnings、
    「残りは誤検知と判断し、このまま登録する」で確定した後に残っていたもの）を
    人間が任意で「今すぐ直したい」と思った場合の入口。

    登録前のフロー（run_gate2_checks / _run_confirm_job）とは根本的に違う点:
    修正対象はもう pending/diffs（人間承認待ちの下書き）ではなく、既に登録済みの
    ライブファイル（Backend/solvers/{snake}_solver.py 等）そのもの。
    apply_domain_files()のような「承認後にまとめて書き込む」段階は無く、
    debug_agentのwrite_fileツールがこのパスへ直接書き込む。

    2026-08-10修正（#31設計3-2の最小実装）: 以前はhearing_textsを意図的に空リスト
    固定で渡していた（登録ジョブのpendingはjob_gcで消えている可能性があり、元の
    ヒアリング文脈を確実に復元する手段が無かったため）。現在は登録確定時
    （app.py _finalize_confirm_job）にdsl_evolution_logのdsl_patchへhearing_textsを
    保存するようにしたので、呼び出し側（app.py domain_post_register_fix）がそこから
    読み出して渡せる。渡されなかった場合は従来通り空リストとして扱う
    （後方互換。文脈が無いとエージェントはask_humanで止まりやすくなりうる——その
    場合、修正を完了させずに「要対応」として返す。登録前フローのような
    一時停止→再開の往復はv1では未対応）。

    戻り値:
      {"fixed_summary": str, "gate2_warnings": list[str], "turns_used": int,
       "stopped_reason": str}
      gate2_warningsは、修正後に静的field-check・動的検証を再実行した結果
      （直っていれば減る／空になる。完全解消を保証するものではない）。
    """
    from debug_agent import run_debug_agent

    solver_path        = f"Backend/solvers/{snake}_solver.py"
    converter_path     = f"Backend/dsl_transformer/{snake}_converter.py"
    ui_converter_path  = f"Backend/dsl_transformer/{snake}_ui_converter.py"

    written_paths = [solver_path, converter_path]
    if _resolve_path(ui_converter_path).exists():
        written_paths.append(ui_converter_path)

    agent_result = run_debug_agent(
        questions=warnings,
        written_paths=written_paths,
        domain_name=domain_name,
        hearing_texts=hearing_texts or [],
        snake_name=snake,
        max_turns=8,
        should_stop=should_stop,
        # 2026-08-10: 段階B A/Bテストでトークン-19%・所要時間-5%を確認
        # （Koshoshi承認、詳細はENGINEERING_LOG.md 2026-08-10追記4）。
        # 段階C（本番切替）として有効化。
        summarize_stale_reads=True,
    )

    if agent_result.get("stopped_reason") == "waiting_for_human":
        pending_q = agent_result.get("pending_question", "")
        logger.info(f"[post_registration_fix] {snake}: エージェントが業務判断待ちで停止 "
                    f"（v1では未対応、要対応として返す）: {pending_q}")
        return {
            "fixed_summary": "AIエージェントが業務判断が必要な質問をしたため、修正は完了していません。"
                              "手動で内容を確認・修正してください。",
            "gate2_warnings": [f"（要対応・登録後修正では自動応答できません）{pending_q}", *warnings],
            "turns_used": agent_result.get("turns_used", 0),
            "stopped_reason": "waiting_for_human",
        }

    # 修正後の再検証: run_gate2_checks全体（自己修復ループ・カバレッジLLM呼び出し）は
    # ここでは回さない。それは「新規生成の妥当性を一から確認する」用の重い処理で、
    # 登録後の「今回の修正で直ったか」の確認には過剰（実機ログで自己修復ループが
    # 1回100秒前後かかることを確認済み。docs/ENGINEERING_LOG.md 2026-07-18e参照）。
    new_warnings: list[str] = []
    try:
        converter_code = _resolve_path(converter_path).read_text(encoding="utf-8")
        solver_code    = _resolve_path(solver_path).read_text(encoding="utf-8")
        field_check = check_converter_solver_field_consistency(converter_code, solver_code)
        if field_check["missing_in_converter"]:
            new_warnings.append(
                f"{converter_path} が一度も出力しないキー: {field_check['missing_in_converter']} "
                f"（{solver_path} が参照）"
            )
        if field_check["unused_in_solver"]:
            new_warnings.append(
                f"{solver_path} が一度も参照しないキー: {field_check['unused_in_solver']} "
                f"（{converter_path} が出力）"
            )
    except Exception as e:
        new_warnings.append(f"修正後の静的field-check実行に失敗: {e}")

    try:
        dyn_report = run_gate2_dynamic_verification(snake)
        for w in dyn_report.get("warnings", []):
            new_warnings.append(f"[動的検証] {w}")
        for suffix, entry in dyn_report.get("scenarios", {}).items():
            if entry.get("status") == "exception":
                tb_text = entry.get("traceback") or ""
                last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "不明なエラー"
                new_warnings.append(
                    f"（実装のバグの疑い）{suffix}シナリオの実行中に例外が発生しました: {last_line}"
                )
    except Exception as e:
        new_warnings.append(f"修正後の動的検証実行に失敗: {e}")

    if agent_result.get("needs_human_decision"):
        # 2026-07-19: app.py の _advance_after_agent_round と同じ理由で
        # 「（人間の判断が必要）」→「（未解決）」に統一。この画面（登録後の
        # 「今すぐ直す」結果表示）にも個別回答の手段は無く、「判断が必要」と
        # 言うと回答できるかのような誤解を招くため。
        for item in agent_result["needs_human_decision"]:
            new_warnings.append(f"（未解決）{item}")

    logger.info(f"[post_registration_fix] {snake}: 修正完了 turns={agent_result.get('turns_used', 0)}, "
                f"残警告={len(new_warnings)}件（修正前={len(warnings)}件）")

    return {
        "fixed_summary": agent_result.get("fixed_summary", ""),
        "gate2_warnings": new_warnings,
        "turns_used": agent_result.get("turns_used", 0),
        "stopped_reason": agent_result.get("stopped_reason", ""),
    }


def run_gate2_checks(diffs: list, snake: str, domain_name: str, hearing_texts: list,
                      on_stage=None, prior_coverage: dict | None = None,
                      prior_diffs: list | None = None) -> dict:
    """
    2026-07-16追加（デバッグチャット構想の再設計）: new_domain登録のGate2チェック一式
    （自己修復ループ・静的field-check・ヒアリング⇔コード カバレッジLLMチェック・
    動的検証・業務向け言い換え）を1箇所にまとめたもの。

    以前の設計との違い: 従来は指摘が出ると「回答をhearing_textsに追記してStage1a〜
    Stage2をまるごと再実行する」自動ループになっていたが、これは「自動で直るという
    前提を置かない」という元々の方針に反していた（実機テストでも指摘が0件に収束せず
    ループするだけだった）。正しい設計は、指摘が出た生成物（このdiffsが指す実ファイル）
    を人間（実質Claude）が直接読んで直すこと。そのため、指摘がある間は
    write_files_for_dynamic_check() が書き出したdraftファイルを削除せず、Claudeが
    通常のファイル編集ツールで直接修正できる状態のままディスクに残す。修正後は
    Stage1a/Stage2を再実行するのではなく、refresh_diffs_from_disk() でdiffsを
    最新のディスク内容に更新した上で、この関数をもう一度呼んでGate2だけを
    再検証する（_run_confirm_job から呼ばれる想定）。

    on_stage: 2026-08-07追加（Koshoshi依頼: Stage2の時間内訳を①LLM生成本体／
      ②Gate2静的チェック／③Gate2動的検証（実ソルブ）の最低3分割で見られるようにする
      計装、CSPLIB_UNIMPLEMENTED_PRIORITY系の引き継ぎメモ対応）。
      呼び出し可能なら on_stage(stage_name: str) をフェーズの境目ごとに呼ぶ。
      app.py側は job_set(job_id, stage=stage_name) を渡すことで、既存の
      domain_job_timing_log（tools/domain_job_timing_report.py）にそのまま
      粒度が反映される（repository.py・job_set()自体の変更は不要——stageが
      変化するたびにstage_timelineへ自動記録される既存の仕組みに乗るだけ）。
      Noneのままなら何もしない（既存呼び出し元との後方互換のため必須引数にしない）。
      内訳の対応:
        verifying_self_repair      … Gate2自己修復ループ（converter/solverキー不一致のLLM自動修正）
        verifying_static_check     … 静的field-check等（AST走査、非LLM）。②に相当
        verifying_hearing_coverage … ヒアリング⇔コード カバレッジLLM判定（detect_hearing_dsl_gaps）
        verifying_tech_conformance … Gate2構造適合チェック
        verifying_dynamic_check    … 実ソルブ検証（write_files_for_dynamic_check + run_gate2_dynamic_verification）。③に相当
        verifying_humanize         … 指摘の業務向け言い換え（humanize_technical_findings一式）
      このコールバックが例外を投げても本体の処理は止めない（計装のためにGate2を
      失敗させては本末転倒のため、_emit_stage()内でtry/exceptして握りつぶす）。

    prior_coverage / prior_diffs: 2026-08-09追加（登録時間短縮タスク2の再開、
      hearing_dsl_gaps差分渡し化）。debug_agentの1ラウンド後にこの関数を
      再呼び出しする際（_advance_after_agent_round経由）、直前のrun_gate2_checks()
      が返した"coverage"（前回のカバレッジ判定結果）と、その時点のdiffs
      （debug_agent実行前のコード）を渡すと、detect_hearing_dsl_gaps()の
      コード全文渡しではなく、変更差分のみを渡すdetect_hearing_dsl_gaps_incremental()
      を使う。単体検証（`tools/stage_b_hearing_dsl_gaps_diff_mode_validation.py`、
      2026-08-09）で所要時間88%短縮・判定精度は全文再チェックと実質一致することを
      確認済み。両方とも省略した場合（初回チェック時等）は、従来通り
      detect_hearing_dsl_gaps()の全文渡しを使う（後方互換）。

    戻り値:
      {"questions": [...], "written_paths": [...]（ディスクに残っている draft ファイルパス）,
       "coverage": dict | None（今回のカバレッジ判定結果。次回呼び出し時にprior_coverageとして
       渡せる。hearing_coverageチェック自体をスキップした場合はNone）}
    """
    def _emit_stage(stage_name: str) -> None:
        if on_stage is None:
            return
        try:
            on_stage(stage_name)
        except Exception as e:
            logger.warning(f"[gate2_checks] on_stage({stage_name!r})の呼び出しに失敗（計装のみのため処理は継続）: {e}")

    repair_notes: list[str] = []
    _emit_stage("verifying_self_repair")
    try:
        repair_result = attempt_field_consistency_repair(diffs, snake)
        if repair_result["attempted"]:
            before, after, attempts = repair_result["before"], repair_result["after"], repair_result["attempts"]
            if repair_result["healed"]:
                repair_notes.append(
                    f"（自動修復）converter/solverのキー不一致を自動検出し、{attempts}回の修正で解消しました"
                    f"（修正前: missing_in_converter={before['missing_in_converter']}, "
                    f"unused_in_solver={before['unused_in_solver']}）。"
                )
            elif _field_check_total(after) < _field_check_total(before):
                repair_notes.append(
                    f"（自動修復試行・部分改善）converter/solverのキー不一致を検出し{attempts}回修正を試みましたが、"
                    f"完全には解消しませんでした（修正前 計{_field_check_total(before)}件 → "
                    f"修正後 計{_field_check_total(after)}件）。下記のfield-check警告を確認してください。"
                )
            else:
                repair_notes.append(
                    f"（自動修復試行・未解消）converter/solverのキー不一致を検出し{attempts}回修正を試みましたが、"
                    "解消しませんでした。下記のfield-check警告を確認してください。"
                )
    except Exception as e:
        logger.warning(f"[gate2_checks] 自己修復ループをスキップ（実行エラー）: {e}", exc_info=True)

    _emit_stage("verifying_static_check")
    try:
        _scan_result = scan_diffs_for_warnings(diffs)
        sanitizer_warnings = _scan_result["warnings"]
        unused_in_solver_warnings = _scan_result["unused_in_solver_warnings"]
        big_m_warnings = _scan_result["big_m_warnings"]
        absent_value_warnings = _scan_result["absent_value_warnings"]
        pulse_float_warnings = _scan_result["pulse_float_warnings"]
        containment_warnings = _scan_result["containment_warnings"]
        missing_in_dsl_for_solver_warnings = _scan_result["missing_in_dsl_for_solver_warnings"]
        mip_self_check_warnings = _scan_result["mip_self_check_warnings"]
    except Exception as e:
        logger.warning(f"[gate2_checks] 静的チェックをスキップ（実行エラー）: {e}", exc_info=True)
        sanitizer_warnings = []
        unused_in_solver_warnings = []
        big_m_warnings = []
        absent_value_warnings = []
        pulse_float_warnings = []
        containment_warnings = []
        missing_in_dsl_for_solver_warnings = []
        mip_self_check_warnings = []

    required_gap_warnings: list[str] = []
    optional_gap_summary: list[str] = []
    coverage: dict | None = None

    _emit_stage("verifying_hearing_coverage")
    try:
        artifacts = extract_domain_artifacts_from_diffs(diffs, snake)
        if artifacts["converter_code"] and artifacts["solver_code"] and artifacts["scenarios"]:
            # 2026-08-09追加: prior_coverage/prior_diffsが両方渡されていれば、
            # 差分渡し版（detect_hearing_dsl_gaps_incremental）を使う。
            # prior側から対象ファイルを抽出できない、またはcoverage未取得
            # （前回hearing_coverageチェック自体がスキップされていた等）の場合は、
            # 安全側に倒して従来の全文渡しにフォールバックする。
            use_incremental = False
            if prior_coverage is not None and prior_diffs is not None:
                try:
                    prior_artifacts = extract_domain_artifacts_from_diffs(prior_diffs, snake)
                    converter_diff = build_code_diff(
                        prior_artifacts.get("converter_code"), artifacts.get("converter_code"), "converter.py"
                    )
                    solver_diff = build_code_diff(
                        prior_artifacts.get("solver_code"), artifacts.get("solver_code"), "solver.py"
                    )
                    ui_converter_diff = build_code_diff(
                        prior_artifacts.get("ui_converter_code"), artifacts.get("ui_converter_code"), "ui_converter.py"
                    )
                    use_incremental = True
                except Exception as e:
                    logger.warning(
                        f"[gate2_checks] hearing_dsl_gaps差分渡しの準備に失敗、全文渡しにフォールバック: {e}"
                    )

            if use_incremental:
                coverage = detect_hearing_dsl_gaps_incremental(
                    domain_name=domain_name, hearing_texts=hearing_texts,
                    prior_result=prior_coverage,
                    converter_diff=converter_diff, solver_diff=solver_diff,
                    ui_converter_diff=ui_converter_diff,
                )
            else:
                coverage = detect_hearing_dsl_gaps(
                    domain_name=domain_name, hearing_texts=hearing_texts,
                    dsl_scenarios=artifacts["scenarios"],
                    converter_code=artifacts["converter_code"], solver_code=artifacts["solver_code"],
                    ui_converter_code=artifacts.get("ui_converter_code"),
                )
            items = coverage.get("items", [])
            gaps = [i for i in items if i.get("status") == "gap"]
            required_gaps = [g for g in gaps if g.get("priority") == "required"]
            optional_gaps = [g for g in gaps if g.get("priority") != "required"]
            for g in required_gaps:
                required_gap_warnings.append(
                    f"ヒアリング{g.get('hearing_section','?')}節（必須）の要件が実装に見当たりません: "
                    f"{g.get('requirement','')}（{g.get('evidence','')}）"
                )
            if optional_gaps:
                optional_gap_summary.append(
                    f"（参考）ヒアリングの任意節の要件のうち{len(optional_gaps)}件が実装に見当たりません"
                    f"（節: {sorted({g.get('hearing_section','?') for g in optional_gaps})}）。"
                    "必須ではないため確認は必須ではありませんが、詳細はGate2カバレッジログを参照してください。"
                )
        else:
            logger.info("[gate2_checks] hearing_dsl_gaps: 対象ファイル（converter/solver/シナリオ）が揃わずスキップ")
    except Exception as e:
        logger.warning(f"[gate2_checks] hearing_dsl_gapsチェックをスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-08-02追加: Gate2構造適合チェック（Phase3、advisory・非ブロッキング）。
    # lookup_family_reference()をdomain_name/hearing_textsだけで内部再利用するため、
    # このrun_gate2_checks()自体のシグネチャは変更不要（呼び出し元app.pyの2箇所
    # （初回登録・デバッグエージェント再検証ループ）双方に自動的に適用される）。
    tech_conformance_warnings: list[str] = []
    _emit_stage("verifying_tech_conformance")
    try:
        _tech_artifacts = extract_domain_artifacts_from_diffs(diffs, snake)
        tech_conformance_warnings = check_family_technology_conformance(
            domain_name, hearing_texts, _tech_artifacts.get("solver_code")
        )
    except Exception as e:
        logger.warning(f"[gate2_checks] Gate2構造適合チェックをスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-08-11追加: i18nメッセージカバレッジチェック（advisory・非ブロッキング）。
    # 詳細は _check_i18n_message_coverage のdocstring・直前のコメント参照。
    # run_gate2_checks()自体がnew_domainフル生成ルート（app.py `_run_domain_job`の
    # generate_domain_files()直後）と、その確認画面からの再検証ループでしか
    # 呼ばれていない（base_domain/extension_gapsルートは早期returnで別処理・
    # パターン3のStage2-lite V2は本関数を経由しない）ため、このチェックも
    # 自動的にnew_domain登録のみに限定される。
    i18n_coverage_warnings: list[str] = []
    try:
        _i18n_artifacts = extract_domain_artifacts_from_diffs(diffs, snake)
        i18n_coverage_warnings = _check_i18n_message_coverage(
            _i18n_artifacts.get("solver_code"), _i18n_artifacts.get("ui_converter_code"), snake
        )
    except Exception as e:
        logger.warning(f"[gate2_checks] i18nカバレッジチェックをスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-08-03変更: 「solver.pyが例外でクラッシュした」場合と「モデルは正常に
    # solve()まで完走したがfeasible/infeasibleの期待値と食い違った」場合を、
    # ここで別リストに分けておく。従来は両方とも dynamic_warnings に混ぜて
    # humanize_technical_findings() に一括で渡していたため、LLMによる言い換えの
    # 過程で「実装のバグでクラッシュした」という事実が「実行可能と判定される
    # べきでしたが実行不可と判定されました」のような、あたかも制約矛盾（真の
    # infeasible）であるかのような文面に薄まってしまい、Koshoshiが実機で毎回
    # シナリオを手動実行して初めて真因（例外）に気付く、という手戻りが複数回
    # 発生した（WorkerLoadBalancer・VesselDeckLoader較正実験で確認）。
    # exception由来の指摘だけ別の言い換え・別の接頭辞で扱うことで、LLMの
    # 言い換え精度に依存せず、UI上のラベルだけで「バグの疑い」と「制約矛盾の
    # 疑い」を機械的に区別できるようにする。
    dynamic_warnings: list[str] = []
    dynamic_exception_warnings: list[str] = []
    written_paths: list[str] = []
    _emit_stage("verifying_dynamic_check")
    try:
        written_paths = write_files_for_dynamic_check(diffs, snake)
        if written_paths:
            gate2_report = run_gate2_dynamic_verification(snake)
            dynamic_warnings.extend(gate2_report.get("warnings", []))
            for suffix, entry in gate2_report.get("scenarios", {}).items():
                if entry.get("status") == "exception":
                    tb_text   = entry.get("traceback") or ""
                    last_line = tb_text.strip().splitlines()[-1] if tb_text.strip() else "不明なエラー"
                    dynamic_exception_warnings.append(
                        f"{suffix}シナリオの実行中に例外が発生しました: {last_line}"
                    )
        else:
            logger.info("[gate2_checks] 動的検証: 対象ファイルなし（4DSL非準拠の可能性）— スキップ")
    except Exception as e:
        logger.warning(f"[gate2_checks] 動的検証をスキップ（実行エラー）: {e}", exc_info=True)

    # 2026-07-17再設計: 以前はstatic（repair_notes/sanitizer_warnings）と
    # dynamic（実際にsolve()した結果）を1つのquestionsリストにまとめて同じ重みで
    # 出していた。しかしstaticなfield-check（converter⇔solverのキー突き合わせ）は
    # AST上のキー抽出だけの粗い判定で、solver内部の中間オブジェクト（例: candidate
    # dictの計算済みキー）を「converterが出力すべきキー」と誤認識する既知の弱点が
    # あり、実機で「エージェントが正しく誤検知と説明しても、静的チェックは学習せず
    # 毎回同じ指摘を出し続けて収束しない」事故が起きた。加えて、業務ユーザーに
    # 「これは誤検知か本物のバグか」を判断させるのはそもそも無理がある。
    #
    # そこで、実際にCP Optimizerでsolve()した結果（dynamic）と、ヒアリング必須節の
    # 未実装（required coverage gap）だけを「人間の判断が必須」の指摘として扱い、
    # static findingsとoptionalなcoverage要約は「advisory（参考情報）」として区別
    # して返す。呼び出し元は、advisoryしか残っていない場合は人間に判断を求めずに
    # 進めてよい（ただし記録には残す）。
    _emit_stage("verifying_humanize")
    try:
        static_humanized = humanize_technical_findings(repair_notes + sanitizer_warnings, domain_name)
    except Exception as e:
        logger.warning(f"[gate2_checks] 静的指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        static_humanized = repair_notes + sanitizer_warnings

    try:
        dynamic_humanized = humanize_technical_findings(dynamic_warnings, domain_name, category="dynamic")
    except Exception as e:
        logger.warning(f"[gate2_checks] 動的検証指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        dynamic_humanized = dynamic_warnings

    # 2026-08-03追加: exception由来の指摘は「実装バグの疑い」であることが
    # 分かる文面に限定して言い換える（上記dynamic_humanizedと違うプロンプト・
    # 違う接頭辞で扱い、真のinfeasible判定と混同されないようにする）。
    try:
        dynamic_exception_humanized = _humanize_exception_findings(dynamic_exception_warnings, domain_name)
    except Exception as e:
        logger.warning(f"[gate2_checks] 例外指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        dynamic_exception_humanized = dynamic_exception_warnings

    # 2026-07-19追加: unused_in_solver（converterが出力しているがsolverが一度も
    # 参照しないキー）は、他の静的field-check（missing_in_converterの自己参照
    # 誤検知等）と違って既知の誤検知パターンが無い。MeetingRoomの
    # metadata.instance_name/noteパススルー漏れが、advisory扱いだったために
    # 確認画面から自動的に除外され本物のバグのまま登録されてしまった実機事故を
    # 受け、blocking側（AIエージェントによる自動修正の対象、直せなければ
    # 取りやめ／このまま登録するの判断対象）に分離する。
    try:
        unused_in_solver_humanized = humanize_technical_findings(unused_in_solver_warnings, domain_name, category="unused_in_solver")
    except Exception as e:
        logger.warning(f"[gate2_checks] unused_in_solver指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        unused_in_solver_humanized = unused_in_solver_warnings

    # 2026-07-22追加: Big-M近似検出（Koshoshi合意により例外的にblocking側へ）。
    # unused_in_solverと同じ理由（既知の誤検知パターンを持たず、過去に実際の
    # 業務問題を起こしたパターンであるため）で、advisory（自動で通す）ではなく
    # blocking（人間の確認必須）に分離する。他の静的field-check系（advisory側）
    # とは異なる扱いである点に注意。
    try:
        big_m_humanized = humanize_technical_findings(big_m_warnings, domain_name, category="big_m")
    except Exception as e:
        logger.warning(f"[gate2_checks] big_m指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        big_m_humanized = big_m_warnings

    # 2026-08-10追加: optional interval_var のabsent値誤用検出（ProductionLineSequencing
    # 登録時に実機発生。start_of(var, 0) == s のような形がexactly-1 present制約と矛盾し
    # 必ずinfeasibleになる既知の実害パターン）。big_m_warningsと同じ理由でblocking側へ
    # （Koshoshi合意、2026-08-10）。
    try:
        absent_value_humanized = humanize_technical_findings(absent_value_warnings, domain_name, category="absent_value")
    except Exception as e:
        logger.warning(f"[gate2_checks] absent_value指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        absent_value_humanized = absent_value_warnings

    # 2026-09-18追加: mdl.pulse()へのfloat height直渡し検出（EnergyCostAwareScheduler
    # 登録時に実機発生、2026-08-09）。absent_value_warningsと同じ理由でblocking側へ
    # （Koshoshi合意、2026-09-18）。
    try:
        pulse_float_humanized = humanize_technical_findings(pulse_float_warnings, domain_name, category="pulse_float")
    except Exception as e:
        logger.warning(f"[gate2_checks] pulse_float指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        pulse_float_humanized = pulse_float_warnings

    # 2026-09-18追加: if_thenの第2引数への比較式直渡し検出（禁止パターン4）。
    # 従来はadvisory（_CPO_WARN_PATTERNS）扱いだったが、EnergyCostAwareScheduler
    # で実際に見過ごされた事故（2026-08-30発覚）を受けblocking側へ昇格
    # （Koshoshi合意、2026-09-18）。
    try:
        containment_humanized = humanize_technical_findings(containment_warnings, domain_name, category="containment")
    except Exception as e:
        logger.warning(f"[gate2_checks] containment指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        containment_humanized = containment_warnings

    # 2026-07-26追加: missing_in_dsl_for_solver（DSL⇔solver直接突き合わせ、ネストキー
    # 対応）も、unused_in_solver/big_mと同じ理由（既知の誤検知パターンが薄く、
    # work_limitsのようなパススルー構造で実際にハード制約が無効化された実機不具合の
    # 再発防止が目的）でblocking側に分離する。
    try:
        missing_in_dsl_for_solver_humanized = humanize_technical_findings(
            missing_in_dsl_for_solver_warnings, domain_name, category="missing_in_dsl_for_solver"
        )
    except Exception as e:
        logger.warning(f"[gate2_checks] missing_in_dsl_for_solver指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        missing_in_dsl_for_solver_humanized = missing_in_dsl_for_solver_warnings

    # 2026-09-06追加: required_gapも他のblockingカテゴリ同様、事実を薄めない
    # 限定的な言い換え（_humanize_required_gap_findings）を通す。
    try:
        required_gap_humanized = _humanize_required_gap_findings(required_gap_warnings, domain_name)
    except Exception as e:
        logger.warning(f"[gate2_checks] required_gap指摘の言い換えをスキップ（実行エラー）: {e}", exc_info=True)
        required_gap_humanized = required_gap_warnings

    # 2026-08-28追記（Koshoshi合意）: 接頭辞を生の技術用語（Big-M近似／optional
    # interval absent値／ネストキー等）からプレーンな日本語ラベルに変更。
    # ただし先頭2つ（実装エラーの疑い／実行検証）は Backend/app.py の
    # _DYNAMIC_STRUCTURAL_PREFIXES と Frontend/.../RegisterModal.tsx の
    # DYNAMIC_STRUCTURAL_PREFIXES が同じ文字列を前方一致で参照しているため、
    # 3箇所を必ず同時に変更すること（「Gate2動的検証は非交渉」の判定に使われる）。
    blocking_questions = (
        [f"（プログラムのエラーで停止・要修正）{w}" for w in dynamic_exception_humanized]
        + [f"（実際に解いてみた結果が想定と違いました）{w}" for w in dynamic_humanized]
        + [f"（ヒアリング内容が未反映）{w}" for w in required_gap_humanized]
        + [f"（入力項目の反映漏れの疑い）{w}" for w in unused_in_solver_humanized]
        + [f"（数値のざっくり近似に関する指摘）{w}" for w in big_m_humanized]
        + [f"（特殊な条件の扱いに矛盾の疑い）{w}" for w in absent_value_humanized]
        + [f"（数量の型に関する指摘）{w}" for w in pulse_float_humanized]
        + [f"（条件分岐の書き方に関する指摘）{w}" for w in containment_humanized]
        + [f"（設定項目の反映漏れの疑い）{w}" for w in missing_in_dsl_for_solver_humanized]
        + [f"（解の自己検証が未実装の疑い）{w}" for w in mip_self_check_warnings]
    )

    # 2026-09-19追加（Koshoshi合意）: debug_agentに渡すため、humanizeを一切
    # 経由しない「生の」blocking_questionsを別途組み立てる。上のblocking_questions
    # （humanize済み）は業務担当者向け確認画面の表示専用であり、
    # humanize_technical_findings()/_humanize_exception_findings()は意図的に
    # presence_of/interval_var等の識別子・コード片を削る設計（画面を見る人が
    # プログラムを読めない前提のため）。ところがrun_debug_agent()の
    # questions引数にはこれまでこのhumanize済みテキストがそのまま渡っており、
    # 例えば_check_optional_interval_absent_value()が既に警告文中に書いている
    # 「mdl.if_then(mdl.presence_of(var), mdl.start_of(var) == <値>)」という
    # そのまま使える正解のコードパターンを、debug_agentが一度も受け取れない
    # 状態になっていた（実機2回連続で同一バグを8ターン修正できず失敗、
    # 2026-09-19）。blocking_questionsと完全に同じ接頭辞・同じ並び順を保つ
    # ことで、呼び出し側の_DYNAMIC_STRUCTURAL_PREFIXESによる絞り込みロジックは
    # 一切変更せずに、debug_agent行き専用の内容だけを差し替えられるようにする。
    blocking_questions_raw = (
        [f"（プログラムのエラーで停止・要修正）{w}" for w in dynamic_exception_warnings]
        + [f"（実際に解いてみた結果が想定と違いました）{w}" for w in dynamic_warnings]
        + [f"（ヒアリング内容が未反映）{w}" for w in required_gap_warnings]
        + [f"（入力項目の反映漏れの疑い）{w}" for w in unused_in_solver_warnings]
        + [f"（数値のざっくり近似に関する指摘）{w}" for w in big_m_warnings]
        + [f"（特殊な条件の扱いに矛盾の疑い）{w}" for w in absent_value_warnings]
        + [f"（数量の型に関する指摘）{w}" for w in pulse_float_warnings]
        + [f"（条件分岐の書き方に関する指摘）{w}" for w in containment_warnings]
        + [f"（設定項目の反映漏れの疑い）{w}" for w in missing_in_dsl_for_solver_warnings]
        + [f"（解の自己検証が未実装の疑い）{w}" for w in mip_self_check_warnings]
    )
    advisory_questions = (
        [f"（参考情報）{w}" for w in static_humanized]
        + [f"（ヒアリング内容が未反映・任意項目）{w}" for w in optional_gap_summary]
        + tech_conformance_warnings
        + i18n_coverage_warnings
    )
    questions = blocking_questions + advisory_questions

    logger.info(
        f"[gate2_checks] {domain_name}: repair_notes={len(repair_notes)}件, "
        f"sanitizer_warnings={len(sanitizer_warnings)}件, unused_in_solver={len(unused_in_solver_warnings)}件, "
        f"big_m_warnings={len(big_m_warnings)}件, "
        f"absent_value_warnings={len(absent_value_warnings)}件, "
        f"pulse_float_warnings={len(pulse_float_warnings)}件, "
        f"containment_warnings={len(containment_warnings)}件, "
        f"missing_in_dsl_for_solver={len(missing_in_dsl_for_solver_warnings)}件, "
        f"required_gap={len(required_gap_warnings)}件, "
        f"optional_gap_summary={len(optional_gap_summary)}件, dynamic_warnings={len(dynamic_warnings)}件, "
        f"dynamic_exception_warnings={len(dynamic_exception_warnings)}件, "
        f"tech_conformance={len(tech_conformance_warnings)}件, "
        f"i18n_coverage={len(i18n_coverage_warnings)}件, "
        f"mip_self_check={len(mip_self_check_warnings)}件 "
        f"→ blocking={len(blocking_questions)}件, advisory={len(advisory_questions)}件"
    )
    return {
        "questions": questions,
        "blocking_questions": blocking_questions,
        "blocking_questions_raw": blocking_questions_raw,
        "advisory_questions": advisory_questions,
        "written_paths": written_paths,
        "coverage": coverage,
    }
