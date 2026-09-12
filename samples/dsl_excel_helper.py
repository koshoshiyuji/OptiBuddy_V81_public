# -*- coding: utf-8 -*-
"""
OptiBuddy 業務DSL ⇔ Excel 変換ヘルパー（サンプル）
================================================================

業務DSL（例: samples/truck_dispatcher_api_sample.py の SAMPLE_DSL）は
JSONの手書き・手編集がしづらいため、Excel（複数シートの単一 .xlsx ファイル）
との相互変換ヘルパーを提供する。ドメイン固有の知識を一切持たず、値の
「形」だけを見て変換する汎用ロジックのため、TruckDispatcher に限らず
同じ形（トップレベルにレコードのリストとスカラー/dictが混在する）の
業務DSLであれば流用できる想定。

変換ルール
----------
  - トップレベルの値が「dictのリスト」（例: vehicles, customers）
    → そのキー名のシートに変換。1行=1レコード、列はレコード間の
      キーの和集合（あるレコードに無いキーは空セル）。
  - トップレベルの値が「dict」（例: config, meta, depot）や単純な
    スカラー値（例: problem_class, domain）
    → すべて1枚の "meta" シートにドット区切りキー（例: config.avg_speed_kmh,
      depot.lat）でまとめる（1行のみ）。
      ※ DSL側にたまたま "meta" というフィールドがある場合、その中身は
      "meta.date" のようなキーとしてこの集約シートに入る。集約シート名の
      "meta" とDSLフィールド名の "meta" は無関係（このツール側の命名）。
  - トップレベルが「空リスト」または「dict以外を含むリスト」の場合は、
    列の形を決められないためシート化せず、meta シートにJSON文字列として
    そのまま保持する。
  - レコード内の値がさらに list/dict の場合（現時点で確認している
    ドメインでは未発生）は、そのセルにJSON文字列として格納する。Excel上
    では生JSONのまま表示され、スプレッドシートとして直接編集できる形には
    ならない。

既知の制約（正直に明記しておく）
--------------------------------
  - Excelのシート名は31文字上限。切り詰めた結果、別のシート名と衝突した
    場合は黙って上書きせず例外を送出する。
  - 各シートの列順はキーのソート順になる。元のJSONのキー順は保持されない
    （値は一致するが順序は保証しない）。
  - 「キーが存在しない」と「キーはあるが値がnull」は、Excel往復後は
    区別できなくなる（どちらも空セル→Noneに統一される）。
  - 上記のいずれも、これは「サンプル/ヘルパー」であり本番のDSLバリデータ
    ではない。既存ドメインのDSLがこの制約の範囲に収まることは
    TruckDispatcher の SAMPLE_DSL で確認済みだが、全27ドメインを
    網羅的に確認したものではない。

前提
----
    pip install pandas openpyxl xlsxwriter
    （Backend/requirements.txt に既に含まれているため、Backend用の
    環境で実行する場合は追加インストール不要）

テスト方法
----------
  1. 変換ロジック自体の正しさを確認する（Flaskサーバーは不要）:

         python dsl_excel_helper.py

     「[3] 元のDSLと比較...」の後に「完全一致（往復変換OK）」と表示されれば、
     samples/truck_dispatcher_api_sample.py の SAMPLE_DSL に対する往復変換
     （dict → Excel → dict）が元のDSLと完全一致することが確認できたことに
     なる。差分がある場合は unified diff 形式で表示され、終了コードが1に
     なる。

  2. 生成された samples/sample_dsl_roundtrip.xlsx をExcelで開き、
     "meta" シート（config.*/depot.*/problem_class などがドット区切り
     キーの列として1行に入っている）と "vehicles"/"customers" シート
     （1行=1レコード）が SAMPLE_DSL の内容と対応しているか、目視でも
     確認できる。

  3. 他のドメインの業務DSLで試す場合は、そのDSL(dict)を用意して以下を
     実行するだけでよい:

         from dsl_excel_helper import dsl_to_excel, excel_to_dsl
         dsl_to_excel(your_dsl, "test.xlsx")
         restored = excel_to_dsl("test.xlsx")
         assert restored == your_dsl

     ネストが1階層より深いフィールドを持つドメインの場合、そのフィールド
     だけJSON文字列セルになる（前述「既知の制約」参照）ので、Excelを開いた
     ときにその見え方も併せて確認するとよい。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

_MAX_SHEET_NAME_LEN = 31


def _flatten(prefix: str, value: Any, out: dict) -> None:
    """dict/スカラー値をドット区切りキーで out に書き込む（メタ用）。"""
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else k
            _flatten(key, v, out)
    elif isinstance(value, list):
        out[prefix] = json.dumps(value, ensure_ascii=False)
    else:
        out[prefix] = value


def _cell_value(value: Any) -> Any:
    """レコード内の値をExcelセルに書ける形にする。list/dictはJSON文字列化。"""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _maybe_json(value: Any) -> Any:
    """セルの値がJSON文字列に見える場合はデコードして返す（往復変換用）。"""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in ("[", "{"):
            try:
                return json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return value
    return value


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return False


def _safe_sheet_name(name: str, used: set) -> str:
    truncated = name[:_MAX_SHEET_NAME_LEN]
    if truncated in used:
        raise ValueError(
            f"シート名 '{name}' が{_MAX_SHEET_NAME_LEN}文字に切り詰められた結果 "
            f"'{truncated}' となり、既存のシート名と衝突しました。"
            f"トップレベルのキー名を見直してください。"
        )
    used.add(truncated)
    return truncated


def _unflatten_meta(meta_row: dict) -> dict:
    """ドット区切りキーの1行分の辞書をネストしたdictに戻す。"""
    result: dict = {}
    for flat_key, value in meta_row.items():
        if _is_missing(value):
            continue
        parts = flat_key.split(".")
        node = result
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _maybe_json(value)
    return result


def dsl_to_excel(dsl: dict, path: str | Path) -> None:
    """業務DSL(dict)をExcelファイル(複数シート)に変換する。詳細はモジュール
    docstring参照。"""
    used_names: set = set()
    sheets: dict[str, pd.DataFrame] = {}
    meta: dict = {}

    for key, value in dsl.items():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            columns = sorted({k for record in value for k in record.keys()})
            rows = [{c: _cell_value(record.get(c)) for c in columns} for record in value]
            sheets[_safe_sheet_name(key, used_names)] = pd.DataFrame(rows, columns=columns)
        elif isinstance(value, list):
            # 空リスト、またはdict以外を含むリストは列の形を決められないため
            # シート化せず、metaにJSON文字列として保持する。
            meta[key] = json.dumps(value, ensure_ascii=False)
        else:
            _flatten(key, value, meta)

    meta_df = pd.DataFrame([meta]) if meta else pd.DataFrame()
    meta_sheet_name = _safe_sheet_name("meta", used_names)

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        meta_df.to_excel(writer, sheet_name=meta_sheet_name, index=False)
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def excel_to_dsl(path: str | Path) -> dict:
    """dsl_to_excel() で作ったExcelファイルを業務DSL(dict)に復元する。"""
    all_sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")

    dsl: dict = {}

    meta_df = all_sheets.pop("meta", None)
    if meta_df is not None and not meta_df.empty:
        meta_row = meta_df.iloc[0].to_dict()
        dsl.update(_unflatten_meta(meta_row))

    for sheet_name, df in all_sheets.items():
        records = []
        for _, row in df.iterrows():
            record = {col: _maybe_json(value) for col, value in row.items() if not _is_missing(value)}
            records.append(record)
        dsl[sheet_name] = records

    return dsl


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from truck_dispatcher_api_sample import SAMPLE_DSL

    out_path = Path(__file__).resolve().parent / "sample_dsl_roundtrip.xlsx"

    print(f"[1] SAMPLE_DSL (truck_dispatcher_api_sample.py) を {out_path.name} に変換...")
    dsl_to_excel(SAMPLE_DSL, out_path)
    print(f"    書き出し完了: {out_path}")

    print("[2] 変換したExcelを読み戻し...")
    restored = excel_to_dsl(out_path)

    print("[3] 元のDSLと比較...")
    if restored == SAMPLE_DSL:
        print("    完全一致（往復変換OK）")
    else:
        import difflib

        original_json = json.dumps(SAMPLE_DSL, ensure_ascii=False, indent=2, sort_keys=True)
        restored_json = json.dumps(restored, ensure_ascii=False, indent=2, sort_keys=True)
        diff = difflib.unified_diff(
            original_json.splitlines(),
            restored_json.splitlines(),
            fromfile="original",
            tofile="restored",
            lineterm="",
        )
        print("\n".join(diff))
        sys.exit(1)
