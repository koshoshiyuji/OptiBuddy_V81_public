// src/app/studio/types.ts
// Studio固有の型定義 + domain/types.ts からの再エクスポート

import type {
  Location, Task, SnapshotContainer, BayGrid, Snapshot, HighlightId,
  FixStrategy, IssueAction, Issue, ContainerAttr, SolutionProfile,
  Dsl, DslContainer, YardSolverConfig,
} from '../../domain/types';

export type {
  Location, Task, SnapshotContainer, BayGrid, Snapshot, HighlightId,
  FixStrategy, IssueAction, Issue, ContainerAttr, SolutionProfile,
  Dsl, DslContainer, YardSolverConfig,
};

export interface SolutionPlan {
  name: string;
  label?: string;
  tasks: Task[];
  makespan: number;
  penalty?: number;
  feasible?: boolean;
  ui_dsl?: any;  // 4DSLドメイン用（CVRP等）
}

// 既知のモード（IDE補完が効く）
export type KnownStudioMode =
  | 'overview' | 'timeline' | 'spatial' | 'staff' | 'task'
  | 'kitchen' | 'kitchen_params' | 'planner' | 'bin_packing'
  | 'issues' | 'register' | 'infeasible' | 'optimize';

// 新規ドメインを動的に受け入れる型（完全自動化）
// string & {} は TypeScript のトリック：任意の文字列を受け入れつつ、Union型の補完を維持
export type StudioMode = KnownStudioMode | (string & {});

export interface StudioControllerProps {
  initialTasks: Task[];
  initialInputData: Dsl;
  scenarioLabel?: string | null;
  onBackToHome: () => void;
  aiSuggestions?: any[];
  applyingSuggestionId?: string | null;
  handleApplySuggestion?: (suggestion: any) => void;
}

export interface StudioState {
  scenarioLabel: string | null;
  yardBays: number[];
  shipBays: number[];
  tasks: Task[];
  currentTime: number;
  setCurrentTime: (t: number) => void;
  currentTimeMin: number;
  setCurrentTimeMin: (min: number) => void;
  currentTimeHHMM: string;
  setCurrentTimeHHMM: (hhmm: string) => void;
  operationStartMin: number;
  operationStartHHMM: string;
  setOperationStartHHMM: (hhmm: string) => void;
  makespan: number;
  highlightId: HighlightId;
  setHighlightId: (h: HighlightId) => void;
  liveViewData: Record<string, SnapshotContainer>;
  timePoints: number[];
  selectedYardBay: number;
  setSelectedYardBay: (b: number) => void;
  selectedShipBay: number;
  setSelectedShipBay: (b: number) => void;
  solutions: SolutionPlan[];
  selectedSolutionIndex: number;
  setSelectedSolutionIndex: (i: number) => void;
  allIssues: Issue[];
  issueActions: Record<string, IssueAction>;
  handleApplyFix: (issue: Issue, fixMode: 'FIX' | 'ACCEPT') => void;
  handleBulkAccept: (issues: Issue[]) => void;
  handleBulkFix: (issues: Issue[]) => void;
  patchedDsls: Record<number, Dsl>;
  isSolverLoading: boolean;
  solverErrorMessage: string | null;
  handleRunSolver: () => void;
  handleReset: () => void;
  handleExportCsv: () => void;
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  searchHitIds: Set<string>;
  isOverIssuePanel: boolean;
  setIsOverIssuePanel: (v: boolean) => void;
  mousePos: { x: number; y: number };
  mode: StudioMode;
  setMode: (m: StudioMode) => void;
  initialInputData: Dsl;
  handleApplyAndReoptimize: (patchedDsl: Dsl) => Promise<{ feasible: boolean }>;
  onBackToHome: () => void;
  // 2026-07-24追加: 非同期解チェッカー（DESIGN_2026-07-21 3-3節、
  // HANDOFF_2026-07-24実装）のポーリング状態。/baselineのレスポンスに
  // async_check_job_id が含まれていた間だけ true になる。
  isCheckingDeferred: boolean;
}
