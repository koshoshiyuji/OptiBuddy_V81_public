
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




# 目的関数の意味論的な見落としを検出するパターン（自動修正はしない、ログのみ）
# 例: 「urgent フラグを持つアイテムのみ未割り当てペナルティを付与し、
#      それ以外のアイテムには未割り当てペナルティが存在しない」という
#      実際に production_lot_scheduler で発生したパターンを検出する。
def _check_objective_coverage(code: str, path: str) -> str | None:
    """
    目的関数（penalty_terms 等への add）内に 'urgent' 等の条件分岐で
    presence_of / is_assigned 系のペナルティが「一部アイテムのみ」に
    限定されていないかを簡易チェックし、該当すれば警告ログを出す。

    完全な静的解析ではなく、ヒューリスティックな検出。
    誤検知の可能性があるため、自動修正は行わずログ警告のみとする。
    """
    # 「if ... urgent ...: ... presence_of(...) ...」のような
    # urgent 限定ブロック内で presence_of / is_assigned ペナルティが使われ、
    # かつ urgent 条件を伴わない同種のペナルティ加算が見当たらない場合に警告
    urgent_block_pattern = re.compile(
        r'for\s+\w+\s+in\s+\w+:\s*\n'
        r'(?:[^\n]*\n){0,3}?'
        r'[^\n]*if\s+not\s+\w+\.get\(["\']urgent["\'][^\n]*:\s*\n'
        r'[^\n]*continue',
    )
    presence_penalty_pattern = re.compile(r'presence_of\(.*?\)')

    if urgent_block_pattern.search(code) and presence_penalty_pattern.search(code):
        # urgent 限定の continue パターンが存在し、かつ presence_of ベースの
        # ペナルティ計算がその近くにある場合、非urgentアイテムへの
        # 同種ペナルティが欠落していないか要確認、という警告を出す
        return (
            f"[sanitize] {path}: 目的関数内で 'urgent' フラグにより処理をスキップして"
            f"いる箇所と presence_of() ベースのペナルティ計算が検出されました。"
            f"非urgentアイテムに対する同種の未割り当てペナルティが"
            f"目的関数から漏れていないか確認してください"
            f"（過去に urgent 限定の未割り当てペナルティのみ実装され、"
            f"非urgentアイテムが全て未割り当てになる不具合が発生しています）。"
        )
    return None


# 禁止パターン1: no_overlap に interval_var のリストと transition_matrix を直接渡している
# （sequence_var を経由していない）ことを検出する。
#
# [2026-07-16 修正] 元の実装は `mdl.no_overlap(引数1, 引数2)` という2引数呼び出しの形
# だけで判定しており、正しい書き方（`seq = mdl.sequence_var(...); mdl.no_overlap(seq, tm)`）
# も同じ形に見えるため常に誤検知していた（truck_dispatcher_solver.py で確認）。
# 第1引数が mdl.sequence_var() で作られた変数であれば正しい呼び方とみなし、
# それ以外（interval_varのリストを直接渡している等）の場合のみ警告する。
#
# [2026-07-28 修正] 今度は逆に見逃し（false negative）が発覚。第2引数が
# `mdl.transition_matrix(dist_matrix)` のようにその場で組み立てた関数呼び出し
# （ドット・括弧を含む）だと `\w+` にマッチせず、チェック自体が発火しなかった
# （NurseShiftEval registration で実際に発生 — `mdl.no_overlap(itvs,
# mdl.transition_matrix(dist_matrix))` という禁止パターン1そのものの誤用が
# 静的チェックをすり抜け、Gate2でも拾われず、実行時に毎回AssertionErrorで
# 落ちるバグとして初めて発覚した）。第2引数の中身は判定に使わず、単に
# 「カンマの後に何か（＝2引数以上）がある」ことだけを見て発火させ、第1引数が
# sequence_var かどうかだけで判定するように変更。第2引数の残り全体は警告文の
# 表示用にだけ緩く拾う（構文的に閉じていなくても表示上は問題ない）。
_SEQUENCE_VAR_ASSIGN_RE = re.compile(r'(\w+)\s*=\s*mdl\.sequence_var\(')
_NO_OVERLAP_CALL_RE     = re.compile(r'mdl\.no_overlap\(\s*(\w+)\s*,\s*(.+)$', re.MULTILINE)


def _check_no_overlap_without_sequence_var(code: str, path: str) -> list[str]:
    scan_code = _strip_line_comments(code)
    sequence_var_names = set(_SEQUENCE_VAR_ASSIGN_RE.findall(scan_code))
    warnings = []
    for m in _NO_OVERLAP_CALL_RE.finditer(scan_code):
        first_arg = m.group(1)
        if first_arg in sequence_var_names:
            continue
        warnings.append(
            f"{path}: no_overlap({first_arg}, {m.group(2)}) が検出されました（禁止パターン1）。"
            f"第1引数が mdl.sequence_var() で作られた変数だと確認できませんでした。"
            f"interval_var のリストを直接渡している場合は sequence_var 経由に変更してください。"
        )
    return warnings


def _strip_line_comments(code: str) -> str:
    """各行の最初の '#' 以降を雑に除去する（コード中で言及・説明しているだけの
    コメント行を、実際の違反コードと誤検知しないようにするための簡易前処理）。
    文字列リテラル内の '#' も区別なく切り捨てる粗い近似だが、ソルバーコードで
    '#' を含む文字列リテラルは稀なため実用上のヒューリスティックとして許容する。
    """
    return "\n".join(line.split("#", 1)[0] for line in code.splitlines())


