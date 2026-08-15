"""
baplie_to_dsl.py
BaplieParseResultをOptiBuddy 業務DSL v1形式に変換する。

デフォルトconfig値はOptiBuddyのconstants.tsと同期させること:
  TIME_PICK=180, TIME_LOAD=300, TIME_MOVE=120, TIME_REHANDLE=240

業務DSL v1形式:
{
    "problem_class": "RCPSP",
    "version": "1.0",
    "extensions": [...],   # BAPLIEの内容から自動推定
    "containers": [...],
    "resources": {...},
    "config": {...},
}

extensions自動推定ルール:
  - "physical_space"   : 常に付与（ヤード・船座標が存在する）
  - "attribute_zones"  : いずれかのコンテナにattrsが存在する場合
  - "phase_separation" : DISCHARGEコンテナ（pod==current_port）とLOADコンテナが混在する場合
  operation_type判定: pod == current_port → DISCHARGE、それ以外 → LOAD
"""

from typing import Any, Dict, List

from baplie.baplie_parser import BaplieContainer, BaplieParseResult, BaplieResource

# OptiBuddyのデフォルト設定値（constants.tsと同期）
DEFAULT_CONFIG = {
    "time_pick":      180,
    "time_load":      300,
    "time_discharge": 300,
    "time_place":     180,
    "time_move":      120,
    "time_rehandle":  240,
    "min_transit":     30,
    "safety_gap":      10,
    "max_bays":         5,
    "max_rows":         6,
    "max_tiers":        5,
    "max_vessel_bays": 20,
    "max_vessel_rows":  8,
    "max_vessel_tiers": 8,
    "weight_bay_move": 100,
    "weight_row_move":  10,
    "weight_block_risk": 200,
    "time_limit":       30,
    "crane_interference": False,
    "crane_safety_bays": 2,
}


def _detect_operation_type(container: BaplieContainer, current_port: str) -> str:
    """
    コンテナのpodとcurrent_portを比較してoperation_typeを判定する。
    pod == current_port → DISCHARGE（船→ヤード）
    それ以外           → LOAD（ヤード→船）
    """
    if current_port and container.pod and container.pod == current_port:
        return "DISCHARGE"
    return "LOAD"


def _build_resources(parse_result: BaplieParseResult) -> Dict[str, Any]:
    """
    BaplieResourceリストからOptiBuddy resources形式に変換。
    リソース情報がない場合はコンテナの実座標からbayリストを生成。
    """
    containers = parse_result.containers
    valid = [c for c in containers if c.is_valid()]

    # 実データから使用ベイ一覧を収集
    yard_bays = sorted(set(c.yard_bay for c in valid if c.yard_bay is not None))
    ship_bays = sorted(set(c.ship_bay for c in valid if c.ship_bay is not None))

    # BAPLIEにリソース定義がある場合はそちらを優先
    yard_cranes = []
    ship_cranes = []
    for r in parse_result.resources:
        crane = {"id": r.resource_id, "bay": r.work_bays or []}
        if r.resource_type == "YARD_CRANE":
            yard_cranes.append(crane)
        else:
            ship_cranes.append(crane)

    # リソース定義がない場合は実座標から自動生成
    if not yard_cranes:
        yard_cranes = [{"id": "RC-1", "bay": yard_bays or [1]}]
    if not ship_cranes:
        ship_cranes = [{"id": "GC-1", "bay": ship_bays or [10]}]

    return {
        "yard_cranes": yard_cranes,
        "ship_cranes": ship_cranes,
    }


