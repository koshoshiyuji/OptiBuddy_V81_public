"""
Backend/solvers/registry.py  (V7.1 - Robust Fix)

V7.1:
  - _to_pascal() の数字・連続大文字対応を強化 ("test_domain_v2" -> "TestDomainV2")
  - solve() 時に hint が指定されているにもかかわらずソルバーが見つからない場合、
    勝手にデフォルトへフォールバックせず、明確に例外を投げるように変更（サイレントバグ防止）。
"""

import importlib
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_SOLVERS_DIR = Path(__file__).parent

# registry に表示するが実行は app.py の _solve_4dsl_generic()（規約ベースルーティング）
# が担うドメイン（旧CapacitatedVehicleRoutingProblemは削除済み。TruckDispatcherは
# _solve_4dsl_generic()経由で自動実行されるため、ここにdisplay_onlyとして登録する必要はない）。
# 2026-07-20: 案内文言中の"_DSL4_SOLVERS"は削除済みのため"_solve_4dsl_generic()"に更新。
_DSL4_DISPLAY_ONLY: dict[str, str] = {}

# デフォルトソルバー名
_DEFAULT_SOLVER = "cplex_dynamic"

# 登録をスキップするファイル
_SKIP_FILES = {
    "base_solver.py", "registry.py", "decomposer.py",
    "full_yard_solver.py",
}

# ---------------------------------------------------------------------------
# DSL定義との整合性チェック（2026-07-27追加、監査目的のみ・登録/実行の挙動は変えない）
# ---------------------------------------------------------------------------
#
# 背景: このレジストリーは solvers/*_solver.py というファイル名パターンにさえ
# 一致すれば、Gate1/Gate2の承認やdsl_definitions/extension登録の有無に関わらず
# 自動的に登録する（本来はStage2生成物の自動検出が目的だが、実験用の
# プロトタイプファイルも同じ経路で無条件に登録されてしまう）。
# 2026-07-26に実証実験として作られたBalancedNursingWorkloadSolver（DSL定義・
# extension・進化ログのいずれにも存在しない）が、この経路でレジストリーに
# 「本番ドメインと見分けが付かない形」で登録されていたことが2026-07-27の
# Koshoshiとの会話で発覚した。
#
# 対応方針: 開発中の新規ドメインをDSL定義登録より先にsolver.pyだけ書いて
# 動作確認する、という既存の開発フロー自体は妨げたくないため、登録・選択・
# 実行の挙動は変えない。dsl_definitionsに対応する行が無いsolverが見つかったら
# 起動時にWARNINGログを出すだけに留める（監査目的）。
_KNOWN_NON_DSL_SOLVERS = {
    # cplex_dynamic: 特定の1ドメインに対応するものではなく、YardPlanning等が
    # 経由する汎用エンジンラッパー（cplex_dynamic_solver.py参照）。
    # dsl_definitionsに対応する行が無いのは想定通りのため、警告対象から除外する。
    "cplex_dynamic",
}


def _load_known_dsl_problem_classes() -> Optional[set]:
    """
    dsl_repository/optibuddy.db の dsl_definitions.problem_class 一覧を返す。
    DBに接続できない・テーブルが無い等の場合は None を返す。呼び出し側は
    None の場合「確認不能」として警告を出さない（DBパスがずれている等の
    環境差でfalse positiveを大量に出し、監査ログとしての信頼性を落とさない
    ための保守的な失敗時挙動）。
    """
    try:
        db_path = Path(__file__).parent.parent / "dsl_repository" / "optibuddy.db"
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT problem_class FROM dsl_definitions")
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"[Registry] dsl_definitions確認をスキップ(DB読み込み失敗): {e}")
        return None