# 禁止パターン6: mdl.minimize()/mdl.maximize() が mdl.add() で包まれていないかを検出する
# （HANDOFF_2026-07-15b 論点1-3で判明: プロンプトには追記済みだったが機械的検出が
#  存在しなかった。自動修正は括弧の対応を壊すリスクがあるため警告のみとする）
#
# 2026-07-20: このチェックはdocplex.cp.model（CP Optimizer）のAPI規約
# （mdl.add(mdl.minimize(...))と書く必要がある）を前提にしている。
# docplex.mp.model（CPLEX MP、StoreSiteで導入）ではmdl.minimize(obj)を
# 直接呼ぶのが正しい書き方であり、この前提が成立しないため恒常的に誤検知する
# （実機StoreSite登録で3回連続誤検知を確認、docs/ENGINEERING_LOG.md 2026-07-20参照）。
# docplex.mp.modelのimportを検出したファイルはこのチェック自体をスキップする。
_MINIMIZE_CALL_RE = re.compile(r'mdl\.(minimize|maximize)\(')
_DOCPLEX_MP_IMPORT_RE = re.compile(r'from\s+docplex\.mp\.model\s+import|import\s+docplex\.mp\.model')


def _check_unwrapped_minimize(code: str, path: str) -> list[str]:
    scan_code = _strip_line_comments(code)
    if _DOCPLEX_MP_IMPORT_RE.search(scan_code):
        # docplex.mp.model（CPLEX MP）はmdl.add()で包まない書き方が正しいため対象外。
        return []
    warnings = []
    for m in _MINIMIZE_CALL_RE.finditer(scan_code):
        preceding = scan_code[max(0, m.start() - 40): m.start()]
        # 直前（空白・改行を無視）が "mdl.add(" で終わっていれば正しく包まれている
        if re.search(r'mdl\.add\(\s*$', preceding):
            continue
        warnings.append(
            f"{path}: mdl.{m.group(1)}(...) が mdl.add() で包まれていない可能性があります"
            f"（禁止パターン6）。mdl.add(mdl.{m.group(1)}(...)) の形にしないと目的関数が"
            f"ソルバーに一度も登録されません。"
        )
    return warnings


# 禁止パターン6b: mdl.max()/mdl.min()/mdl.sum() で組み立てた無名式を、そのまま
# msol.get_value() でクエリしていないかを検出する（名前付き変数/KPIでないため
# 「Variable or KPI '...' not in the solution」で失敗する既知バグ）
_UNNAMED_EXPR_ASSIGN_RE = re.compile(r'(\w+)\s*=\s*mdl\.(?:max|min|sum)\(')
_GET_VALUE_VAR_RE       = re.compile(r'msol\.get_value\(\s*(\w+)\s*\)')


def _check_unnamed_expr_get_value(code: str, path: str) -> list[str]:
    scan_code = _strip_line_comments(code)
    unnamed_expr_vars = set(_UNNAMED_EXPR_ASSIGN_RE.findall(scan_code))
    if not unnamed_expr_vars:
        return []
    warnings = []
    for m in _GET_VALUE_VAR_RE.finditer(scan_code):
        var_name = m.group(1)
        if var_name in unnamed_expr_vars:
            warnings.append(
                f"{path}: msol.get_value({var_name}) が検出されました（禁止パターン6b）。"
                f"{var_name} は mdl.max/min/sum() で組み立てた名前無し式のため、"
                f"get_value() は『Variable or KPI が solution に無い』で失敗します。"
                f"既に get_var_solution() で抽出済みの schedule から直接計算するか、"
                f"msol.get_objective_value() を使ってください。"
            )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: Big-M近似（多目的の優先順位偽装）検出
#
# 背景（2026-07-22追加）: lexicographicモード（複数目的の優先順位付け）が
# 必要な場面で、mdl.minimize_static_lex() ではなく極端に桁の異なる係数を
# 使った単一目的関数（例: mdl.minimize(1000000*a + b)）で優先順位を偽装する
# 「Big-M近似」が過去に使われ、下位優先度の目的の最適性が数値的な許容誤差で
# 検証不能になる問題が発生した（Key Learnings、objective_terms.py の
# 「Big-M代替の禁止」節参照）。
#
# 検出方針: mdl.minimize()/mdl.maximize() 呼び出し内の式を、トップレベルの
# '+' で加算項に分割し、各項の先頭/末尾の数値リテラルを明示係数として抽出する
# （例: "1000000 * a + b" → 項["1000000 * a", "b"] → 係数[1000000, 1(暗黙)]）。
# 明示係数が無くても変数を含む項は暗黙係数1として扱う（Big-Mパターンの典型形
# 「大きい係数付きの項 + 係数無しの項」を見逃さないため）。最大/最小比が
# 閾値以上であればBig-M近似の疑いとして警告する。
# _check_objective_coverage 等と同様、ヒューリスティックな検出であり誤検知の
# 可能性があるため自動修正は行わずログ警告のみとする（_CPO_WARN_PATTERNS/
# 禁止パターンには追加せず、needs_confirmationの警告に合流させるに留める。
# ブロックするかどうかは別途の判断が必要）。
#
# docplex.mp.model（CPLEX MP）はmdl.minimize(obj)を直接呼ぶのが正しい書き方
# であり、_check_unwrapped_minimize と同じ理由で対象外とする
# （StoreSiteで誤検知確認済み、docs/ENGINEERING_LOG.md 2026-07-20参照）。

