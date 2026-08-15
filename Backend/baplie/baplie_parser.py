"""
baplie_parser.py
BAPLIE D95B形式のEDIテキストをパースしてContainerリストを返す。

座標形式: 7桁固定 BBBRRTT
  BBB: ベイ番号 (3桁)
  RR:  ロウ番号 (2桁)
  TT:  ティア番号 (2桁)

例: "0100102" → Bay=10, Row=1, Tier=2

属性マッピング (BAPLIE D95B セグメント):
  DGS+IMD+<class>  → IMO_CLASS_<class>
  ATT+11+RF        → REEFER
  ATT+11+OOG / ATT+11+OG / ATT+11+OD / ATT+11+OS  → OOG
  TSR+RF           → REEFER  (旧規格フォールバック)
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BaplieContainer:
    container_id: str
    size_type:    str        = ""
    pol:          str        = ""
    pod:          str        = ""
    weight:       float      = 0.0
    load_order:   int        = 999  # SEQセグメントから取得

    # 船内座標
    ship_bay:  Optional[int] = None
    ship_row:  Optional[int] = None
    ship_tier: Optional[int] = None

    # ヤード座標
    yard_bay:  Optional[int] = None
    yard_row:  Optional[int] = None
    yard_tier: Optional[int] = None

    # コンテナ属性 (例: ["REEFER"], ["IMO_CLASS_3"], ["OOG"])
    attrs: list = field(default_factory=list)

    def is_valid(self) -> bool:
        """最低限の座標情報が揃っているか"""
        return (
            self.ship_bay is not None and
            self.yard_bay is not None
        )


@dataclass
class BaplieResource:
    resource_id: str
    resource_type: str        # "YARD_CRANE" | "SHIP_CRANE"
    work_bays:   list = field(default_factory=list)


@dataclass
class BaplieParseResult:
    containers: list  # List[BaplieContainer]
    resources:  list  # List[BaplieResource]
    vessel_name: str  = ""
    voyage:      str  = ""
    pol:         str  = ""
    pod:         str  = ""


def _parse_location(loc_str: str) -> Optional[tuple]:
    """
    7桁の座標文字列をパースして (bay, row, tier) を返す。
    例: "0100102" → (10, 1, 2)
    不正な場合は None を返す。
    """
    s = loc_str.strip().split(":")[0]  # "0100102:139:6" → "0100102"
    if len(s) < 7:
        return None
    try:
        bay  = int(s[0:3])
        row  = int(s[3:5])
        tier = int(s[5:7])
        return (bay, row, tier)
    except ValueError:
        return None


def _parse_weight(mea_seg: str) -> float:
    """
    MEAセグメントから重量(float)を取得する。
    例: "MEA+WT+VGM+MT:25.0" → 25.0
    """
    try:
        # MT:25.0 の形式を想定
        weight_part = mea_seg.split(":")[-1]
        return float(weight_part.strip())
    except (ValueError, IndexError):
        return 0.0


def _parse_dgs_class(dgs_seg: str) -> Optional[str]:
    """
    DGSセグメントからIMOクラスを取得する。
    例: "DGS+IMD+3+1234+I"  → "IMO_CLASS_3"
         "DGS+IMD+3.1+..."   → "IMO_CLASS_3"
    DGS+IMD+<class>+... 形式。classが空の場合はNoneを返す。
    """
    parts = dgs_seg.split("+")
    # parts[0]=DGS, parts[1]=IMD|..., parts[2]=クラス番号
    if len(parts) < 3:
        return None
    raw_class = parts[2].strip()
    if not raw_class:
        return None
    # "3.1" → "3", "9" → "9"
    digit = raw_class.split(".")[0]
    if digit.isdigit() and 1 <= int(digit) <= 9:
        return f"IMO_CLASS_{digit}"
    return None


def _parse_att_attr(att_seg: str) -> Optional[str]:
    """
    ATTセグメントからコンテナ属性を取得する。
    BAPLIE D95B: ATT+11+<code>
      RF          → REEFER
      OOG / OG / OD / OS  → OOG
    """
    parts = att_seg.split("+")
    if len(parts) < 3:
        return None
    code = parts[2].strip().upper()
    if code == "RF":
        return "REEFER"
    if code in ("OOG", "OG", "OD", "OS"):
        return "OOG"
    return None


def _parse_tsr_attr(tsr_seg: str) -> Optional[str]:
    """
    TSRセグメント（旧規格フォールバック）からREEFERを取得する。
    TSR+RF+... → REEFER
    """
    parts = tsr_seg.split("+")
    if len(parts) >= 2 and parts[1].strip().upper() == "RF":
        return "REEFER"
    return None


def parse_baplie_text(text: str) -> BaplieParseResult:
    """
    BAPLIEテキスト文字列をパースしてBaplieParseResultを返す。

    Args:
        text: BAPLIEのEDIテキスト文字列

    Returns:
        BaplieParseResult
    """
    result = BaplieParseResult(containers=[], resources=[])

    # ' をデリミタとしてセグメント分割
    segments = [s.strip() for s in text.split("'") if s.strip()]

    current_container: Optional[BaplieContainer] = None
    current_resource:  Optional[BaplieResource]  = None
    pol_global = ""  # ヘッダーレベルのPOL

    for seg in segments:
        parts = seg.split("+")
        tag   = parts[0].strip()

        # ---------------------------------------------------
        # TDT: 航海情報（船名・航海番号）
        # ---------------------------------------------------
        if tag == "TDT":
            if len(parts) > 2:
                result.voyage = parts[2]
            if len(parts) > 7:
                result.vessel_name = parts[7]

        # ---------------------------------------------------
        # LOC: ヘッダーレベルの出発港・到着港
        # ---------------------------------------------------
        elif tag == "LOC" and current_container is None and current_resource is None:
            if len(parts) < 3:
                continue
            loc_type = parts[1]
            loc_val  = parts[2].split(":")[0]
            if loc_type == "5":    # 出発港
                result.pol = loc_val
                pol_global = loc_val
            elif loc_type == "61": # 到着港
                result.pod = loc_val

        # ---------------------------------------------------
        # EQD: コンテナまたはリソースの開始
        # ---------------------------------------------------
        elif tag == "EQD":
            if len(parts) < 3:
                continue
            eqd_type  = parts[1]
            entity_id = parts[2]
            size_type = parts[3] if len(parts) > 3 else ""

            if eqd_type == "MHE":
                # リソース（クレーン等）
                current_resource  = BaplieResource(
                    resource_id=entity_id,
                    resource_type="YARD_CRANE"
                )
                result.resources.append(current_resource)
                current_container = None
            else:
                # 通常コンテナ
                current_container = BaplieContainer(
                    container_id=entity_id,
                    size_type=size_type,
                    pol=pol_global,
                )
                result.containers.append(current_container)
                current_resource = None

        # ---------------------------------------------------
        # LOC: コンテナ/リソースレベルの座標
        # ---------------------------------------------------
        elif tag == "LOC":
            if len(parts) < 3:
                continue
            loc_type = parts[1]
            loc_val  = parts[2]

            if current_container:
                coords = _parse_location(loc_val)
                if loc_type == "147" or loc_type == "171":
                    # ヤード座標
                    if coords:
                        current_container.yard_bay, \
                        current_container.yard_row, \
                        current_container.yard_tier = coords
                elif loc_type == "165":
                    # 船内座標
                    if coords:
                        current_container.ship_bay, \
                        current_container.ship_row, \
                        current_container.ship_tier = coords
                elif loc_type == "11":
                    # POD（揚げ地）
                    current_container.pod = loc_val.split(":")[0]

            elif current_resource:
                if loc_type in ("171", "165"):
                    coords = _parse_location(loc_val)
                    if coords and coords[0] > 0:
                        bay = coords[0]
                        if bay not in current_resource.work_bays:
                            current_resource.work_bays.append(bay)
                    # loc_typeでYARD/SHIPを判別（最初のLOCで確定）
                    if current_resource.resource_type == "YARD_CRANE" and loc_type == "165":
                        current_resource.resource_type = "SHIP_CRANE"
                    elif loc_type == "171":
                        current_resource.resource_type = "YARD_CRANE"

        # ---------------------------------------------------
        # MEA: 重量
        # ---------------------------------------------------
        elif tag == "MEA" and current_container:
            if "VGM" in seg or "WT" in seg:
                current_container.weight = _parse_weight(seg)

        # ---------------------------------------------------
        # SEQ: 積み順（order）
        # ---------------------------------------------------
        elif tag == "SEQ" and current_container:
            # SEQ++1 → parts[2] = "1"
            if len(parts) > 2 and parts[2].strip().isdigit():
                current_container.load_order = int(parts[2].strip())

        # ---------------------------------------------------
        # DGS: 危険物クラス → IMO_CLASS_N
        # ---------------------------------------------------
        elif tag == "DGS" and current_container:
            attr = _parse_dgs_class(seg)
            if attr and attr not in current_container.attrs:
                current_container.attrs.append(attr)

        # ---------------------------------------------------
        # ATT: コンテナ属性（REEFER, OOG）
        # ---------------------------------------------------
        elif tag == "ATT" and current_container:
            attr = _parse_att_attr(seg)
            if attr and attr not in current_container.attrs:
                current_container.attrs.append(attr)

        # ---------------------------------------------------
        # TSR: REEFER フォールバック（旧規格）
        # ---------------------------------------------------
        elif tag == "TSR" and current_container:
            attr = _parse_tsr_attr(seg)
            if attr and attr not in current_container.attrs:
                current_container.attrs.append(attr)

        # ---------------------------------------------------
        # FTX: PODのフォールバック
        # ---------------------------------------------------
        elif tag == "FTX" and current_container:
            if "POD" in seg and len(parts) > 4:
                current_container.pod = parts[4]

    return result