def _warn_if_missing_dsl_definition(snake: str, known_problem_classes: Optional[set]) -> None:
    """既知のDSL定義一覧に対応する行が無いsolverについてWARNINGログを出す（監査目的のみ）。"""
    if known_problem_classes is None or snake in _KNOWN_NON_DSL_SOLVERS:
        return
    from dsl_repository.domain_registry import domain_key_to_problem_class
    problem_class = domain_key_to_problem_class(snake)
    if problem_class not in known_problem_classes:
        logger.warning(
            f"[Registry] '{snake}' (problem_class='{problem_class}') はdsl_definitions "
            f"テーブルに登録されていません。Gate1/Gate2を経た本番ドメインではなく、"
            f"実験用/未承認のプロトタイプの可能性があります。本番ドメインとして扱ってよいか"
            f"確認してください（監査目的のログのみで、登録・実行の動作自体は変更していません）。"
        )


def _to_pascal(snake: str) -> str:
    if not snake:
        return ""
    # アンダースコアで分割し、各単語の先頭を大文字にする
    # v2 などのケースで capitalization が正しく処理されるように保証
    return "".join(w[0].upper() + w[1:] if len(w) > 1 else w.upper() for w in snake.split("_"))


class SolverEntry:
    def __init__(self, name, solver_fn, description="", is_default=False, display_only=False):
        self.name         = name
        self.solver_fn    = solver_fn
        self.description  = description
        self.is_default   = is_default
        self.display_only = display_only