_BIG_M_RATIO_THRESHOLD = 1000
_LEADING_COEF_RE = re.compile(r'^(\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*\*(?!\*)')
_TRAILING_COEF_RE = re.compile(r'(?<!\*)\*(?!\*)\s*(\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)$')
_HAS_ALPHA_RE = re.compile(r'[A-Za-z_]')


def _extract_balanced_call_args(code: str, open_paren_idx: int) -> str | None:
    """code[open_paren_idx] が '(' である前提で、対応する閉じ括弧までの中身
    （括弧自体は含まない）を返す。文字列リテラル内の括弧は考慮しない簡易実装
    （目的関数の係数式に文字列リテラルが含まれることは実用上ほぼ無いため許容）。"""
    depth = 0
    for i in range(open_paren_idx, len(code)):
        ch = code[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return code[open_paren_idx + 1:i]
    return None


def _split_top_level_plus_terms(expr: str) -> list[str]:
    """expr をトップレベル（括弧の外）の '+' で加算項に分割する。
    括弧/角括弧/波括弧の深さを追跡し、内部の '+' では分割しない。"""
    terms = []
    depth = 0
    start = 0
    for i, ch in enumerate(expr):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == '+' and depth == 0:
            terms.append(expr[start:i])
            start = i + 1
    terms.append(expr[start:])
    return [t.strip() for t in terms if t.strip()]


def _term_coefficient(term: str) -> float | None:
    """加算項1つから係数を推定する。
    - 先頭/末尾に明示的な "数値 *" / "* 数値" があればその値。
    - 明示係数が無く、変数/式らしき内容（英字を含む）があれば暗黙係数1。
    - 変数を含まない純粋な数値定数項は係数比較の対象外として None を返す
      （目的関数のオフセット定数であり「複数目的の重み」ではないため）。
    符号（先頭の '-'）は絶対値化して係数の大きさの比較にのみ使う簡易近似。
    """
    term = term.strip()
    if term.startswith('-'):
        term = term[1:].strip()
    if not term:
        return None

    m = _LEADING_COEF_RE.match(term)
    if m:
        try:
            return float(m.group(1).replace('_', ''))
        except ValueError:
            return None

    m = _TRAILING_COEF_RE.search(term)
    if m:
        try:
            return float(m.group(1).replace('_', ''))
        except ValueError:
            return None

    if _HAS_ALPHA_RE.search(term):
        return 1.0

    return None


def _check_big_m_objective(code: str, path: str) -> list[str]:
    """
    mdl.minimize()/mdl.maximize() 呼び出し内の各加算項の係数（暗黙の1を含む）
    の最大/最小比を調べ、_BIG_M_RATIO_THRESHOLD 以上であればBig-M近似
    （多目的の優先順位を単一の重み付き合算で偽装するパターン）の疑いとして
    警告を返す。

    lexicographicモードで書くべき箇所を weighted_sum + 極端な係数差で
    代替していないかの確認を促すのが目的。優先順位付けが必要なら
    solvers.base.objective_terms.build_lexicographic_objective_exprs() 経由で
    mdl.minimize_static_lex([...]) を使うべき、という既存の設計方針
    （objective_terms.py参照）と対になっている。
    """
    scan_code = _strip_line_comments(code)
    if _DOCPLEX_MP_IMPORT_RE.search(scan_code):
        return []

    warnings: list[str] = []
    for m in _MINIMIZE_CALL_RE.finditer(scan_code):
        # m は 'mdl.minimize(' / 'mdl.maximize(' に一致し、末尾が '(' なので
        # m.end() - 1 がその開き括弧の位置になる。
        # （なお 'mdl.minimize_static_lex(' は '(' の直前が 'minimize' ではなく
        #  '_static_lex' なのでこの正規表現自体にマッチしない＝自動的に対象外）
        open_idx = m.end() - 1
        args = _extract_balanced_call_args(scan_code, open_idx)
        if args is None:
            continue

        coefs: list[float] = []
        for term in _split_top_level_plus_terms(args):
            c = _term_coefficient(term)
            if c is not None and c > 0:
                coefs.append(c)

        if len(coefs) < 2:
            continue
        ratio = max(coefs) / min(coefs)
        if ratio >= _BIG_M_RATIO_THRESHOLD:
            warnings.append(
                f"{path}: {m.group(0)}...) 内で係数比 約{ratio:.0f}倍（係数例: "
                f"{sorted(set(coefs))[:6]}）が検出されました。複数の目的の優先順位を"
                f"極端に異なる係数の重み付き合算（Big-M近似）で表現している疑いがあります。"
                f"優先順位付けが意図であれば、objective_terms.py の "
                f"build_lexicographic_objective_exprs() 経由で mdl.minimize_static_lex([...]) "
                f"を使ってください（Big-M近似は下位目的の最適性が数値的な許容誤差で検証不能に"
                f"なる既知の問題があります。意図的な重み付けで問題なければ無視して構いません）。"
            )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: optional interval_var の absent値誤用検出
#
# 背景（2026-08-10追加）: ProductionLineSequencing登録時に実機発生。
# mdl.start_of(var, 0) のように第2引数（absent時のデフォルト値）を指定した
# 呼び出しを、そのまま mdl.add(... == ...) 等の等式・不等式制約に使うと、
# 「presentならX、absentならデフォルト値」という意味になるため、absent（不採用）
# 候補すべてに事実上presentを強制してしまう。1バッチにつき複数の候補
# interval_varがあり「exactly 1つだけpresent」であるべきところ、デフォルト値と
# 一致しない候補が軒並みpresent強制され、exactly-1制約と矛盾して必ずinfeasibleに
# なった（実機再現済み）。big_m_warnings/unused_in_solver_warningsと同じ理由
# （誤検知パターンが薄く、実際に業務事故を起こした既知パターン）でblocking側に
# 分離する（Koshoshi合意、2026-08-10）。
# ─────────────────────────────────────────────────────────────

_OPTIONAL_INTERVAL_ABSENT_VALUE_CMP_RE = re.compile(
    r'mdl\.(?:start_of|end_of|size_of|length_of)\(\s*\w+\s*,\s*[^()]+?\)\s*(?:==|<=|>=|!=|<|>)'
    r'|(?:==|<=|>=|!=|<|>)\s*mdl\.(?:start_of|end_of|size_of|length_of)\(\s*\w+\s*,\s*[^()]+?\)'
)


def _check_optional_interval_absent_value(code: str, path: str) -> list[str]:
    """
    mdl.start_of/end_of/size_of/length_of(var, <absent時デフォルト値>) の
    2引数呼び出しが、比較演算子（==等）と直接組み合わされていないかを検出する。

    ヒューリスティックな検出であり、比較対象が別の変数に一度代入されてから
    比較される間接的なケースまでは追えない（他の_check_*関数と同じ粒度）。
    """
    scan_code = _strip_line_comments(code)
    warnings: list[str] = []
    for m in _OPTIONAL_INTERVAL_ABSENT_VALUE_CMP_RE.finditer(scan_code):
        warnings.append(
            f"{path}: {m.group(0)!r} のように、start_of/end_of/size_of/length_of の"
            f"第2引数（absent時のデフォルト値）を指定した呼び出しが比較演算子と直接組み合わされて"
            f"います。この形は『presentならX、absentならデフォルト値』を意味するため、mdl.add()内の"
            f"等式・不等式にそのまま使うと、absent（不採用）候補すべてに事実上presentを強制して"
            f"しまう既知の実害パターンです（ProductionLineSequencing登録時に実機発生、"
            f"『exactly 1 present』制約と矛盾し必ずinfeasibleになった）。interval_var生成時に"
            f"start=<値>等を固定値として渡すか、mdl.if_then(mdl.presence_of(var), "
            f"mdl.start_of(var) == <値>) のようにpresence条件付きにしてください"
            f"（第2引数なしのstart_of(var)を使うこと）。"
        )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（blocking）: Tier1 — MIP解の自己検証（is_valid_solution）
#
# 2026-09-01追加（Koshoshi合意）: MIPドメイン（docplex.mp採用）で、solve()後に
# is_valid_solution()による自己検証をしているかを検出する。MIPソルバーの実装
# によっては、solve()自体は成功してもソルバー内部のバグや数値誤差により
# 制約違反のある解を返すことがまれにある（実装例: solvers/store_site_solver.py）。
# required_gap_warningsと同じ扱い（blocking・force-apply可・非humanize）。
# ─────────────────────────────────────────────────────────────

def _check_mip_self_verification(code: str, path: str) -> list[str]:
    """
    docplex.mp（MIP）採用ドメインで、is_valid_solution()による解の自己検証が
    見当たらない場合に警告する。ヒューリスティックな検出（他の_check_*関数と
    同じ粒度）で、importの有無と呼び出し文字列の有無だけを見る。
    """
    scan_code = _strip_line_comments(code)
    if not _DOCPLEX_MP_IMPORT_RE.search(scan_code):
        return []
    if "is_valid_solution(" in scan_code:
        return []
    return [
        f"{path}: docplex.mp（MIP）を使用していますが、is_valid_solution()による解の"
        f"自己検証が見当たりません。MIPソルバーの実装によっては、solve()自体は成功しても"
        f"制約違反のある解をまれに返すことがあります。solve()直後に "
        f"sol.is_valid_solution(tolerance=1e-6) を呼び出し、Falseの場合はissueとして"
        f"報告するようにしてください（実装例: solvers/store_site_solver.py）。"
    ]


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（advisory）: i18nメッセージカバレッジ
#
# 背景（2026-08-11追加）: _I18N_MESSAGE_DICT_INSTRUCTION（Stage2プロンプト、
# is_dsl4のnew_domain登録に埋め込み）でBackend/i18n/{snake}_messages.py 経由の
# t()呼び出しを指示するようにしたが、プロンプト指示だけでは実効性が保証されない
# （LLMがtitle/message/label等に日本語文字列を直書きする既存の生成傾向に
# 戻ってしまう可能性がある）。本チェックはその検知用。
#
# advisory（非ブロッキング）とする理由: 「意味的カバレッジ判定」（ヒアリング
# 要件が実装に反映されているか等）と同種の性質の指摘であり、start_of誤用
# バグやBig-M近似のような客観的に実害が確定しているコード欠陥ではない。
# force_apply（指摘を残したまま登録する）で問題なく通せる（Koshoshi合意、
# 2026-08-11）。誤検知パターンも未検証の新規チェックである。
# ─────────────────────────────────────────────────────────────

_I18N_HARDCODED_JA_RE = re.compile(
    r'''["'](?:title|message|label|sub|unit)["']\s*:\s*'''
    r'''f?(["'])(?:(?!\1).)*?[぀-ヿ一-鿿](?:(?!\1).)*?\1''',
    re.DOTALL,
)


def _check_i18n_message_coverage(solver_code: str | None, ui_converter_code: str | None, snake: str) -> list[str]:
    """
    title/message/label/sub/unit キーに日本語文字列（f-string含む）が直書きされて
    おり、かつ Backend/i18n/{snake}_messages.py 経由の t() 呼び出し
    （`from i18n.{snake}_messages import t`）が見つからないファイルを検知する。
    """
    import_marker = f"from i18n.{snake}_messages import t"
    targets = [
        (f"Backend/solvers/{snake}_solver.py", solver_code),
        (f"Backend/dsl_transformer/{snake}_ui_converter.py", ui_converter_code),
    ]
    warnings: list[str] = []
    for path, code in targets:
        if not code:
            continue
        scan_code = _strip_line_comments(code)
        if import_marker in scan_code:
            continue
        if _I18N_HARDCODED_JA_RE.search(scan_code):
            warnings.append(
                f"[Gate2 i18nカバレッジチェック] {path}: title/message/label等のUI文言に"
                f"日本語文字列が直書きされているようですが、`{import_marker}` によるt()経由の"
                f"辞書参照が見つかりませんでした。Backend/i18n/{snake}_messages.py を作成（または"
                f"該当キーを追加）し、該当箇所を t(key, **params) 呼び出しに置き換えると日英"
                f"バイリンガル表示に対応できます（対応必須ではなくadvisoryです。既存の"
                f"nursing_workload_balance_messages.py がキー命名規則の実例です）。"
            )
    return warnings


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: table_sections配線チェック
#
# 背景（2026-07-22追加）: dsl_transformer/table_sections.py の
# build_table_section()/build_table_sections_from_issues() は「層A（共通コア）」
# として全ドメインのui_converterが経由すべきヘルパーだが、呼び出した結果を
# 実際に ui_dsl["table_sections"] として返し忘れる（変数に代入しただけ・
# 別の変数で上書きしてしまう等）と、ソルバーが計算した詳細テーブルが画面に
# 一切表示されないまま登録されてしまう。過去にGeneric4DSLView.tsx側が
# table_sectionsそのものを無視していた既知バグ（現在は修正済み、Key Learnings
# 「Generic4DSLView.tsx は新規ドメインの無言フォールバック」参照）と同種の、
# 「計算されたのに配線されていない」パターンをbackend側（{snake}_ui_converter.py）
# でも検出する。
#
# 検出方針: build_table_section(/build_table_sections_from_issues( の呼び出しが
# ファイル内にあるにもかかわらず、"table_sections" キーへの代入らしき記述
# （dict literalの "table_sections": ... ／ ui_dsl["table_sections"] = ... ／
# .setdefault("table_sections", ...)）が1つも見つからない場合に警告する。
# 他の_check_*関数と同じ粒度のヒューリスティックであり、制御フロー解析はしない
# （例えばif分岐の片方だけで配線されているケースまでは判定できない）。
# ヒューリスティックな検出のため自動修正は行わずログ警告のみとする。
# ─────────────────────────────────────────────────────────────

_BUILD_TABLE_SECTION_CALL_RE = re.compile(r'\bbuild_table_sections?(?:_from_issues)?\s*\(')
_TABLE_SECTIONS_KEY_ASSIGN_RE = re.compile(
    r'''["']table_sections["']\s*:'''             # dict literal: "table_sections": ...
    r'''|\[\s*["']table_sections["']\s*\]\s*='''  # ui_dsl["table_sections"] = ...
    r'''|\.setdefault\(\s*["']table_sections["']'''  # .setdefault("table_sections", ...)
)


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: KPIカード配線チェック
#
# 背景（2026-09-16追加）: MatchingOptimizationのsolve_time_sec（求解時間）で
# 実際に発覚した不具合。solver.py側は metrics に値を計算して詰めており、
# i18n/{snake}_messages.py にも "kpi.solve_time.label"/"kpi.solve_time.unit"
# という翻訳キーまで用意されていたが、{snake}_ui_converter.py の kpi_cards
# リストにカード自体を追加し忘れており、画面には一切表示されないまま
# 登録されていた。_check_table_sections_wiring と同じ「計算・翻訳キーまで
# 用意されているのに配線されていない」パターンをKPIカードについても検出する。
# i18nにkpi.<name>.labelが定義されている＝表示する意図があった強いシグナルと
# みなし、対応するidのカードがui_converter.py側に存在するかを突き合わせる
# （ヒューリスティックであり厳密な構文解析はしない。誤検知はあり得るが
# advisoryであり登録をブロックしない）。
# ─────────────────────────────────────────────────────────────

_KPI_LABEL_KEY_RE = re.compile(r'"kpi\.([a-zA-Z0-9_]+)\.label"')


def _check_kpi_card_coverage(ui_converter_code: str, i18n_code: str, snake: str) -> list[str]:
    """
    i18n/{snake}_messages.py に定義された kpi.<name>.label キーのうち、
    {snake}_ui_converter.py 側に対応する "id": "<name>" のkpi_cardが
    見つからないものを検知する。
    """
    if not ui_converter_code or not i18n_code:
        return []

    label_names = sorted(set(_KPI_LABEL_KEY_RE.findall(i18n_code)))
    if not label_names:
        return []

    scan_code = _strip_line_comments(ui_converter_code)
    warnings: list[str] = []
    for name in label_names:
        id_re = re.compile(r'"id"\s*:\s*"' + re.escape(name) + r'"')
        if not id_re.search(scan_code):
            warnings.append(
                f"i18n/{snake}_messages.py: 'kpi.{name}.label' という翻訳キーが定義されていますが、"
                f"{snake}_ui_converter.py のkpi_cardsに id='{name}' のカードが見つかりませんでした。"
                f"solver側で計算・翻訳キーまで用意されているのに、画面には表示されないまま登録される"
                f"疑いがあります（意図的に非表示にしている場合は無視して構いません）。"
            )
    return warnings


def _check_table_sections_wiring(code: str, path: str) -> list[str]:
    """
    build_table_section()/build_table_sections_from_issues() を呼んでいるのに、
    その結果を ui_dsl の "table_sections" キーとして配線し忘れていないかを
    ヒューリスティックに検出する。{snake}_ui_converter.py にのみ適用する想定。
    """
    scan_code = _strip_line_comments(code)
    if not _BUILD_TABLE_SECTION_CALL_RE.search(scan_code):
        return []  # そもそもtable_sectionsを使っていないドメイン（対象外）
    if _TABLE_SECTIONS_KEY_ASSIGN_RE.search(scan_code):
        return []

    return [
        f"{path}: build_table_section()/build_table_sections_from_issues() が呼ばれていますが、"
        f"'table_sections' キーへの代入が見つかりませんでした。組み立てたテーブルが ui_dsl に"
        f"配線されず、画面に一切表示されないまま登録される疑いがあります"
        f"（過去にGenericResultTable側がtable_sectionsを無視していた既知バグと同種の"
        f"「計算されたが配線されていない」パターンです。意図的にtable_sectionsを使わない"
        f"設計であれば無視して構いません）。"
    ]


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック: フィールド突き合わせ（converter出力キー ⇔ solver.py参照キー）
#
# 背景: HospitalShiftPlanner登録時に発覚した7件の不具合のうち5件
# （task["start"]/task["end"]参照ミス、required_skills丸ごと未実装、
#  availability無視、solver_time_limitキー名不一致、available_days依存）は、
# 「ソルバーが例外なく解を返す」ため feasible/infeasible 判定だけでは
# 検出できなかった。一方これらはすべて、converter.pyが実際に出力する
# 辞書キーとsolver.pyが参照する辞書キーの単純な集合比較で機械的に
# 検出可能だったことを確認済み（docs/DESIGN_2026-07-09_registration_gates_and_hard_soft.md
# 3-1節）。汎用ヒューリスティックであり厳密な意味解析ではない（ネストした
# オブジェクト単位の区別はしない、ファイル全体でのフラットな集合比較）。
# 誤検知はあり得るが、Gate2はneeds_confirmation（人間確認ゲート）への警告
# 追加であり登録を直接ブロックしないため許容する。シンプルさを優先し、
# ベンチマーク接続や自動デバッグループ等は今回のスコープに含めない。
# ─────────────────────────────────────────────────────────────

_FIELD_CHECK_IGNORE_KEYS = {
    "id", "name", "description", "label", "meta", "tag", "tagColor", "tag_color",
    "domain", "problem_class", "version", "issue_statuses", "config", "status",
    "feasible", "metadata", "solutions", "issues", "tasks", "staff",
    # 2026-07-24追加（解チェッカー、DESIGN_2026-07-21）: issue_statusesと同じ理由で
    # ここに追加する。いずれもconverter.pyが出力するドメイン固有DSLフィールドでは
    # なく、Gate2/app.py側がsolver_input/resultに後付けで注入・消費する
    # フレームワーク横断のプランビングキーのため、converter⇔solverのフィールド
    # 整合性チェックの対象外とする。
    #   _gate2_full_check: run_gate2_dynamic_verification()がsolver_inputに注入する
    #     フラグ（converterを経由しない）。現状MeetingRoomのみが参照するが、
    #     他ドメインが今後同様の非同期分岐チェックを追加した際にも同じ理由で
    #     誤検知するため、ドメイン個別ではなくここでグローバルに無視する。
    #   _deferred_checks: solverがresultに書き込む出力側キー（app.pyの
    #     _solve_4dsl_genericがpopして読む）。_extract_accessed_keys()が
    #     Subscriptのstore/load文脈を区別しないため、result["_deferred_checks"]=...
    #     という代入も「converterからの入力参照」として誤検知される
    #     （_extract_dict_literal_keys()はdictリテラル{...}のみを拾い、
    #     生成後にsubscript代入で追加したキーは自己参照抑制の対象にならない
    #     ため）。真の入力欠落バグではないためここで除外する。
    "_gate2_full_check", "_deferred_checks",
    # 2026-07-26追加（DSL⇔solver直接チェック新設に伴う既知の誤検知抑制）:
    #   _lns_used: TruckDispatcherのRouteDecomposer（route_decomposer.py、別ファイル）が
    #     result辞書へsubscript代入で設定するCE上限フォールバック発火フラグ。
    #     truck_dispatcher_solver.py側は result.get("_lns_used") で読むだけで、
    #     DSLにもconverterにも一度も現れない（正当にそうあるべき、ソルバー内部の
    #     実行時フラグのため）。_gate2_full_check/_deferred_checksと同種の
    #     「ドメインDSLフィールドではない、フレームワーク内部の配管キー」。
    "_lns_used",
    # 2026-09-09追加: _lns_usedと全く同じ理由で見落とされていたキー。
    #   _engine_used: truck_dispatcher_solver.py自身が result["_engine_used"] = ...
    #     というsubscript代入でエンジン選択結果（docplex_cpo/cpsat/
    #     savings_2opt_fallback）を記録し、同じ関数内で
    #     result.get("_engine_used", ...) と読み返すだけの内部フラグ。
    #     DSLにもconverterにも一度も現れない（正当にそうあるべき）。
    #     test_dsl_solver_field_consistency.py::test_truck_dispatcher_real_files_have_no_false_positive
    #     が実在するシナリオファイルを初めて読めるようになったことで誤検知が
    #     表面化した（それ以前はファイル欠落でこのassertに到達すらしていなかった）。
    "_engine_used",
}


def _extract_dict_literal_keys(tree: ast.AST) -> set:
    """辞書リテラルのキー（文字列定数のみ）を全て集める。"""
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
    return keys


def _extract_accessed_keys(tree: ast.AST) -> set:
    """`.get("key", ...)` / `["key"]` の形で参照されているキーを全て集める。"""
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
        if isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
    return keys


def check_converter_solver_field_consistency(converter_code: str, solver_code: str) -> dict:
    """
    Gate2静的チェック（フィールド突き合わせ）本体。
    converter.py の出力dictキー集合と solver.py の参照キー集合をAST比較する。

    返り値:
      missing_in_converter: solverが参照するがconverterが一度も出力しないキー
                             （実行時KeyError／常時デフォルト値化の疑い）
      unused_in_solver:     converterが出力するがsolverが一度も参照しないキー
                             （宣言されたが無視される制約の疑い）
      suppressed_self_referential: missing_in_converter候補だったが、solver.py自身が
                             同名キーをdictリテラルとして構築している（＝solverが
                             自分で作った出力/中間dictを後で.get()/[...]で読み返している
                             だけ）と判定して除外したキー。誤検知として握りつぶすのではなく、
                             ここに残して監査可能にする（2026-07-18 MeetingRoom登録時、
                             assigned/meeting_id/meeting_name が誤ってmissing_in_converterに
                             出た件の恒久対応。詳細はENGINEERING_LOG.md参照）。
    """
    result = {"missing_in_converter": [], "unused_in_solver": [], "errors": [],
               "suppressed_self_referential": []}
    try:
        conv_tree = ast.parse(converter_code)
    except SyntaxError as e:
        result["errors"].append(f"converter.py 構文エラー: {e}")
        return result
    try:
        solver_tree = ast.parse(solver_code)
    except SyntaxError as e:
        result["errors"].append(f"solver.py 構文エラー: {e}")
        return result

    converter_emitted = _extract_dict_literal_keys(conv_tree) - _FIELD_CHECK_IGNORE_KEYS
    solver_accessed    = _extract_accessed_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS
    solver_own_keys    = _extract_dict_literal_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS

    raw_missing = solver_accessed - converter_emitted
    # solver.py自身がそのキーをdictリテラルとして組み立てている場合、
    # 「converterからの入力が欠けている」のではなく「solver内部で作った出力/中間
    # 構造を自己参照で読み返している」可能性が高い（真の入力欠落バグなら、
    # solver側が同じキーを自分でも生成している必然性はない）。
    self_referential = raw_missing & solver_own_keys

    result["missing_in_converter"] = sorted(raw_missing - self_referential)
    result["unused_in_solver"]     = sorted(converter_emitted - solver_accessed)
    result["suppressed_self_referential"] = sorted(self_referential)
    return result


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（track1拡張）: DSLシナリオ突き合わせ（DSL JSON ⇔ converter.py参照キー）
#
# 背景: HANDOFF_2026-07-15b「顧客ごとのOptiBuddy構想」議論より。従来の
# check_converter_solver_field_consistency() はconverter.py⇔solver.pyという
# コード同士の対称性しか見ておらず、「ヒアリング→DSL」「DSL→コード」という
# 手前2段の突き合わせは一切機械チェックされていなかった。本チェックはそのうち
# 「DSL→converter」の段を、既存のmissing_in_converter/unused_in_solverと同じ
# AST差集合パターンを1段前に伸ばすだけで実現する（新規機構ではなく既存機構の拡張）。
# ─────────────────────────────────────────────────────────────

def _extract_json_keys(obj) -> set:
    """DSLシナリオ（JSON dict/listの入れ子）から辞書キーを再帰的に全て集める。"""
    keys: set = set()

    def _walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k)
                _walk(v)
        elif isinstance(o, list):
            for item in o:
                _walk(item)

    _walk(obj)
    return keys


