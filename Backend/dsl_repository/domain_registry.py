"""
domain_registry.py

scenarios.domain (snake_case) と dsl_definitions.problem_class (PascalCase) の
対応関係を一元管理する。

背景: 大半のドメインは to_pascal(snake) の機械変換で一致するが、
"yard" -> "YardPlanning" のように機械変換では導けない例外が存在する
（2026-07-20、export_domain.py設計時にサンドボックス検証で確認済み）。
cleanup_domain.py 等の個別スクリプトがそれぞれ snake/pascal変換を
持つのではなく、ここを単一の参照点にする。

新規ドメインは原則として to_pascal(domain_key) に従うことを前提とし、
それに従わない場合のみ DOMAIN_REGISTRY に明示的なエントリを追加すること。
"""

import re
from typing import Optional

# 明示的な対応表（機械変換で導けない例外を含む）
DOMAIN_REGISTRY: dict[str, str] = {
    "line_changeover_scheduler": "LineChangeoverScheduler",
    "meeting_room":              "MeetingRoom",
    "nurse_shift_weekly_cap":    "NurseShiftWeeklyCap",
    "store_site":                "StoreSite",
    "truck_dispatcher":          "TruckDispatcher",
    "yard":                      "YardPlanning",   # 唯一の非機械変換ケース
}

_REVERSE_REGISTRY: dict[str, str] = {v: k for k, v in DOMAIN_REGISTRY.items()}


def to_pascal(snake: str) -> str:
    """snake_case -> PascalCase の機械変換（DOMAIN_REGISTRY未登録の新規ドメイン用）"""
    return "".join(w[0].upper() + w[1:] if w else w for w in snake.split("_"))


def domain_key_to_problem_class(domain_key: str) -> str:
    """
    scenarios.domain (snake_case) から dsl_definitions.problem_class (PascalCase) を得る。
    DOMAIN_REGISTRYに無ければ機械変換にフォールバックする。
    """
    return DOMAIN_REGISTRY.get(domain_key) or to_pascal(domain_key)


def problem_class_to_domain_key(problem_class: str) -> Optional[str]:
    """
    dsl_definitions.problem_class (PascalCase) から scenarios.domain (snake_case) を得る。
    DOMAIN_REGISTRYに無ければNoneを返す（機械逆変換は曖昧になりやすいため行わない）。
    """
    return _REVERSE_REGISTRY.get(problem_class)


def to_snake(pascal: str) -> str:
    """PascalCase -> snake_case の機械変換（to_pascal の逆。DOMAIN_REGISTRY未登録の
    新規ドメイン用フォールバック）。"""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", pascal)
    return s.lower()


def normalize_domain_key(domain_key: str) -> str:
    """
    scenarios.domain 列の検索に使うべき snake_case 値へ正規化する。

    背景（2026-07-25）: export_domain.py に PascalCase の problem_class
    （例: "CarSequencing"）を domain_key として誤って渡すと、dsl_definitions/
    extensions は domain_key_to_problem_class() 経由で（to_pascal が既に
    PascalCaseな文字列に対して素通りするため偶然）正しく取れてしまう一方、
    scenarios は domain_key をそのまま WHERE domain = ? に使うため
    サイレントに0件になる、という気づきにくい不具合があった。
    export_domain() の入口でこの正規化を必ず通すことで、大文字小文字の
    取り違えを吸収する。

    優先順位:
      1. すでに DOMAIN_REGISTRY の snake_case キーならそのまま返す。
      2. _REVERSE_REGISTRY に problem_class として登録されていれば、
         対応する snake_case キーに変換する（例: "YardPlanning" -> "yard"）。
      3. どちらでもなく、PascalCase的（アンダースコアを含まず先頭が大文字）に
         見える場合は to_snake() で機械変換する（例: "CarSequencing" ->
         "car_sequencing"）。
      4. それ以外（すでにsnake_caseの新規ドメイン等）はそのまま返す。
    """
    if domain_key in DOMAIN_REGISTRY:
        return domain_key
    if domain_key in _REVERSE_REGISTRY:
        return _REVERSE_REGISTRY[domain_key]
    if "_" not in domain_key and domain_key[:1].isupper():
        return to_snake(domain_key)
    return domain_key
