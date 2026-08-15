"""
ce_limit_lns.py — 層A: CPLEX Community Edition（評価版）モデルサイズ上限の
                  汎用検知 + 逐次バッチ分割ループ
===============================================================================

【背景】
CPLEX/CP Optimizerの評価版（Community Edition）にはモデルサイズ上限があり、
超過すると例外が発生する。この上限は変数・制約を宣言した時点ではなく、
**エンジンを実際に呼び出す時点（`.solve()`）でのみ**チェックされる（docplex.mpの
実トレースバックで確認済み: `Model.solve()` → `cplex_engine.solve()` →
`fatal_ce_limits()`。変数・制約の追加自体はPure Python内で完結し上限の
影響を受けない。CP Optimizerの"Problem size limit exceeded"も同様に
`solve()`呼び出し時に発生する）。

このため、モデルを構築してから内部の変数を固定して縮小する、という方式は
成立しない。既存の`RouteDecomposer`（TruckDispatcher）/`HorizonDecomposer`/
`SpatialDecomposer`（decomposer.py。旧`StaffDecomposer`は呼び出し元
EventStaffing削除に伴い2026-07-27に削除済み）が例外なく採用している
「モデル構築前に入力データ側のリストを絞り込む」方式を踏襲する。

【対象】
CP Optimizer（`docplex.cp`）ドメイン専用。CPLEX MIP（`docplex.mp`）ドメインは
モデル全体を分割せずHiGHSへエンジンスイッチする方式（`ce_limit_mip_fallback.py`）
を使うため、本モジュールの対象外。

【層の境界】
- 層A（共通コア）: `is_ce_limit_exceeded()` / `run_sequential_batch()` /
  `reduce_quota_fields()` / `CeLimitExceededError` / `run_solve_with_ce_limit_fallback()`
  （このファイル全体）。
- 層B（ドメインアダプタ）: 各ドメインの`{domain}_batch_decomposer.py`が提供する3点。
    1. `PRIMARY_ENTITY_KEY`: Solver Input DSLのどのリストフィールドを
       分割軸にするか（例: NurseShiftWeeklyCapは"staff"）。
    2. `build_subset_input(solver_input, entity_ids, prior_results) -> dict`:
       分割軸のリストをentity_idsに絞り込んだ縮小Solver Input DSLを返す。
       タスク単位の集計値（required_count/min_chiefs等）の残数は
       `reduce_quota_fields()`（層A）への宣言（QUOTA_FIELDS）で計算する。
    3. `merge_results(results, original_solver_input) -> dict`:
       複数バッチのSolver Output DSLを1つに統合する。全体集約値は
       統合後のデータから再計算すること（バッチ単位の値をそのまま使わない、
       `HorizonDecomposer.merge()`のmax()再計算と同じ考え方）。通常はそのドメイン
       自身が既に持つ集計関数（build_task_summary等）を呼び直すだけでよい。

【2026-07-26追記: solve()側の「retry-depth付き再試行ラッパー」も層Aへ一般化】
NurseShiftWeeklyCapSolver.solve()に最初に実装した「CE上限を検知したら
バッチサイズを段階的に縮めながら再試行し、一定回数で諦める」というロジックは、
当初はそのドメインのsolve()メソッド内に直接書かれていた（Koshoshiの指摘:
「これをドメイン固有のまま続けると、拡張ドメインを作るたびに毎回コピーする
羽目になり、ドメイン固有実装がずっと続くラインができてしまう」）。
実際に中身を精査すると、この部分にドメイン固有の判断は一切無く
（retry_depthのカウント・バッチサイズの半減・MAX_RETRY_DEPTHでの打ち切り・
`ce_limit_unresolvable`イシューの組み立ては全ドメイン共通）、ドメインごとに
違うのは「どの例外クラスを投げ直すか」「自分自身のクラスをどう再帰的に
インスタンス化するか」「problem_class文字列」だけだった。そこで例外クラスも
`CeLimitExceededError`として層Aに共通化し、ラッパー全体を
`run_solve_with_ce_limit_fallback()`として層Aへ切り出した。各ドメインの
solve()は、この関数を数行呼ぶだけでCE上限フォールバックが「無料で」手に入る
（`ce_limit_mip_fallback.py`のMIP版が持つ「ドメイン側アダプタ不要」という
性質に、CP版も一歩近づいた形）。

参照: docs/DESIGN_2026-07-26_generic_ce_limit_fallback.md
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Set

logger = logging.getLogger(__name__)

__all__ = [
    "is_ce_limit_exceeded", "run_sequential_batch", "reduce_quota_fields",
    "CeLimitExceededError", "run_solve_with_ce_limit_fallback",
]


class CeLimitExceededError(Exception):
    """
    CPLEX Community Edition（評価版）のモデルサイズ上限を検知した時に、
    ドメインの`_build_and_solve()`が投げ直すための共通例外クラス。

    2026-07-26追記: 以前は各ドメインが`_NurseShiftCeLimitError`のように
    自前で専用の例外クラスを定義していたが、中身は「CE上限を検知した」という
    シグナル以上の意味を持たず、ドメインごとに定義し直す必要が無いことが
    分かったため、層Aで共通化した。ドメイン側は
    `raise CeLimitExceededError(str(e)) from e`と書くだけでよい。
    """
    pass


# -----------------------------------------------------------------------------
# 上限検知（CP Optimizer / CPLEX MIP 両対応）
# -----------------------------------------------------------------------------

# CP Optimizer（docplex.cp）: 探索空間 2^1000 上限。既存の
# truck_dispatcher_solver.py の _CpoSizeLimitError / decomposer.py の
# _is_size_limit と同じメッセージパターン。
_CP_OPTIMIZER_PATTERNS = (
    "problem size limit exceeded",
    "size limit",
)

# CPLEX（docplex.mp）: 1000変数 または 1000制約 上限。CPLEX Error 1016。
# 実例: "CPLEX Error 1016: Community Edition. Problem size limits exceeded."
# docplex.mp独自の例外クラス名 DOcplexLimitsExceeded も検知対象に含める
# （クラス名で判定できるようtype名も見る。isinstanceで直接importに依存しない
# ことで、docplex未インストール環境でも安全にimportできるようにする）。
_CPLEX_MIP_PATTERNS = (
    "cplex error 1016",
    "1016",
    "promotional edition",
    "problem size limits exceeded",
    "docplexlimitsexceeded",
)


def is_ce_limit_exceeded(exc: BaseException) -> bool:
    """
    CP Optimizer / CPLEX MIP いずれかのCommunity Edition（評価版）モデルサイズ
    上限例外かどうかを判定する。既存の`_is_size_limit()`（app.py/decomposer.py内の
    ローカル定義）と同じ用途だが、CPLEX MIPのシグネチャも追加で見る点が異なる。
    """
    msg = str(exc).lower()
    type_name = type(exc).__name__.lower()
    haystack = f"{msg} {type_name}"
    return any(p in haystack for p in _CP_OPTIMIZER_PATTERNS) or any(
        p in haystack for p in _CPLEX_MIP_PATTERNS
    )


# -----------------------------------------------------------------------------
# 逐次バッチ分割ループ（層A: CPドメイン共通コア）
# -----------------------------------------------------------------------------

def run_sequential_batch(
    solver_input: Dict[str, Any],
    primary_entity_key: str,
    batch_size: int,
    build_subset_input: Callable[[Dict[str, Any], Set[Any], List[Dict]], Dict[str, Any]],
    solve_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    merge_results: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    `primary_entity_key`のリストをbatch_size件ずつに分割し、各バッチについて
    `build_subset_input`で縮小Solver Input DSLを作ってから`solve_fn`（＝ドメインの
    通常のsolve()相当）を呼び、`merge_results`で統合する。

    `HorizonDecomposer.solve_sequentially()` / `StaffDecomposer.solve_sequentially()`
    と同じ「一巡バッチ分割+マージ」方式（品質改善のRuin-and-Recreate反復は行わない、
    DESIGN_2026-07-26で明示的に見送った範囲）。

    呼び出し元（ドメインのsolve()）が、通常のフルモデル解決でCE上限例外を
    捕捉した場合にのみこの関数を呼ぶこと（閾値の事前判定ではなく実際の例外検知で
    切り替える、TruckDispatcher/YardPlanningと同じ既存方針を踏襲）。

    Args:
        solver_input:        元のSolver Input DSL全体。
        primary_entity_key:  分割軸とするリストフィールド名（層Bが宣言）。
        batch_size:          1バッチあたりのエンティティ数上限。
        build_subset_input:  (solver_input, entity_ids, これまでのバッチ結果) -> 縮小DSL。
        solve_fn:            縮小DSLを受け取りSolver Output DSLを返す関数
                              （通常は対象ドメインの`{Domain}Solver(sub_input).solve()`）。
        merge_results:       (全バッチのSolver Output DSLのリスト, 元のsolver_input) -> 統合結果。

    Returns:
        統合後のSolver Output DSL。
    """
    entities = solver_input.get(primary_entity_key, [])
    if not entities:
        logger.warning(
            f"[ce_limit_lns] primary_entity_key={primary_entity_key!r} が空です。"
            f"分割せずそのままsolve_fnを呼びます。"
        )
        return solve_fn(solver_input)

    if len(entities) <= batch_size:
        logger.info(
            f"[ce_limit_lns] {primary_entity_key}={len(entities)} <= batch_size={batch_size} "
            f"のため分割不要（呼び出し元の判断ミスの可能性、通常はCE上限例外後にのみ呼ばれる想定）"
        )
        return solve_fn(solver_input)

    batches: List[List[Dict]] = [
        entities[i : i + batch_size] for i in range(0, len(entities), batch_size)
    ]
    logger.info(
        f"[ce_limit_lns] 逐次バッチ分割: {primary_entity_key}={len(entities)}件 → "
        f"{len(batches)}バッチ（batch_size={batch_size}）"
    )

    results: List[Dict[str, Any]] = []
    for i, batch_entities in enumerate(batches):
        entity_ids = {e["id"] for e in batch_entities}
        sub_input = build_subset_input(solver_input, entity_ids, results)

        try:
            result = solve_fn(sub_input)
        except Exception as exc:
            if is_ce_limit_exceeded(exc):
                # バッチをさらに分割しても解決しない設定ミスの可能性が高いため、
                # ここでの再帰分割は行わずエラーとして扱う（無限再帰・無限に細かい
                # バッチ化を防ぐ）。呼び出し元にbatch_sizeの見直しを促す。
                raise RuntimeError(
                    f"[ce_limit_lns] バッチ{i}（{len(batch_entities)}件）でもCE上限に抵触しました。"
                    f"batch_size={batch_size}をさらに下げて再試行してください。"
                ) from exc
            logger.error(f"[ce_limit_lns] バッチ{i}でエラー: {exc}", exc_info=True)
            result = {"status": "error", "feasible": False, "solutions": [], "issues": []}

        results.append(result)
        logger.info(
            f"[ce_limit_lns] バッチ{i}/{len(batches)-1} 完了: "
            f"status={result.get('status')}, feasible={result.get('feasible')}"
        )

    return merge_results(results, solver_input)


