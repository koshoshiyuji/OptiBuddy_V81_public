"""
objective_terms.py — 層B: 目的関数項ビルダーカタログ
=======================================================

【設計方針】
- 各ビルダーは (mdl, context: Dict) -> CpoExpr のシグネチャで統一。
- context に必要なデータを辞書で渡す（ドメイン依存の引数をビルダー内で取得）。
- OBJECTIVE_TERM_BUILDERS にデコレータで自動登録。
- build_objective_expr() がレシピ宣言 + 重みから目的式を合成する（weighted_sumモード）。
- build_lexicographic_objective_exprs() が優先順位付きの目的式リストを合成する
  （lexicographicモード。mdl.minimize_static_lex() に渡す）。

【目的モード（2026-07-22追加）】
  OBJECTIVE_RECIPES[domain]["mode"] は "weighted_sum"（既定・省略可）または
  "lexicographic" のいずれか。
    - "weighted_sum": 従来通り。terms + default_weights を1本のCpoExprに合成し、
      mdl.minimize(expr) に渡す。build_objective_expr() を使う。
    - "lexicographic": priority_terms（優先順位の高い順のリスト。各要素は
      単一の項名、または同ティアで結ぶ項名のリスト/タプル）から、
      mdl.minimize_static_lex([...]) に渡すCpoExprのリストを合成する。
      build_lexicographic_objective_exprs() を使う。

  【重要: Big-M代替の禁止】
  lexicographicモードは、かつて「係数の大きさが極端に異なる重み付き単一目的関数
  （Big-M近似）」で疑似的に実現されていたが、これは数値的な許容誤差により
  下位優先度の目的の最適性が事後検証不能になる既知の問題があった
  （docs/DESIGN_2026-07-18_layer_ab_pattern_library.md §4-A、Key Learnings参照）。
  本実装は必ず mdl.minimize_static_lex() というCP Optimizerネイティブの
  辞書式最適化構文を使うこと。単一の mdl.minimize(w1*t1 + w2*t2 + ...) で
  優先順位を近似することは禁止する（domain_generator.py の
  _CPO_WARN_PATTERNS によるBig-M係数比検出の対象でもある）。
  新規にlexicographicモードのレシピを追加した場合は、
  verify_lexicographic_mechanism() による識別テスト（Big-M近似では誤答し、
  true lexicographicでは正答する最小構成）を Gate2動的検証
  （domain_generator.run_gate2_dynamic_verification）で必ず通すこと。

【層の境界】
- 層A (共通コア): このファイル全体
- 層B (レシピカタログ): OBJECTIVE_RECIPES (ドメインごとの宣言)
- 層C (ドメイン固有): なし (ドメイン依存ロジックは context 経由で注入)

【ドメイン別 context キー一覧】

  makespan:
    - task_itvs: Dict[str, interval_var]  # タスクID→CPO interval変数

  order_penalty:
    - task_itvs: Dict[str, interval_var]
    - penalty_pairs: List[Dict]           # DSL objective.penalty_pairs
      各要素: {task_a: str, task_b: str, expected_order: "a_before_b"}

"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

# ---------------------------------------------------------------------------
# 登録テーブル
# ---------------------------------------------------------------------------

OBJECTIVE_TERM_BUILDERS: Dict[str, Callable] = {}


def register_term(name: str):
    """ビルダー関数を OBJECTIVE_TERM_BUILDERS に登録するデコレータ。"""
    def decorator(fn: Callable) -> Callable:
        OBJECTIVE_TERM_BUILDERS[name] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# 項ビルダー定義
# ---------------------------------------------------------------------------

@register_term("makespan")
def build_makespan(mdl, context: Dict[str, Any]):
    """
    全タスクの終了時刻の最大値。

    YardPlanning の主目的: 全クレーン作業が終わる時刻を最小化する。
    """
    task_itvs: Dict = context["task_itvs"]
    return mdl.max([mdl.end_of(iv) for iv in task_itvs.values()])


@register_term("order_penalty")
def build_order_penalty(mdl, context: Dict[str, Any]):
    """
    LOAD/DISCHARGE 順序逆転ペナルティ。

    DSL の objective.penalty_pairs を参照する。
    各ペアは {task_a, task_b, expected_order: "a_before_b"} の形式。
    expected_order が "a_before_b" のとき、
      end_of(task_b) < end_of(task_a) であれば違反 → +1。

    インライン生成(旧実装)は廃止し、DSL変換側が生成した
    penalty_pairs を必ず経由する。
    """
    task_itvs: Dict    = context["task_itvs"]
    penalty_pairs: List = context.get("penalty_pairs", [])

    terms = []
    for pair in penalty_pairs:
        aid = str(pair["task_a"])
        bid = str(pair["task_b"])
        order = pair.get("expected_order", "a_before_b")

        iv_a = task_itvs.get(aid)
        iv_b = task_itvs.get(bid)
        if iv_a is None or iv_b is None:
            continue  # タスクIDが存在しない場合はスキップ

        if order == "a_before_b":
            # b が a より先に終わったら逆転 → ペナルティ
            terms.append(mdl.conditional(
                mdl.end_of(iv_b) < mdl.end_of(iv_a), 1, 0
            ))
        else:
            # "b_before_a": a が b より先に終わったら逆転
            terms.append(mdl.conditional(
                mdl.end_of(iv_a) < mdl.end_of(iv_b), 1, 0
            ))

    return mdl.sum(terms) if terms else mdl.integer_var(0, 0)


# 2026-07-27削除: build_resource_usage_cost() / build_preference_penalty()
# （EventStaffing専用、OBJECTIVE_RECIPES["EventStaffing"]からのみ参照されていた
# 項ビルダー）。EventStaffingドメインは2026-07-11に削除済みで、
# preference_penalty側が前提とする"EventStaffingConstraintApplier"クラスも
# 既に存在しないため、どちらも到達不能なデッドコードだった
# （Koshoshiとの会話で発覚、OBJECTIVE_RECIPESのエントリ・対応テストと合わせて削除）。


@register_term("workload_balance_range")
def build_workload_balance_range(mdl, context: Dict[str, Any]):
    """
    グループ（通常はスタッフ）ごとの負荷合計の「最大値 - 最小値」（range）。

    2026-07-26追加。CSPLib prob069（Balanced Nursing Workload、看護師負荷
    均等化）をNurseShiftWeeklyCapの拡張として実装できるかの実証実験
    （REFERENCE_csplib_cp_mip_table.md参照）で追加した、初の汎用「負荷均等化」
    項ビルダー。

    本来の問題は「負荷の標準偏差の最小化」だが、CP Optimizerは標準偏差
    （2乗和）のような非線形項を直接目的関数に持てないため、range(max-min)を
    均等化の線形代理指標として使う（この近似はスケジューリング分野で一般的
    ─ makespan最小化がmax単体の最小化であるのと同じ発想で、rangeは
    「ばらつきの大きさ」を線形式で表現できる）。真の標準偏差最小化が必要な
    場合は、別途二次目的やpiecewise線形近似の追加実装が必要になる（本ビルダーの
    対象外）。

    context:
      - candidates: List[Dict]              # 各要素はid・group_key・workload_fieldを持つ
      - assignment_itvs: Dict[str, interval_var]
      - group_key: str = "staff_id"          # 負荷を集計する単位（既定: スタッフ）
      - workload_field: str = "workload"     # 候補ごとの負荷量フィールド名

    グループが1つ以下しかない場合（比較対象が無い）は0を返す（安全側）。
    """
    candidates: List[Dict] = context["candidates"]
    assignment_itvs: Dict  = context["assignment_itvs"]
    group_key      = context.get("group_key", "staff_id")
    workload_field = context.get("workload_field", "workload")

    terms_by_group: Dict[Any, List] = {}
    for c in candidates:
        gid = c.get(group_key)
        itv = assignment_itvs.get(c["id"])
        if gid is None or itv is None:
            continue
        weight = c.get(workload_field, 0)
        if not weight:
            continue
        terms_by_group.setdefault(gid, []).append(mdl.presence_of(itv) * weight)

    group_totals = [mdl.sum(terms) for terms in terms_by_group.values() if terms]
    if len(group_totals) < 2:
        return mdl.integer_var(0, 0)
    return mdl.max(group_totals) - mdl.min(group_totals)


# ---------------------------------------------------------------------------
# レシピ宣言 (層B)
# ---------------------------------------------------------------------------

OBJECTIVE_RECIPES: Dict[str, Dict] = {
    "YardPlanning": {
        # profile の w_makespan / w_penalty で上書き可
        "terms": ["makespan", "order_penalty"],
        "default_weights": {
            "makespan":      1,
            "order_penalty": 1,
        },
    },
    # 2026-07-27削除: "EventStaffing"レシピ（EventStaffingドメインは
    # 2026-07-11に削除済みで、このレシピはどこからも参照されない
    # デッドエントリだった。対応するbuild_resource_usage_cost()/
    # build_preference_penalty()も同時に削除済み）。
}


# ---------------------------------------------------------------------------
# 目的式合成エントリポイント
# ---------------------------------------------------------------------------

def build_objective_expr(
    mdl,
    domain: str,
    context: Dict[str, Any],
    weight_overrides: Dict[str, float] | None = None,
):
    """
    レシピに従って目的式を合成して返す。

    Args:
        mdl:              CpoModel インスタンス
        domain:           OBJECTIVE_RECIPESに登録済みのドメイン名（例: "YardPlanning"）
        context:          各項ビルダーが参照するデータ辞書
        weight_overrides: デフォルト重みを上書きする場合に指定
                          例: {"makespan": 2, "order_penalty": 300}

    Returns:
        CpoExpr: mdl.minimize() に渡す目的式

    Raises:
        KeyError: 未登録の domain または term 名が指定された場合
    """
    recipe = OBJECTIVE_RECIPES[domain]
    mode = recipe.get("mode", "weighted_sum")
    if mode == "lexicographic":
        raise ValueError(
            f"{domain} は mode='lexicographic' のレシピです。build_objective_expr() は"
            f"weighted_sumモード専用です。build_lexicographic_objective_exprs() を使い、"
            f"mdl.add(mdl.minimize_static_lex([...])) に渡してください。"
            f"単一の重み付き合算（Big-M近似）でlexicographicを代替することは禁止されています。"
        )

    weights = dict(recipe["default_weights"])
    if weight_overrides:
        weights.update(weight_overrides)

    expr = None
    for term_name in recipe["terms"]:
        builder = OBJECTIVE_TERM_BUILDERS[term_name]
        term_expr = builder(mdl, context)
        w = weights.get(term_name, 1)
        weighted = w * term_expr if w != 1 else term_expr
        expr = weighted if expr is None else expr + weighted

    # terms が空の場合の安全策（通常は起きない）
    return expr if expr is not None else mdl.integer_var(0, 0)


# ---------------------------------------------------------------------------
# lexicographicモード: 優先順位付き目的式リストの合成
# ---------------------------------------------------------------------------

def build_lexicographic_objective_exprs(
    mdl,
    domain: str,
    context: Dict[str, Any],
    weight_overrides: Dict[str, float] | None = None,
) -> List:
    """
    レシピの priority_terms（優先順位の高い順）に従って、
    mdl.minimize_static_lex() に渡すための CpoExpr のリストを合成する。

    レシピ形式:
        OBJECTIVE_RECIPES[domain] = {
            "mode": "lexicographic",
            # 各要素が1つの優先順位ティア。文字列なら単一項、
            # リスト/タプルなら同ティア内で重み付き合算してから1項として扱う。
            "priority_terms": ["term_highest", ["term_mid_a", "term_mid_b"], "term_lowest"],
            "tier_weights": {"term_mid_a": 1, "term_mid_b": 2},  # 省略可（既定1）
        }

    Args:
        mdl:              CpoModel インスタンス
        domain:           OBJECTIVE_RECIPES に登録済みのドメイン名
        context:          各項ビルダーが参照するデータ辞書
        weight_overrides: tier_weights を上書きする場合に指定

    Returns:
        List[CpoExpr]: 優先順位が高い順に並んだ目的式のリスト。
                        そのまま mdl.add(mdl.minimize_static_lex(戻り値)) に渡せる。

    Raises:
        KeyError:   未登録の domain または term 名が指定された場合
        ValueError: レシピの mode が "lexicographic" でない場合、
                    または priority_terms が空の場合
    """
    recipe = OBJECTIVE_RECIPES[domain]
    if recipe.get("mode") != "lexicographic":
        raise ValueError(
            f"{domain} は mode='lexicographic' ではありません（mode={recipe.get('mode', 'weighted_sum')!r}）。"
            f"weighted_sumモードのドメインには build_objective_expr() を使ってください。"
        )

    priority_terms = recipe.get("priority_terms") or []
    if not priority_terms:
        raise ValueError(f"{domain} の priority_terms が空です。lexicographicモードには最低1ティア必要です。")

    weights = dict(recipe.get("tier_weights", {}))
    if weight_overrides:
        weights.update(weight_overrides)

    exprs: List = []
    for tier in priority_terms:
        tier_terms = tier if isinstance(tier, (list, tuple)) else [tier]
        tier_expr = None
        for term_name in tier_terms:
            builder = OBJECTIVE_TERM_BUILDERS[term_name]
            term_expr = builder(mdl, context)
            w = weights.get(term_name, 1)
            weighted = w * term_expr if w != 1 else term_expr
            tier_expr = weighted if tier_expr is None else tier_expr + weighted
        exprs.append(tier_expr)

    return exprs


# ---------------------------------------------------------------------------
# lexicographicメカニズムの識別テスト
# ---------------------------------------------------------------------------
#
# 背景: 過去にlexicographic目的が「係数の大きさが極端に異なる重み付き単一目的
# 関数（Big-M近似）」で偽装されていた（Key Learnings参照）。この関数は
# ドメイン非依存の最小構成で、この環境のdocplex/CP Optimizerが
# mdl.minimize_static_lex() を正しく解けることを実ソルブで確認する。
#
# 用途:
#   1. このファイル自身のユニットテスト（test_objective_terms.py）
#   2. domain_generator.run_gate2_dynamic_verification() から、mode="lexicographic"
#      のドメインが1つでも登録されている場合に呼ばれる、環境レベルの回帰ゲート
#
# シナリオ設計（3変数 a, b, c、各 0..10 の整数）:
#     制約: a + b >= 4   （aを増やせばbの下限は緩む）
#     制約: b + c >= 4   （bを増やせばcの下限は緩む）
#   目的1（最優先）: a を最小化 → 最適値 a=0（b>=4で充足可能）
#   目的2（次点）:   b を最小化 → a=0固定下では b>=4 が必須 → 最適値 b=4
#   目的3（3番目）:  c を最小化 → a=0, b=4固定下で c>=0 で十分 → 最適値 c=0
#   期待する get_objective_values() == (0, 4, 0)。
#
#   このシナリオは「最優先目的を達成した上で次点をさらに絞り込む」という
# lexicographicの本質的な振る舞いを要求するため、minimize_static_lex()が
# 実際に優先順位を守って解いているかどうかを判定できる。
#
#   なお、Big-M近似（minimize(BIG_M**2*a + BIG_M*b + c)）は、BIG_Mを
# 十分大きく取れば線形問題としては同じ解に収束しうる。したがってこの
# 識別テストの主目的は「Big-Mと結果が食い違うこと」の実証ではなく、
# (1) minimize_static_lex がこの環境で (0, 4, 0) を返すことの回帰確認、
# (2) 意図的に小さいBig-M係数（big_m=3）で近似した場合に解がずれうる
#     ことを参考情報として示し、「Big-Mは係数選択に依存する脆い近似で
#     あり、係数を誤ると下位目的の最適性が壊れる」というKey Learningsの
#     教訓を具体的な反例として残すこと、の2点に絞る。
#     ((2)は環境のソルバー実装依存で必ずしも毎回ずれるとは限らないため
#     警告のみとし、Gate2の合否判定は(1)にのみ依拠する。)

_LEX_TEST_TIER_VARS = ("a", "b", "c")
_LEX_TEST_EXPECTED_OBJECTIVES = (0, 4, 0)


def _build_lex_identification_model(mdl_factory, big_m: int | None = None):
    """識別テスト用の最小CPOモデルを構築する。

    Args:
        mdl_factory: CpoModel クラス（呼び出し側から注入。このファイルは
                     docplex に依存しないようにするため）
        big_m: None なら minimize_static_lex（true lexicographic）を使う。
               数値を指定すると、その値を係数とした
               minimize(big_m**2*a + big_m*b + c)（Big-M近似）を使う。

    Returns:
        (mdl, {"a": a_var, "b": b_var, "c": c_var})
    """
    mdl = mdl_factory(name="lex_identification_test")
    a = mdl.integer_var(0, 10, name="a")
    b = mdl.integer_var(0, 10, name="b")
    c = mdl.integer_var(0, 10, name="c")
    mdl.add(a + b >= 4)
    mdl.add(b + c >= 4)

    if big_m is None:
        mdl.add(mdl.minimize_static_lex([a, b, c]))
    else:
        mdl.add(mdl.minimize(big_m * big_m * a + big_m * b + c))

    return mdl, {"a": a, "b": b, "c": c}


def verify_lexicographic_mechanism(mdl_factory=None, solve_kwargs: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    「Big-M近似は係数選択に依存する脆い近似であり、true lexicographic
    （minimize_static_lex）はこの環境で正しく機能する」ことを実ソルブで確認する。

    domain_generator.run_gate2_dynamic_verification() から、mode="lexicographic"
    のドメインが1つでも登録されている場合に呼び出される想定（環境レベルの
    回帰確認であり、特定ドメインのcontextには依存しない）。

    Args:
        mdl_factory: CpoModel クラス。省略時は docplex.cp.model.CpoModel を
                     import して使う（呼び出し側で docplex 未インストール
                     環境向けにモックを注入したい場合に上書き可能）。
        solve_kwargs: mdl.solve() に渡す追加引数（例: {"TimeLimit": 10}）。

    Returns:
        {
          "ok": bool,                     # true lexicographicが期待通り解けたか
          "true_lex_objectives": tuple | None,
          "expected_objectives": (0, 4, 0),
          "big_m_small_diverged": bool | None,  # 小さいBig-M係数で期待通り
                                                 # 真の解と食い違ったか（Big-Mの
                                                 # 脆さを示せたか）
          "warnings": list[str],
          "errors": list[str],
        }
    """
    if mdl_factory is None:
        from docplex.cp.model import CpoModel as mdl_factory  # noqa: N806

    solve_kwargs = solve_kwargs or {}
    report: Dict[str, Any] = {
        "ok": False,
        "true_lex_objectives": None,
        "expected_objectives": _LEX_TEST_EXPECTED_OBJECTIVES,
        "big_m_small_diverged": None,
        "warnings": [],
        "errors": [],
    }

    # (1) true lexicographic: minimize_static_lex がこの環境で正しく解けるか
    try:
        mdl, _vars = _build_lex_identification_model(mdl_factory, big_m=None)
        msol = mdl.solve(**solve_kwargs)
        if msol is None or not msol:
            report["errors"].append(
                "minimize_static_lex識別テストが解を返しませんでした（環境にCP Optimizer"
                "エンジンが無い、またはTimeLimit不足の可能性）。"
            )
            return report
        objectives = tuple(msol.get_objective_values() or ())
        report["true_lex_objectives"] = objectives
        if objectives != _LEX_TEST_EXPECTED_OBJECTIVES:
            report["errors"].append(
                f"minimize_static_lex識別テストの目的値が期待値と不一致: "
                f"expected={_LEX_TEST_EXPECTED_OBJECTIVES}, actual={objectives}。"
                f"このdocplex/CP Optimizer環境でlexicographic最適化が正しく機能していない疑いがあります。"
            )
            return report
    except Exception as e:
        report["errors"].append(f"minimize_static_lex識別テストで例外: {e}")
        return report

    report["ok"] = True

    # (2) Big-M近似が「係数選択に依存する脆い近似」であることの反例確認（参考情報、非ブロッキング）
    try:
        mdl_bm, vars_bm = _build_lex_identification_model(mdl_factory, big_m=3)
        msol_bm = mdl_bm.solve(**solve_kwargs)
        if msol_bm is not None and msol_bm:
            bm_result = (
                msol_bm.get_value(vars_bm["a"]),
                msol_bm.get_value(vars_bm["b"]),
                msol_bm.get_value(vars_bm["c"]),
            )
            diverged = bm_result != _LEX_TEST_EXPECTED_OBJECTIVES
            report["big_m_small_diverged"] = diverged
            if not diverged:
                report["warnings"].append(
                    "参考: big_m=3 でもBig-M近似がtrue lexicographicと同じ解に一致しました"
                    "（この識別シナリオでは差が出ませんでした。Big-M係数依存の脆さを別シナリオで"
                    "示すか、このテストの制約設計を見直すことを検討してください）。"
                )
    except Exception as e:
        report["warnings"].append(f"Big-M比較ソルブで例外（非ブロッキング）: {e}")

    return report