def check_dsl_converter_field_consistency(dsl_scenarios: list, converter_code: str) -> dict:
    """
    Gate2静的チェック（DSL⇔converter フィールド突き合わせ）本体。
    business DSL JSON（Stage1b生成シナリオ）が実際に持つキー集合の和集合と、
    converter.py が参照するキー集合をAST/JSON比較する。

    dsl_scenarios: 複数シナリオ（baseline/infeasible等）のdictのリスト。
                   シナリオ間でキーの有無が揺れることがあるため和集合で見る
                   （あるシナリオにしか出てこないオプショナルフィールドを
                   誤ってmissing_in_dslと報告しないため）。

    返り値:
      missing_in_dsl:      converterが参照するがDSLシナリオが一度も提供しないキー
                            （実行時は常にデフォルト値/空にフォールバックする疑い）
      unused_in_converter: DSLシナリオが持つがconverterが一度も参照しないキー
                            （ヒアリング→DSLで拾われたのにコードに反映されていない疑い。
                             check_converter_solver_field_consistencyのunused_in_solverと
                             同種だが、こちらはDSL層の「宣言されたが無視されるデータ」を
                             捉える）
    """
    result = {"missing_in_dsl": [], "unused_in_converter": [], "errors": []}
    try:
        conv_tree = ast.parse(converter_code)
    except SyntaxError as e:
        result["errors"].append(f"converter.py 構文エラー: {e}")
        return result

    dsl_provided: set = set()
    for scenario in dsl_scenarios:
        dsl_provided |= _extract_json_keys(scenario)
    dsl_provided -= _FIELD_CHECK_IGNORE_KEYS

    converter_accessed = _extract_accessed_keys(conv_tree) - _FIELD_CHECK_IGNORE_KEYS

    result["missing_in_dsl"]      = sorted(converter_accessed - dsl_provided)
    result["unused_in_converter"] = sorted(dsl_provided - converter_accessed)
    return result