# -----------------------------------------------------------------------------
# 「残数持ち越し」の汎用ヘルパー（層A: CPドメイン共通コア）
# -----------------------------------------------------------------------------
#
# 2026-07-26追加（アダプタの宣言型への一般化）。
#
# 【経緯】
# NurseShiftWeeklyCap用に最初に書いたbuild_subset_input()は、「タスクの
# required_count/min_chiefsから、これまでのバッチで割り当て済みの数を引いた
# 残数を計算する」処理を、そのドメイン専用のPythonコードとして直接書いていた。
# しかし実際の中身を見ると、この処理は「① 集計対象のフィールド名（例:
# required_count）」「② 集計時の絞り込み条件（例: grade==CHIEFの候補だけ数える）」
# という**宣言（データ）**さえあれば、ループ自体はどのドメインでも同じ形になる
# （StaffDecomposer._compute_batch_required_targetsが同じ形のロジックを
# EventStaffing用に独自実装していたのも同じパターン）。
#
# よって、ループ本体をここに1つだけ書き、ドメイン側は「どのフィールドを
# 何をキーに・どういう条件で集計するか」という宣言（quota_fields）だけを渡せば
# 済むようにする。これにより、新しいドメインでこの機能が必要になった時、
# 層Bに書くコードの量を大きく減らせる（丸ごと関数を書き直す必要がなくなる）。
#
# 対象外にしたもの: 「割り当て結果からissue/metricsを再計算する」部分
# （merge_results側）は、汎用ループとしてここに切り出すのではなく、各ドメインが
# 元々持っている（持つべき）`build_candidates`/`_build_xxx_contexts`/
# `run_issue_rules`をそのまま呼び直すだけにする、という別の一般化方針を取る
# （nurse_shift_weekly_cap_batch_decomposer.pyのmerge_results参照）。
# これは「ここに書く」ようなロジックではなく「ドメイン自身の既存関数を呼ぶ」
# という話であるため、層Aの共通コードとしては切り出さない。


