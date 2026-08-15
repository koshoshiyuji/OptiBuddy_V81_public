export const YARD_CONFIG = {
  MAX_BAY: 20,
  MAX_ROW: 10,
  MAX_TIER: 6,
  DISPLAY_BAY: "B01",
  BAYS: Array.from({ length: 20 }, (_, i) => i + 1),
  ROWS: Array.from({ length: 10 }, (_, i) => i + 1),
  TIERS: [6, 5, 4, 3, 2, 1], // ヤードも上から積むため、Tierの順番が逆
  DEFAULT_CRANE: "RC-1"
};

export const VESSEL_CONFIG = {
  MAX_BAY: 20,
  MAX_ROW: 10,
  MAX_TIER: 8,
  DISPLAY_BAY: "V01",
  BAYS: Array.from({ length: 20 }, (_, i) => i + 1),
  ROWS: Array.from({ length: 10 }, (_, i) => i + 1),
  TIERS: [8, 7, 6, 5, 4, 3, 2, 1], // 船は上から積むため、Tierの順番が逆
  DEFAULT_CRANE: "GC-1"
};

export const OPERATION_CONFIG = {
  // タスク別・標準作業時間（秒）
  TIME_PICK: 120,    // ヤード搬出: 3分
  TIME_LOAD: 240,    // 船側積込: 5分（慎重な荷役）
  TIME_DISCHARGE: 240, // 船側揚卸: 5分（慎重な荷役）
  TIME_PLACE: 120,    // ヤード内置き場: 5分（慎重な荷役）
  TIME_MOVE: 120,    // ヤード内移動: 2分
  TIME_REHANDLE: 300, // リハンドリング: 4分
  MIN_TRANSIT_TIME: 60, // クレーン空移動(未使用)
  SAFETY_GAP: 10       // 物理干渉バッファ秒
};

export const SOLVER_CONFIG = {
  API_BASE: "http://localhost:5000"
}