# ---------------------------------------------------------------------------
# GhostKitchen レシピ宣言
# ---------------------------------------------------------------------------
# GhostKitchen は CFLP モデルのため build_objective_expr の項ビルダーを使用しない。
# 目的式は GhostKitchenSolver._build_and_solve() 内で直接構築する。
# 宣言のみ残し、将来の拡張（@register_term("facility_fixed_cost") 等）の差し込み口とする。

OBJECTIVE_RECIPES["GhostKitchen"] = {
    "terms": [],
    "default_weights": {},
}

# ============================================================
# objective_terms.py への追記 — ProjectPlanner
# ============================================================
# GhostKitchenセクションの後に追記

OBJECTIVE_RECIPES["ProjectPlanner"] = {
    # ProjectPlannerSolver は build_objective_expr を使用しない。
    # 目的式は ProjectPlannerSolver._build_and_solve() 内で直接構築する。
    "terms": [],
    "default_weights": {},
}

OBJECTIVE_RECIPES["BinPacking"] = []   # 直接実装のため空配列


# ─── Auto-generated by domain_generator ───
OBJECTIVE_RECIPES["MedicalAppointmentScheduler"] = {
    # 目的関数は MedicalAppointmentSchedulerSolver._build_and_solve() 内で
    # mdl.minimize(totalDateViolation + totalResourceViolation + totalTimeViolation) として
    # 直接構築する（CSPLib prob089 準拠、係数なし単純合算）。
    "terms": [],
    "default_weights": {},
}
