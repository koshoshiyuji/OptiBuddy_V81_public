"""
solver_error_result.py — 共通: ソルバー内部の想定外例外(実装バグ)を、
業務上の「解なし(infeasible)」と区別可能な形で表現するための共通部品。

[背景 2026-07-28]
各ドメインsolverの `except Exception` ハンドラは、`mdl.solve()` 等が
想定外の例外を投げた場合に、業務上の「制約を満たす解が存在しない
(infeasible)」場合と同じ形の結果を返してしまいがちだった。
実例: nurse_shift_eval_solver.py で `mdl.no_overlap(itvs, mdl.transition_matrix(...))`
というAPI誤用がAssertionErrorを起こしていたが、except Exceptionで
そのまま infeasible 扱いにされ、原因特定に長時間を要した
（ソルバーのバグなのか、本当に業務要件が矛盾しているのか、
画面上もログ上も区別できなかったため）。

[Frontendの制約 — 重要]
Frontend (`useStudioState.ts` の `hasSolveFailed` 判定、`InfeasibleView.tsx`)
は `issues[].id === "solve_failed"` であることのみを見て「Infeasible画面
（制約見直しタブ・AI緩和提案）」を表示するかどうかを決めており、
結果dictの `feasible` フラグそのものは見ていない。
そのため、crash用のissueであっても id は "solve_failed" のまま使う必要がある。
id を "solver_error"/"solver_exception" 等の独自値にすると、Infeasible画面
自体が表示されなくなり、CRITICALな例外が画面のどこにも明示されない
（overview/issuesモードに落ちて事実上握りつぶされる）退行になる。
car_sequencing_solver.py 内の2026-07-25付コメント（独自id化で「制約見直し
タブ」がFeasible誤判定した実例）を参照。

本モジュールはこの制約の中で、issueの title/message と、結果dict
トップレベルの "_solver_crashed" マーカーによって
「これはコードの例外であり、業務要件を満たす解が無いという意味ではない」
ことを判別可能にする。
"""
from typing import Any, Dict


def _is_cpoptimizer_missing(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "cpoptimizer" in msg and ("does not exist" in msg or "not found" in msg)


def build_solver_crash_issue(exc: Exception) -> Dict[str, Any]:
    """
    except Exception ハンドラ内で使う、クラッシュ用issueオブジェクトを返す。
    id は Frontend 互換のため "solve_failed" のまま。

    2026-09-29追加: SOLVER_BACKENDで指定したエンジンが使えない場合
    （SolverEngineUnavailableError。例: SOLVER_BACKEND=cplex でCPLEX本体が無い、
    OSSのMIPソルバーでは扱えない制約を含むモデル）は、「プログラムの不具合」では
    なく設定・環境の問題として、案内を含む専用の title/message を返す。
    """
    from solvers.base.ce_limit_mip_fallback import SolverEngineUnavailableError
    # 2026-09-29追加（V1修正 第2弾）: cpoptimizer実行ファイルが見つからない場合の
    # docplex.cpの例外（CpoException: "Executable file 'cpoptimizer' does not exists"）も
    # 同じ扱いにする。docplexは基本インストールに入っているため、CP Optimizer専用の
    # ドメインはimportまでは成功し、solve()で初めてこの例外になる。
    if _is_cpoptimizer_missing(exc):
        return {
            "id": "solve_failed",
            "severity": "CRITICAL",
            "title": "ソルバーエンジンが利用できません（設定・環境の問題）",
            "message": (
                "このドメインはCP Optimizerで解きますが、この環境にcpoptimizerが見つかりません。"
                "`pip install -r requirements-cplex.txt`（Community Editionで可）で導入してください。"
                "インストール済みの場合は、.env の CPOPTIMIZER_PATH に cpoptimizer の絶対パスを"
                f"指定してください（詳細: {type(exc).__name__}: {exc}）。\n"
                "これは「現在の制約では業務上の解が存在しない」という判定ではありません。"
            ),
            "relatedContainerIds": [],
        }
    if isinstance(exc, SolverEngineUnavailableError):
        return {
            "id": "solve_failed",
            "severity": "CRITICAL",
            "title": "ソルバーエンジンが利用できません（設定・環境の問題）",
            "message": (
                f"{exc}\n"
                "これは「現在の制約では業務上の解が存在しない」という判定ではありません。"
            ),
            "relatedContainerIds": [],
        }
    return {
        "id": "solve_failed",
        "severity": "CRITICAL",
        "title": "ソルバー内部エラー（実行不可能ではなくプログラム側の不具合の可能性）",
        "message": (
            f"ソルバー内部で想定外の例外が発生しました: {type(exc).__name__}: {exc}\n"
            "これは「現在の制約では業務上の解が存在しない」という判定ではありません。"
            "実装側の不具合（バグ）の可能性が高いため、開発者へ連絡してください。"
        ),
        "relatedContainerIds": [],
    }


def solver_crash_extra_fields(exc: Exception) -> Dict[str, Any]:
    """
    結果dictのトップレベルに追加するマーカー（`result.update(...)` で使う）。
    ログ解析やGate2の動的チェックが「これはクラッシュだった」と
    プログラム的に判別できるようにするためのもの。UI表示には影響しない。
    """
    return {
        "_solver_crashed": True,
        "_error_type": type(exc).__name__,
    }
