"""
engine_availability.py — CP Optimizer（cpoptimizer実行ファイル）の有無の判定

【背景】2026-09-29、DESIGN_2026-09-28_v2_paid_license_and_repo_split.md 8節・9節（V1修正 第2弾）
docplex（Apache 2.0）はrequirements.txtに入っているため、`import docplex.cp` は常に成功する。
CP Optimizerで実際に解けるかどうかは、別ライセンスの実行ファイル `cpoptimizer`
（requirements-cplex.txt の cplex パッケージが venv の bin に置く）が見つかるかで決まる。
docplexは既定で `cpoptimizer` をPATH上から探すため、venvを有効化せずにFlaskを起動すると
（例: venvのpythonをフルパスで起動）、インストール済みでも見つからない
（2026-09-29クラウドで確認: depot_route_plannerのStage2が最近傍法の近似解に落ちた）。

【探す順番】find_cpoptimizer()
  1. 環境変数 CPOPTIMIZER_PATH（.env。明示指定）
  2. docplexの設定 context.solver.local.execfile（絶対パスが設定されている場合）
  3. PATH上（shutil.which）
  4. 実行中のPythonと同じ bin フォルダ（venv の bin）

configure_cpo_execfile() をFlask起動時に1回呼ぶと、1/2/4で見つかった場合に docplex の
execfile へ絶対パスを設定する（PATHに無くてもCPOで解けるようにする）。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "find_cpoptimizer",
    "cpo_available",
    "configure_cpo_execfile",
    "generated_solver_uses_cpo",
    "warn_if_cpo_only_under_oss",
]

_EXEC_NAME = "cpoptimizer"
_CP_MARKERS = ("docplex.cp",)
_CPSAT_MARKERS = ("cpmpy",)


def _is_executable(p: Path) -> bool:
    return p.is_file() and os.access(str(p), os.X_OK)


def find_cpoptimizer() -> Optional[str]:
    """cpoptimizer実行ファイルの絶対パスを返す。見つからなければNone。"""
    env_path = (os.environ.get("CPOPTIMIZER_PATH") or "").strip().strip('"').strip("'")
    if env_path:
        p = Path(env_path).expanduser()
        if _is_executable(p):
            return str(p)
        logger.warning(
            f"[engine_availability] CPOPTIMIZER_PATH='{env_path}' が指定されていますが、"
            "実行可能なファイルが見つかりません。他の場所を探します。"
        )

    try:
        from docplex.cp.config import context
        configured = context.solver.local.execfile
    except Exception:
        configured = None
    if configured and os.path.isabs(str(configured)) and _is_executable(Path(configured)):
        return str(configured)

    on_path = shutil.which(_EXEC_NAME)
    if on_path:
        return on_path

    beside_python = Path(sys.executable).parent / _EXEC_NAME
    if _is_executable(beside_python):
        return str(beside_python)
    return None


def cpo_available() -> bool:
    """CP Optimizerで実際に解ける（docplexがimportでき、cpoptimizerが見つかる）か。"""
    try:
        import docplex.cp.model  # noqa: F401
    except Exception:
        return False
    return find_cpoptimizer() is not None


def configure_cpo_execfile() -> Optional[str]:
    """
    Flask起動時に1回呼ぶ。cpoptimizerが見つかれば docplex の execfile に絶対パスを設定し、
    そのパスを返す。見つからなければ何もせずNone（CPOを使わない環境では正常）。
    """
    path = find_cpoptimizer()
    if path is None:
        logger.info("[engine_availability] cpoptimizer は見つかりませんでした（CP Optimizerは使用不可）。")
        return None
    try:
        from docplex.cp.config import context
        context.solver.local.execfile = path
    except Exception as e:
        logger.warning(f"[engine_availability] docplexへのcpoptimizerパス設定に失敗: {e}")
        return None
    logger.info(f"[engine_availability] cpoptimizer: {path}")
    return path


def generated_solver_uses_cpo(diffs: list, snake: str) -> bool:
    """
    Stage2が生成したファイル一覧（generate_domain_files()のdiffs）のうち、
    solvers/{snake}_solver.py が docplex.cp を使っているか。
    """
    target = f"solvers/{snake}_solver.py"
    for d in diffs or []:
        path = str(d.get("path") or "")
        if path.replace("\\", "/").endswith(target):
            content = d.get("new_content") or d.get("content") or ""
            return any(m in content for m in _CP_MARKERS)
    return False


@lru_cache(maxsize=None)
def _is_cpo_only_source(source_file: str) -> bool:
    try:
        text = Path(source_file).read_text(encoding="utf-8")
    except Exception:
        return False
    return any(m in text for m in _CP_MARKERS) and not any(m in text for m in _CPSAT_MARKERS)


def warn_if_cpo_only_under_oss(solver_module: Any) -> None:
    """
    SOLVER_BACKEND=oss なのに CP-SAT 版を持たない（CPO専用の）ドメインを解く場合に警告ログを出す
    （DESIGN 8節 決定事項4。画面への表示はV2で対応、Koshoshi決定 2026-09-29）。
    """
    try:
        from solvers.base.engine_select import BACKEND_OSS, get_solver_backend
        if get_solver_backend() != BACKEND_OSS:
            return
        source_file = getattr(solver_module, "__file__", None)
        if source_file and _is_cpo_only_source(source_file):
            logger.warning(
                f"[engine_availability] {getattr(solver_module, '__name__', source_file)} は"
                "CP-SAT版を持たないため、SOLVER_BACKEND=oss でも CP Optimizer で解きます。"
            )
    except Exception:
        pass