class SolverRegistry:

    def __init__(self):
        self._solvers: Dict[str, SolverEntry] = {}
        self._default_name: Optional[str] = None
        self._pre_hooks:  list[Callable] = []
        self._post_hooks: list[Callable] = []
        self._register_all_solvers()

    def register(self, name, solver_fn, description="", is_default=False,
                 display_only=False) -> "SolverRegistry":
        entry = SolverEntry(name, solver_fn, description, is_default, display_only)
        self._solvers[name] = entry
        
        # 明示的にデフォルト指定された場合、またはまだデフォルトがない場合に設定
        if is_default or (self._default_name is None and not display_only):
            self._default_name = name
        logger.info(f"[Registry] ソルバー登録: '{name}' (default={is_default}, display_only={display_only})")
        return self

    def add_pre_hook(self, fn):
        self._pre_hooks.append(fn); return self

    def add_post_hook(self, fn):
        self._post_hooks.append(fn); return self

    def solve(self, solver_input: Dict[str, Any]) -> Dict[str, Any]:
        if not self._solvers:
            raise RuntimeError("[Registry] ソルバーが1つも登録されていません。")

        # フロントエンドや呼び出し元から渡されるヒントを取得
        hint = solver_input.get("metadata", {}).get("solver_hint", "")
        
        entry = None
        if hint:
            entry = self._solvers.get(hint)
            if entry is None:
                # 🚨 サイレントフォールバックを廃止し、明示的にエラーを出す
                available = list(self._solvers.keys())
                raise RuntimeError(
                    f"[Registry] 指定された solver_hint='{hint}' に対応するソルバーが見つかりません。 "
                    f"登録済みソルバー: {available}"
                )
        else:
            # ヒントが完全に空の場合のみデフォルトソルバーを採用
            entry = self._solvers.get(self._default_name)

        if entry is None:
            raise RuntimeError(f"[Registry] ソルバーが見つかりません: hint='{hint}', default='{self._default_name}'")
        if entry.display_only:
            raise RuntimeError(f"[Registry] '{entry.name}' は表示専用です。app.py の _solve_4dsl_generic()（規約ベースルーティング）経由で実行されます。")

        logger.info(f"[Registry] ソルバー選択: '{entry.name}' (hint='{hint}')")
        for hook in self._pre_hooks:
            solver_input = hook(solver_input)

        t0 = time.perf_counter()
        try:
            solver_output = entry.solver_fn(solver_input)
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error(f"[Registry] '{entry.name}' エラー ({elapsed:.2f}s): {e}", exc_info=True)
            raise

        elapsed = time.perf_counter() - t0
        logger.info(f"[Registry] '{entry.name}' 完了: status={solver_output.get('status')}, elapsed={elapsed:.2f}s")
        
        for hook in self._post_hooks:
            solver_output = hook(solver_input, solver_output)
            
        solver_output.setdefault("_registry_meta", {}).update(
            {"solver_name": entry.name, "elapsed_sec": round(elapsed, 3)})
        return solver_output

    def _register_all_solvers(self):
        """solvers/ ディレクトリを自動スキャンして {snake}_solver.py を登録する。"""
        known_problem_classes = _load_known_dsl_problem_classes()

        solver_files = sorted(_SOLVERS_DIR.glob("*_solver.py"))
        for fpath in solver_files:
            if fpath.name in _SKIP_FILES:
                continue
            snake = fpath.stem[: -len("_solver")]
            self._try_register_from_file(fpath, snake)
            if snake in self._solvers and not self._solvers[snake].display_only:
                _warn_if_missing_dsl_definition(snake, known_problem_classes)

        for snake, desc in _DSL4_DISPLAY_ONLY.items():
            if snake not in self._solvers:
                self.register(
                    name         = snake,
                    solver_fn    = _noop_solver,
                    description  = desc,
                    is_default   = False,
                    display_only = True,
                )

        if self._default_name is None and self._solvers:
            first = next(iter(self._solvers))
            self._solvers[first].is_default = True
            self._default_name = first

    def _try_register_from_file(self, fpath: Path, snake: str):
        """1つの solver ファイルを import して登録を試みる。"""
        pascal       = _to_pascal(snake)
        class_name   = f"{pascal}Solver"
        module_name  = f"solvers.{snake}_solver"
        is_default   = (snake == _DEFAULT_SOLVER.replace("-", "_"))
        display_only = snake in _DSL4_DISPLAY_ONLY

        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            logger.warning(f"[Registry] import 失敗: {module_name} — {e}")
            return
        except Exception as e:
            logger.warning(f"[Registry] import エラー: {module_name} — {e}")
            return

        solver_class = getattr(module, class_name, None)
        if solver_class is None:
            # クラス名の揺れに対応：モジュール内で *Solver を探す
            for attr in dir(module):
                if attr.endswith("Solver") and attr != "BaseSolver":
                    solver_class = getattr(module, attr)
                    class_name   = attr
                    break
                    
        if solver_class is None:
            logger.debug(f"[Registry] Solverクラスなし: {module_name} — スキップ")
            return

        description = _extract_description(module, solver_class, snake)

        if display_only:
            self.register(
                name         = snake,
                solver_fn    = _noop_solver,
                description  = description or _DSL4_DISPLAY_ONLY.get(snake, ""),
                is_default   = False,
                display_only = True,
            )
        else:
            def _make_fn(cls):
                def _fn(solver_input: dict) -> dict:
                    return cls(solver_input).solve()
                return _fn
            self.register(
                name        = snake,
                solver_fn   = _make_fn(solver_class),
                description = description,
                is_default  = is_default,
            )

    def list_solvers(self) -> list[Dict[str, Any]]:
        return [
            {
                "name":         e.name,
                "description":  e.description,
                "is_default":   e.is_default,
                "display_only": e.display_only,
            }
            for e in self._solvers.values()
        ]

    def __repr__(self) -> str:
        names = list(self._solvers.keys())
        return f"<SolverRegistry solvers={names} default='{self._default_name}'>"


def _noop_solver(solver_input: dict) -> dict:
    raise RuntimeError("このソルバーは display_only です。app.py の _solve_4dsl_generic()（規約ベースルーティング）経由で実行されます。")


def _extract_description(module, solver_class, snake: str) -> str:
    doc = (solver_class.__doc__ or "").strip()
    if doc:
        first_line = doc.splitlines()[0].strip()
        if len(first_line) > 10:
            return first_line
    mod_doc = (module.__doc__ or "").strip()
    if mod_doc:
        for line in mod_doc.splitlines():
            line = line.strip()
            if len(line) > 10 and not line.startswith("#"):
                return line
    return snake.replace("_", " ").title()