def reduce_quota_fields(
    entities: List[Dict[str, Any]],
    entity_key_field: str,
    assigned_items: List[Dict[str, Any]],
    assigned_group_field: str,
    quota_fields: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    entities（例: tasks）内の指定フィールド（quota_fieldsで宣言）を、
    assigned_items（これまでのバッチで実際に割り当て済みの明細）で既に
    消費された分だけ差し引いた「残数」に置き換えた新しいリストを返す。

    Args:
        entities:              残数を計算する対象のリスト（例: tasks）。
        entity_key_field:      entities側のグループキー（例: "id"）。
        assigned_items:        割り当て済み明細のリスト（例: assigned_tasks）。
        assigned_group_field:  assigned_items側の対応するキー（例: "task_id"）。
        quota_fields:          宣言のリスト。各要素は以下の形:
            {
                "field":   "required_count",   # entities側のフィールド名（必須）
                "filter":  lambda item: ...,    # assigned_itemsを数える条件（省略時は無条件で1件としてカウント）
                "default": 0,                   # entities側にfieldが無い場合の既定値（省略時0）
            }

    Returns:
        entitiesと同じ長さの新しいリスト（各quota_fieldsのfieldが残数に
        置き換わったコピー。元のリスト・辞書は変更しない）。
    """
    consumed_by_group: Dict[Any, Dict[str, int]] = {}
    for item in assigned_items:
        gid = item.get(assigned_group_field)
        if gid is None:
            continue
        bucket = consumed_by_group.setdefault(gid, {})
        for spec in quota_fields:
            field = spec["field"]
            filt = spec.get("filter")
            if filt is None or filt(item):
                bucket[field] = bucket.get(field, 0) + 1

    reduced: List[Dict[str, Any]] = []
    for entity in entities:
        eid = entity.get(entity_key_field)
        new_entity = copy.deepcopy(entity)
        bucket = consumed_by_group.get(eid, {})
        for spec in quota_fields:
            field = spec["field"]
            original = entity.get(field, spec.get("default", 0))
            consumed = bucket.get(field, 0)
            new_entity[field] = max(0, original - consumed)
        reduced.append(new_entity)
    return reduced


# -----------------------------------------------------------------------------
# solve()ラッパーの汎用化（層A: CPドメイン共通コア）
# -----------------------------------------------------------------------------
#
# 2026-07-26追加。経緯は本ファイル冒頭のdocstring「2026-07-26追記」参照。
# NurseShiftWeeklyCapSolver.solve()に直接書かれていた「CE上限を検知したら
# バッチサイズを段階的に縮めながら再試行し、一定回数で諦める」ロジックを
# ここに1つだけ実装し、各ドメインのsolve()はこの関数を呼ぶだけにする。

DEFAULT_CE_LIMIT_BATCH_SIZE = 30
DEFAULT_CE_LIMIT_MAX_RETRY_DEPTH = 6


def run_solve_with_ce_limit_fallback(
    solver_class: type,
    solver_input: Dict[str, Any],
    primary_entity_key: str,
    build_subset_input: Callable[[Dict[str, Any], Set[Any], List[Dict]], Dict[str, Any]],
    merge_results: Callable[[List[Dict[str, Any]], Dict[str, Any]], Dict[str, Any]],
    problem_class: str,
    solver_version: str,
    default_batch_size: int = DEFAULT_CE_LIMIT_BATCH_SIZE,
    max_retry_depth: int = DEFAULT_CE_LIMIT_MAX_RETRY_DEPTH,
) -> Dict[str, Any]:
    """
    ドメインの`solve()`が`CeLimitExceededError`を捕捉した時に呼ぶだけでよい、
    「retry-depth付き段階的バッチ縮小フォールバック」の全体を層Aへ切り出したもの。

    ドメイン側の使い方（solve()内）:
        try:
            solution_data, candidates = self._build_and_solve(...)
        except CeLimitExceededError:
            from solvers.{domain}_batch_decomposer import (
                PRIMARY_ENTITY_KEY, build_subset_input, merge_results,
            )
            return run_solve_with_ce_limit_fallback(
                solver_class=type(self),
                solver_input=self.dsl,
                primary_entity_key=PRIMARY_ENTITY_KEY,
                build_subset_input=build_subset_input,
                merge_results=merge_results,
                problem_class="{ProblemClass}",
                solver_version="{solver}_v1.0",
            )

    Args:
        solver_class:        再帰的に子バッチを解く際にインスタンス化するクラス
                              （通常は`type(self)`。`__init__(self, solver_input)`という
                              統一シグネチャを前提とする）。
        solver_input:         元のSolver Input DSL全体（`self.dsl`）。
        primary_entity_key:   分割軸とするリストフィールド名（層Bが宣言するPRIMARY_ENTITY_KEY）。
        build_subset_input:   層Bが提供する関数（run_sequential_batchへそのまま渡す）。
        merge_results:        層Bが提供する関数（run_sequential_batchへそのまま渡す）。
        problem_class:        エラーメッセージ・metadata.problem_classに使うドメイン名。
        solver_version:       結果dictの_solver_versionに使うバージョン文字列。
        default_batch_size:   config.ce_limit_batch_sizeが未指定の場合の既定バッチサイズ。
        max_retry_depth:      これ以上再試行しても解決不能とみなして打ち切る回数。

    Returns:
        Solver Output DSL（統合済み、またはce_limit_unresolvableのfeasible=False結果）。
    """
    config = dict(solver_input.get("config", {}))
    entities = solver_input.get(primary_entity_key, [])
    retry_depth = int(config.get("_ce_limit_retry_depth", 0))

    if retry_depth >= max_retry_depth or len(entities) <= 1:
        logger.error(
            f"[ce_limit_lns] {problem_class}: グループを{retry_depth}回小さくしても"
            f"CPLEXの無料版の上限を超え続けています（現在の{primary_entity_key}数="
            f"{len(entities)}件）。グループ分けでは解決できないため処理を打ち切ります。"
        )
        return {
            "status":   "ok",
            "feasible": False,
            "metadata": {"problem_class": problem_class},
            "solutions": [],
            "issues": [{
                "id":       "ce_limit_unresolvable",
                "severity": "CRITICAL",
                "title":    "CPLEXの無料版で扱える件数を超えています",
                "message":  (
                    "グループに分けて計算しましたが、それでもCPLEXの無料版の上限を"
                    "超えてしまい解決できませんでした。件数自体が多い可能性があります。"
                    "正規ライセンスのご利用をご検討ください。"
                ),
                "relatedContainerIds": [],
            }],
            "_solver_version": solver_version,
        }

    configured_default = int(config.get("ce_limit_batch_size", default_batch_size))
    if retry_depth == 0:
        # 最初の1回目は設定値（既定30件）をそのまま使う。
        batch_size = min(configured_default, max(1, len(entities) - 1))
    else:
        # 2回目以降は、同じ件数でまた失敗しないよう必ず半分以下にする
        # （固定サイズのまま再試行し続けるとRecursionErrorに至る不具合の再発防止、
        # 2026-07-26に実機テストで発見・修正済み）。
        batch_size = max(1, len(entities) // 2)

    logger.warning(
        f"[ce_limit_lns] {problem_class}: CPLEXの無料版の上限を検知。"
        f"{primary_entity_key}をグループ分けして順番に解く処理に切り替えます"
        f"（{retry_depth + 1}回目の試行、batch_size={batch_size}）。"
    )

    def _solve_batch(sub_input: Dict[str, Any]) -> Dict[str, Any]:
        sub_config = dict(sub_input.get("config", {}))
        sub_config["_ce_limit_retry_depth"] = retry_depth + 1
        sub_config["ce_limit_batch_size"] = configured_default
        return solver_class({**sub_input, "config": sub_config}).solve()

    return run_sequential_batch(
        solver_input=solver_input,
        primary_entity_key=primary_entity_key,
        batch_size=batch_size,
        build_subset_input=build_subset_input,
        solve_fn=_solve_batch,
        merge_results=merge_results,
    )
