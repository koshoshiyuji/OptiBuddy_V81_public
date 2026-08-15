// src/domain/types.ts

export interface Location {
  domain: "YARD" | "VESSEL" | "NONE";
  bay: number;
  row: number;
  tier: number;
}

export interface Task {
  id: string;
  containerId: string;
  operation: "LOAD" | "DISCHARGE" | "MOVE" | "REHANDLE" | "PICK" | "PLACE";
  resource: string;
  resourceId?: string; // 4DSL compatibility
  order: number;
  status?: 'pending' | 'scheduled';
  start: number;
  end: number;
  weight: number;
  from: Location;
  to: Location;
  domain: "YARD" | "VESSEL";
  yard: { bay: number; row: number; tier: number };
  ship: { bay: number; row: number; tier: number };
  ports: { pol: string; pod: string };
}

// GanttTask: TaskGantt.tsx が本来必要とする最小限のフィールドだけを持つ
// ドメイン非依存の型（2026-07-14追加）。
// YARD/VESSEL固有の Task はこの型のスーパーセットなので、Task[] はそのまま
// GanttTask[] として渡せる（構造的部分型付けにより後方互換）。
// 新規ドメインはこちらの型だけを満たせばよく、containerId/operation/yard/ship/ports
// のようなYARD固有フィールドをダミー値で埋める必要はない。
export interface GanttTask {
  id: string;
  start: number;   // 秒
  end: number;     // 秒
  resourceId?: string;      // レーンのグルーピングキー（一意性が必要）
  resourceLabel?: string;   // レーンヘッダーの表示名（無ければresource→resourceIdの順でフォールバック）
  label?: string;           // バー内・ツールチップの表示名（無ければcontainerId→idの順でフォールバック）
  colorKey?: string;        // 色分けのグルーピングキー（無ければtask_id→containerId→idの順でフォールバック）
  operation?: string;       // 指定があればYARD系の操作別色分け・凡例を使う。無ければ汎用色分けになる
  status?: string;
  // riskLevel（2026-07-14追加）: 指定があれば colorKey ベースの自動色分けより
  // 優先して、意味付きの3色（緑=余裕あり/オレンジ=タイト/赤=違反）で表示する。
  // 例: TruckDispatcherの時間指定余裕度（tw_slack_min）による色分け。
  riskLevel?: "ok" | "warning" | "critical";
  // 以下はYARD/VESSEL系ドメインとの後方互換用（無くても動作する）
  resource?: string;
  containerId?: string;
  task_id?: string;
}

export interface SnapshotContainer {
  containerId: string;
  weight?: number;
  currentPos: "YARD" | "VESSEL";
  yard: { bay: number; row: number; tier: number };
  ship: { bay: number; row: number; tier: number };
  pod: string;
  pol: string;
  visibleInYard: boolean;
  visibleInVessel: boolean;
}

export interface BayGrid {
  [row: number]: { [tier: number]: string };
}

export interface Snapshot {
  yard: { [bay: number]: BayGrid };
  ship: { [bay: number]: BayGrid };
  containers: Record<string, SnapshotContainer>;
  availableBays: number[];
}

export type HighlightId =
  | { kind: "none" }
  | {
      kind: "active";
      hoverContainerId: string;
      selectedIssueId: string;
      relatedContainerIds: string[];
    };

export type FixStrategy = "MANUAL" | "AI" | "NONE";
export type IssueAction = "NONE" | "FIX" | "ACCEPT";

export interface Issue {
  id: string;
  title: string;
  message: string;
  containerId?: string;
  relatedContainerIds: string[];
  severity: "CRITICAL" | "WARNING" | "INFO";
  // "SOLVER": 解チェッカー（Backend/solvers/base/solution_checker.py）が検出した、
  // DSL宣言のhard制約と返ってきた解の矛盾＝コードバグ疑いのissueに付与される
  // （2026-07-24追加、既存のsolve_failed/zero_assignment_anomalyと同じ意味づけ）。
  category?: "YARD" | "MBP" | "WEIGHT" | "ATTR" | "SHIFT" | "SOLVER"; // ★ SHIFT追加
  fixStrategy?: FixStrategy;
  status: "UNRESOLVED" | "FIXED" | "ACCEPTED";
  action?: IssueAction;
  pair?: [string, string];
  // 2026-07-24追加: 非同期解チェッカー（O(n²)以上、閾値超過時）が本来のsolveレスポンス
  // より後に発見し、/checks/status/<job_id> 経由で追記されたissueであることを示す。
  // 既に表示・承認済みの解を自動的に覆すことはしない方針（HANDOFF_2026-07-24参照）の
  // ため、UI側はこのフラグを見て「追って判明した警告」であることを明示する。
  _deferred_check?: boolean;
}

export type ContainerAttr = "REEFER" | "OOG" | `IMO_CLASS_${number}`;

export interface SolutionProfile {
  name: string;
  label: string;
  w_makespan: number;
  w_penalty: number;
}

export interface Dsl {
  problem_class: string;
  version: string;
  extensions: string[];
  containers: DslContainer[];
  resources: {
    yard_cranes: { id: string; bay: number[] }[];
    ship_cranes: { id: string; bay: number[] }[];
    restricted_zones?: {
      reefer?: { bay: number; row: number; tier?: number }[];
      imo?: { bay: number; row?: number; rows?: number[] }[];
    };
  };
  config: YardSolverConfig;
  staff?: any[]; // ★ event_staffing用
  tasks?: any[]; // ★ event_staffing用
  events?: any[]; // ★ event_staffing用
}

export interface DslContainer {
  id: string;
  containerId?: string;
  operation_type?: "LOAD" | "DISCHARGE" | "MOVE" | "REHANDLE" | "PICK" | "PLACE";
  yard: { bay: number; row: number; tier: number };
  ship: { bay: number; row: number; tier: number };
  weight?: number;
  pod?: string;
  pol?: string;
  order?: number;
  attrs?: ContainerAttr[];
}

export interface YardSolverConfig {
  current_port?: string;
  current_event?: string; // ★ event_staffing用
  time_pick?: number;
  time_load?: number;
  time_discharge?: number;
  time_place?: number;
  time_move?: number;
  time_rehandle?: number;
  min_transit?: number;
  safety_gap: number;
  max_bays?: number;
  max_rows?: number;
  max_tiers?: number;
  max_vessel_bays?: number;
  max_vessel_rows?: number;
  max_vessel_tiers?: number;
  weight_bay_move?: number;
  weight_row_move?: number;
  weight_tier_factor?: number;
  weight_block_risk?: number;
  time_limit?: number;
  solution_profiles?: SolutionProfile[];
  crane_interference?: boolean;
  crane_safety_bays?: number;
  operation_start?: string | number; // ★ number を許容
  operation_date?: string;
  shifts?: { id: string; start: number; end: number; cranes: string[] }[];
  breaks?: { shift: string; start: number; duration: number }[];
  min_preference_satisfaction_rate?: number; // ★ event_staffing用
  preference_penalty_weight?: number; // ★ event_staffing用
}
