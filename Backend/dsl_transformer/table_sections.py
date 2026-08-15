"""
Backend/dsl_transformer/table_sections.py

table_sections ビルダー — UI DSL 汎用テーブル出力ヘルパー
==========================================================

【目的】
  各ドメインの {snake}_ui_converter.py（および convert_solver_to_ui）が
  ui_dsl["table_sections"] を手書きの辞書リテラルで組み立てると、
  キー名のtypoや形式の揺れがドメインごとに発生しやすい
  （特にLLMによるStage2コード生成時）。

  本ヘルパーを経由することで、フロントエンドの
  Frontend/src/app/studio/components/GenericResultTable.tsx が
  要求するスキーマと必ず一致させる。

【使い方】
  from dsl_transformer.table_sections import build_table_section, TableColumn

  section = build_table_section(
      section_id="facilities",
      title="拠点一覧",
      columns=[
          TableColumn(key="name", label="拠点名"),
          TableColumn(key="fixed_cost", label="固定費/月", format="currency"),
          TableColumn(key="coverage", label="カバー率", format="percent"),
      ],
      rows=[
          {"facility_id": "F1", "name": "拠点A", "fixed_cost": 1200000, "coverage": 0.92},
          ...
      ],
      row_id_key="facility_id",   # 各行の一意キーとなる元データのキー
      severity_key=None,          # 行ハイライト用（省略可）
  )
  ui_dsl["table_sections"] = [section]

【スキーマ（GenericResultTable.tsx と対応）】
  TableColumn: {key, label, align?, format?, unit?}
    format: "number" | "currency" | "percent" | "text" | "badge"
  TableSection: {id, title, columns, rows, allow_download?}
  row: {_id, _severity?, ...元データ}

【設計方針】
  - このモジュールは「層A（共通コア）」に位置づける。
    issue_rules.py / objective_terms.py / constraint_applier.py と
    同じ「共通コア + レシピ」の思想を踏襲する。
  - ドメイン固有ロジック（どの列を出すか、何をseverityとするか）は
    呼び出し側（各{snake}_ui_converter.py）に置き、本モジュールは
    スキーマ整形のみを担う。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

ColumnFormat = Literal["number", "currency", "percent", "text", "badge"]
ColumnAlign = Literal["left", "right", "center"]
Severity = Literal["CRITICAL", "WARNING", "INFO"]


@dataclass
class TableColumn:
    key: str
    label: str
    align: Optional[ColumnAlign] = None
    format: Optional[ColumnFormat] = None
    unit: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"key": self.key, "label": self.label}
        if self.align:
            d["align"] = self.align
        if self.format:
            d["format"] = self.format
        if self.unit:
            d["unit"] = self.unit
        return d


def build_table_section(
    section_id: str,
    title: str,
    columns: List[TableColumn],
    rows: List[Dict[str, Any]],
    row_id_key: Optional[str] = None,
    severity_key: Optional[str] = None,
    allow_download: bool = True,
    extra_metrics: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    行データ(rows)から GenericResultTable.tsx が要求する
    table_section 辞書を組み立てる。

    Args:
        section_id:     セクション識別子（英数字推奨。CSVファイル名に使用される）
        title:          画面表示タイトル
        columns:        TableColumn のリスト
        rows:           元データの辞書リスト（columnsで定義したkeyを含むこと）
        row_id_key:     各行の一意キーとなる元データのキー名。
                        省略時は連番 "{section_id}_{i}" を使用する。
        severity_key:   行の重要度（"CRITICAL"|"WARNING"|"INFO"）を
                        持つキー名。指定すると各行に _severity が付与される。
                        値がこの3値以外の場合は付与されない。
        allow_download: CSVダウンロードボタンを表示するか（デフォルト True）
        extra_metrics:  ソルバーが追加で計算した「行単位の追加指標」を汎用的に
                        列として合流させるための差し込み口（V8.7）。
                        2026-07-30 i18n対応でスキーマ変更（V8.8）: 以前は
                        {row_id: {列ラベル: 値}} という「ラベル文字列自体を
                        列keyとして流用する」形式だったため、CSV列名の
                        言語非依存化（列key/labelの分離）が効かなかった
                        （DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md
                        1-2節「動的列見出し」参照）。
                        新形式: {row_id: {metric_key: {"label": 表示ラベル, "value": 値}}}
                        例: {"S001": {"rolling_night_count":
                                        {"label": "直近7日間の夜勤回数（最大）", "value": 3}},
                             "TRK004": {"avg_wait_min":
                                        {"label": "平均待機時間(分)", "value": 12}}}
                        出現したmetric_keyの和集合から追加のTableColumn
                        （key=metric_key, label=初出時のlabel, format="number"固定）を
                        自動生成し、該当行にのみ値を埋める（該当なしの行は空欄）。
                        row_id_key未指定時は使用されない。
                        "staff"や"truck"等ドメイン固有の概念はこの関数のどこにも
                        登場しない＝行を一意に識別できるどんなドメインの
                        table_sectionにも同じ仕組みで使える。拡張機能で
                        「新しい指標を1列足したい」場合、ui_converter側の
                        列定義を書き換える必要がなくなり、ソルバー側で
                        extra_metricsに値を積むだけで済む。

    Returns:
        Dict: table_section 辞書（そのまま ui_dsl["table_sections"] に
              append すれば GenericResultTable.tsx で表示可能）
    """
    extra_columns: List[TableColumn] = []
    if extra_metrics and row_id_key:
        # 出現したmetric_keyの和集合を取り、順序は初出順にする。
        # labelは初出時の値を採用する（同じmetric_keyで行ごとにlabelが
        # 変わるケースは現状想定していない）。
        seen_keys: List[str] = []
        label_by_key: Dict[str, str] = {}
        for metrics in extra_metrics.values():
            for metric_key, entry in metrics.items():
                if metric_key not in seen_keys:
                    seen_keys.append(metric_key)
                    label_by_key[metric_key] = entry.get("label", metric_key)
        extra_columns = [
            TableColumn(key=metric_key, label=label_by_key[metric_key], format="number")
            for metric_key in seen_keys
        ]

    ui_rows: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        ui_row = dict(row)
        if row_id_key and row.get(row_id_key) is not None:
            ui_row["_id"] = str(row[row_id_key])
        else:
            ui_row["_id"] = f"{section_id}_{i}"

        if severity_key and row.get(severity_key) in ("CRITICAL", "WARNING", "INFO"):
            ui_row["_severity"] = row[severity_key]

        if extra_columns and row_id_key:
            row_metrics = extra_metrics.get(ui_row["_id"], {}) if extra_metrics else {}
            for col in extra_columns:
                if col.key in row_metrics:
                    ui_row[col.key] = row_metrics[col.key].get("value")

        ui_rows.append(ui_row)

    return {
        "id": section_id,
        "title": title,
        "columns": [c.to_dict() for c in (columns + extra_columns)],
        "rows": ui_rows,
        "allow_download": allow_download,
    }


def build_table_sections_from_issues(
    section_id: str,
    title: str,
    issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    run_issue_rules() の出力（issues リスト）をそのまま table_section 化する
    ショートカット。issue詳細を表形式でも見せたいドメイン向けの補助関数。

    Args:
        section_id: セクション識別子
        title:      画面表示タイトル
        issues:     issue_rules.run_issue_rules() の戻り値

    Returns:
        Dict: table_section 辞書
    """
    columns = [
        TableColumn(key="severity", label="重要度", format="badge"),
        TableColumn(key="title", label="タイトル"),
        TableColumn(key="message", label="内容"),
    ]
    rows = [
        {
            "id": i.get("id", ""),
            "severity": i.get("severity", "INFO"),
            "title": i.get("title", ""),
            "message": i.get("message", ""),
        }
        for i in issues
    ]
    return build_table_section(
        section_id=section_id,
        title=title,
        columns=columns,
        rows=rows,
        row_id_key="id",
        severity_key="severity",
    )