def _build_config(parse_result: BaplieParseResult) -> Dict[str, Any]:
    """
    パース結果からconfigを生成。
    船内座標の最大値を動的に計算してmax_vessel_*を上書き。
    """
    config = dict(DEFAULT_CONFIG)

    containers = parse_result.containers
    if containers:
        valid = [c for c in containers if c.ship_bay is not None]
        if valid:
            config["max_vessel_bays"] = max(c.ship_bay  for c in valid)
            config["max_vessel_rows"] = max(c.ship_row  for c in valid if c.ship_row)
            config["max_vessel_tiers"]= max(c.ship_tier for c in valid if c.ship_tier)

        valid_yard = [c for c in containers if c.yard_bay is not None]
        if valid_yard:
            config["max_bays"]  = max(c.yard_bay  for c in valid_yard)
            config["max_rows"]  = max(c.yard_row  for c in valid_yard if c.yard_row)
            config["max_tiers"] = max(c.yard_tier for c in valid_yard if c.yard_tier)

    if parse_result.pol:
        config["current_port"] = parse_result.pol
    elif parse_result.pod:
        config["current_port"] = parse_result.pod

    return config


def _detect_extensions(
    valid_containers: List[BaplieContainer],
    current_port: str,
) -> List[str]:
    """
    コンテナ一覧からextensions配列を自動推定する。

    常に付与:
      - "physical_space": ヤード・船座標が存在する

    条件付き付与:
      - "attribute_zones"  : いずれかのコンテナにattrsが存在する
      - "phase_separation" : DISCHARGEとLOADが混在する
    """
    extensions = ["physical_space"]

    # attribute_zones: attrsを持つコンテナが1件でもあれば付与
    has_attrs = any(c.attrs for c in valid_containers)
    if has_attrs:
        extensions.append("attribute_zones")

    # phase_separation: DISCHARGE/LOADが混在する場合
    if current_port:
        op_types = set(
            _detect_operation_type(c, current_port)
            for c in valid_containers
        )
        if "DISCHARGE" in op_types and "LOAD" in op_types:
            extensions.append("phase_separation")

    return extensions


def baplie_to_dsl(parse_result: BaplieParseResult) -> Dict[str, Any]:
    """
    BaplieParseResultをOptiBuddy 業務DSL v1形式のdictに変換して返す。

    Args:
        parse_result: baplie_parser.parse_baplie_text() の戻り値

    Returns:
        業務DSL v1 dict
        {
            "problem_class": "RCPSP",
            "version": "1.0",
            "extensions": [...],
            "containers": [...],
            "resources":  {...},
            "config":     {...},
        }
    """
    # 有効なコンテナのみ変換（ship/yard座標が両方揃っているもの）
    valid_containers = [c for c in parse_result.containers if c.is_valid()]

    if not valid_containers:
        raise ValueError("有効なコンテナデータが見つかりません。ship/yard座標を確認してください。")

    config = _build_config(parse_result)
    current_port = config.get("current_port", "")

    containers_dsl = []
    for c in valid_containers:
        op_type = _detect_operation_type(c, current_port)
        entry: Dict[str, Any] = {
            "id":             c.container_id,
            "weight":         c.weight,
            "pol":            c.pol or parse_result.pol or "---",
            "pod":            c.pod or parse_result.pod or "---",
            "operation_type": op_type,
            "yard": {
                "bay":  c.yard_bay,
                "row":  c.yard_row  or 1,
                "tier": c.yard_tier or 1,
            },
            "ship": {
                "bay":  c.ship_bay,
                "row":  c.ship_row  or 1,
                "tier": c.ship_tier or 1,
            },
            "order": c.load_order,
        }
        # attrsが存在する場合のみ付与
        if c.attrs:
            entry["attrs"] = c.attrs

        containers_dsl.append(entry)

    # orderでソート（未設定=999は末尾）
    containers_dsl.sort(key=lambda c: c["order"])

    extensions = _detect_extensions(valid_containers, current_port)

    return {
        "problem_class": "RCPSP",
        "version":       "1.0",
        "extensions":    extensions,
        "containers":    containers_dsl,
        "resources":     _build_resources(parse_result),
        "config":        config,
    }


def convert_baplie_text_to_dsl(text: str) -> Dict[str, Any]:
    """
    BAPLIEテキスト文字列を直接業務DSL v1に変換するショートカット関数。
    app.pyのエンドポイントから呼ぶ。
    """
    from baplie.baplie_parser import parse_baplie_text
    parse_result = parse_baplie_text(text)
    return baplie_to_dsl(parse_result)
