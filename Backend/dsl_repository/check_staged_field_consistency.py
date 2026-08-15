"""
Backend/dsl_repository/check_staged_field_consistency.py

git pre-commit フックから呼ばれる、Gate2静的フィールド整合性チェックの
「差分ベース」版。

lint_field_consistency_all_domains.py（全ドメイン走査・手動実行用）は
現状のキー集合をそのまま表示するだけなので、既存の登録済みドメインに
元から残っているヒューリスティックな誤検知（例: 2026-07-14時点で
nurse_shift_weekly_cap / truck_dispatcher の両方に、solverが自分自身で
組み立てる出力用ローカル辞書のキー（"task_id"や"vehicle_name"等）まで
「converterが出力していないキー」として拾ってしまっている十数件の
missing_in_converterが残っている）をそのままpre-commitの合否判定に
使うと、無関係な変更のコミットまで毎回ブロックしてしまう。

そこで本スクリプトは「このコミットで新たに増えたmissing_in_converterキー」
だけを見る。具体的には、コミット対象ドメインについて
  - before: 親コミット(HEAD)時点のsolver/converterの内容
  - after : 今回コミットしようとしている（ステージ済み＝インデックス）内容
の両方で check_converter_solver_field_consistency() を実行し、
afterで新たに増えたmissing_in_converterキー（＝before時点では無かったもの）
が1件でもあればコミットを止める。既存の誤検知は「beforeから存在した」
ものとして扱われ、ブロック対象にはならない。

使い方（.githooks/pre-commit から呼ばれる想定。リポジトリルートで実行）:
  cd Backend && python3 dsl_repository/check_staged_field_consistency.py \\
      nurse_shift_weekly_cap truck_dispatcher
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from domain_generator import check_converter_solver_field_consistency  # noqa: E402

BACKEND_REL = "Backend"  # リポジトリルートからの相対パス（gitコマンドはルートで実行する前提）


def _git_show(spec: str) -> str:
    """
    git show <spec> の内容を返す。ファイルが存在しない（新規追加前 / 削除後）
    場合は空文字を返す ―― この場合 check_converter_solver_field_consistency は
    空のキー集合を返すので、「ベースライン無し（＝全部が新規扱い）」として
    自然に扱われる。
    """
    try:
        out = subprocess.run(
            ["git", "show", spec],
            capture_output=True, text=True, check=True,
        )
        return out.stdout
    except subprocess.CalledProcessError:
        return ""


def _git_show_head(path: str) -> str:
    """親コミット(HEAD)時点の内容。"""
    return _git_show(f"HEAD:{path}")


def _git_show_staged(path: str) -> str:
    """
    ステージ済み（インデックス）の内容。
    注意: git showの構文上「:path」（コロン1つ＋パス）が正しい形で、
    「rev:path」の rev 部分を単純に空文字/コロンにした f"{rev}:{path}" 形式では
    "::path" という不正な構文になってしまう（実際に最初の実装でこのバグを踏んだ）。
    """
    return _git_show(f":{path}")


def _check(converter_code: str, solver_code: str) -> dict:
    if not converter_code.strip() or not solver_code.strip():
        return {"missing_in_converter": [], "unused_in_solver": [], "errors": []}
    return check_converter_solver_field_consistency(converter_code, solver_code)


def main() -> None:
    snakes = sys.argv[1:]
    if not snakes:
        print("[Gate2 pre-commit] 対象ドメインが指定されていません（スキップ）。")
        sys.exit(0)

    any_new_missing = False
    for snake in sorted(set(snakes)):
        solver_path    = f"{BACKEND_REL}/solvers/{snake}_solver.py"
        converter_path = f"{BACKEND_REL}/dsl_transformer/{snake}_converter.py"

        solver_before    = _git_show_head(solver_path)
        converter_before = _git_show_head(converter_path)
        solver_after     = _git_show_staged(solver_path)
        converter_after  = _git_show_staged(converter_path)

        result_before = _check(converter_before, solver_before)
        result_after  = _check(converter_after,  solver_after)

        if result_after.get("errors"):
            print(f"--- {snake} ---")
            print(f"  [構文エラー] {result_after['errors']}（このドメインのチェックはスキップ）")
            continue

        new_missing = sorted(
            set(result_after["missing_in_converter"]) - set(result_before["missing_in_converter"])
        )
        new_unused = sorted(
            set(result_after["unused_in_solver"]) - set(result_before["unused_in_solver"])
        )

        if not new_missing and not new_unused:
            continue  # 変化なし。無関係な変更で毎回ノイズを出さないよう何も表示しない。

        print(f"--- {snake} ---")
        if new_missing:
            any_new_missing = True
            print(f"  [NG] このコミットで新たに missing_in_converter に加わったキー: {new_missing}")
            print(
                "       solverが参照しているが converter が一度も出力していません。"
                "実行時KeyError、または.get()の暗黙デフォルト値への意図しないフォールバックの疑いがあります。"
            )
        if new_unused:
            print(f"  [参考] このコミットで新たに unused_in_solver に加わったキー: {new_unused}")
            print("       converterが出力しているが solver が一度も参照していません（コミットはブロックしません）。")

    if any_new_missing:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