# ─────────────────────────────────────────────────────────────
# Gate2静的チェック（2026-07-26追加）: DSLシナリオ⇔solver 直接フィールド突き合わせ
# （ネストキー対応、converterを経由しない）
#
# 背景: check_converter_solver_field_consistency()（converter⇔solver）と
# check_dsl_converter_field_consistency()（DSL⇔converter）は、いずれも
# converter.py自身のAST（dictリテラル・.get()/[...]アクセス）を経由してキー集合を
# 構築している。しかし work_limits のように「converterがキー名を一切書き換えず、
# ネスト構造ごとパススルーする」フィールドでは、個々のネストキー名
# （例: max_night_shifts_per_rolling_7days）がconverter.py自身のコード中に
# 一度も文字列として現れない。そのため、solver側がそのネスト構造の中を誤った
# キー名（例: weekly_night_shift_cap）で読んでいても、converterを経由する上記
# 2チェックのどちらもこれを検出できない（2026-07-12 NurseShiftWeeklyCap登録時の
# 「ハード制約が2重に無効化されていた」不具合1が、まさにこの抜け穴を素通りした。
# ENGINEERING_LOG.md 2026-07-12参照）。
#
# 本チェックはDSLシナリオの再帰的キー集合（_extract_json_keys、ネスト対応）と
# solver.pyの参照キー集合（_extract_accessed_keys）を直接突き合わせる。新規の
# 抽出ロジックは追加せず、既存のヘルパー関数の組み合わせを変えるだけで実現している
# （check_converter_solver_field_consistency と同じ自己参照抑制ロジックも流用）。
#
# 注意（実装上必須の除外）: converter.pyがDSLに存在しない新規フィールドを
# 「計算して」合成するケース（例: TruckDispatcherのdepot_open_min/locations/
# dist_matrixは、DSLのdepot.open_time/customers[].lat,lon等からconverterが
# 計算して初めて生成する。DSL側にこれらのキー名は一度も存在しない）は、
# 「パススルー時のキー名drift」とは全く別の正常系であり、誤検知させてはならない。
# これを区別するため、converter.py自身が該当キーをdictリテラルとして構築して
# いる場合（converter_emitted）は「converterが正しく合成した既知フィールド」として
# missing判定から除外する。これにより本チェックが本当に捉えたいのは
# 「DSLにもconverterにも一度も現れないのに、solverが参照しているキー」
# （＝パススルー構造でのネストキー名drift、または本当に何にも由来しない参照）
# だけに絞られる。
# ─────────────────────────────────────────────────────────────

def check_dsl_solver_field_consistency(dsl_scenarios: list, solver_code: str, converter_code: str = "") -> dict:
    """
    Gate2静的チェック（DSL⇔solver 直接フィールド突き合わせ、ネストキー対応）本体。

    converter_code: 省略可。渡された場合、converter.py自身が計算・合成して
                    dictリテラルとして出力しているキー（DSLには存在しないが
                    converterが正当に新規生成するフィールド）を既知の情報源として
                    扱い、missing判定から除外する。

    返り値:
      missing_in_dsl_for_solver: solverが参照しているが、DSLシナリオにもconverterの
                                  合成結果にも（ネスト構造も含め）一度も現れないキー
                                  （実行時は常にデフォルト値化する疑い）
      suppressed_self_referential: missing_in_dsl_for_solver候補だったが、solver.py
                                  自身が同名キーをdictリテラルとして構築している
                                  （＝自分で作った中間/出力構造を読み返しているだけ）
                                  と判定して除外したキー。
    """
    result = {"missing_in_dsl_for_solver": [], "suppressed_self_referential": [], "errors": []}
    try:
        solver_tree = ast.parse(solver_code)
    except SyntaxError as e:
        result["errors"].append(f"solver.py 構文エラー: {e}")
        return result

    dsl_provided: set = set()
    for scenario in dsl_scenarios:
        dsl_provided |= _extract_json_keys(scenario)

    converter_emitted: set = set()
    if converter_code:
        try:
            converter_emitted = _extract_dict_literal_keys(ast.parse(converter_code))
        except SyntaxError as e:
            result["errors"].append(f"converter.py 構文エラー: {e}")

    known_sources = (dsl_provided | converter_emitted) - _FIELD_CHECK_IGNORE_KEYS

    solver_accessed = _extract_accessed_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS
    solver_own_keys = _extract_dict_literal_keys(solver_tree) - _FIELD_CHECK_IGNORE_KEYS

    raw_missing = solver_accessed - known_sources
    self_referential = raw_missing & solver_own_keys

    result["missing_in_dsl_for_solver"] = sorted(raw_missing - self_referential)
    result["suppressed_self_referential"] = sorted(self_referential)
    return result
