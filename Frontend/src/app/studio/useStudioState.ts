import { useReducer, useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { detectIssues } from '../../ui/detectIssues.ts';
import type { Dsl, HighlightId, Issue, IssueAction, Task } from '../../domain/types.ts';
import { SOLVER_CONFIG, VESSEL_CONFIG, YARD_CONFIG } from '../../domain/constants.ts';
import { buildLiveViewData } from '../../domain/snapshotAtTime.ts';
import type { SolutionPlan, StudioControllerProps, StudioMode, StudioState } from './types.ts';
import { minutesToHHMM, HHMMToMinutes, parseOperationStart } from '../../utils/timeUtils.ts';
import i18n from '../../i18n/index.ts';

// 2026-07-30 i18n対応: /baseline へのリクエストに現在の表示言語を
// クエリパラメータで付与するためのヘルパー。Backend側はcontextvars経由で
// これを参照し、動的メッセージ（issue/KPIラベル等）を解決する
// （DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md 3節）。
function withLangParam(url: string): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}lang=${i18n.language}`;
}

const SESSION_KEY = 'optibuddy_session';

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const entries = Object.keys(value as Record<string, unknown>)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`);
  return `{${entries.join(',')}}`;
}

function getDslSessionId(dsl: Dsl): string {
  const explicitId = (dsl as Dsl & { id?: string }).id;
  if (explicitId) return explicitId;
  const hash = stableStringify(dsl).split('').reduce((acc, char) => (acc * 31 + char.charCodeAt(0)) >>> 0, 0);
  return `custom_${hash.toString(36)}`;
}

// ─────────────────────────────────────────────
// reducer
// ─────────────────────────────────────────────
interface SolverState {
  tasks: Task[];
  makespan: number;
  solutions: SolutionPlan[];
  selectedSolutionIndex: number;
  backendIssues: Issue[];
  issueActions: Record<string, IssueAction>;
  isSolverLoading: boolean;
  solverErrorMessage: string | null;
  patchedDsls: Record<number, Dsl>;
}

type SolverAction =
  | { type: 'SOLVER_START' }
  | { type: 'SOLVER_SUCCESS'; solutions: SolutionPlan[]; issues: Issue[] }
  | { type: 'SOLVER_ERROR'; message: string }
  | { type: 'SELECT_SOLUTION'; index: number }
  | { type: 'APPLY_FIX'; issueId: string; fixMode: IssueAction }
  | { type: 'BULK_ACCEPT'; issueIds: string[] }
  | { type: 'BULK_FIX'; issueIds: string[] }
  | { type: 'APPEND_SOLUTIONS'; solutions: SolutionPlan[]; issues: Issue[]; patchedDsl: Dsl }
  | { type: 'RESTORE_SESSION'; solutions: SolutionPlan[]; issueActions: Record<string, IssueAction> }
  | { type: 'RESET'; initialTasks: Task[] }
  // 2026-07-24追加: 非同期解チェッカー（DESIGN_2026-07-21 3-3節）が
  // /checks/status/<job_id> 経由で後から見つけたissueをbackendIssuesに追記する。
  // 既存のsolutions/tasksには一切触れない（「警告追記のみ」方針、HANDOFF_2026-07-24参照）。
  | { type: 'APPEND_DEFERRED_ISSUES'; issues: Issue[] };

function solverReducer(state: SolverState, action: SolverAction): SolverState {
  switch (action.type) {
    case 'SOLVER_START':
      return { ...state, isSolverLoading: true, solverErrorMessage: null };
    case 'SOLVER_SUCCESS': {
      // solutions[0]でなく、最初のfeasibleなPlanを初期表示に使う。
      // Plan AがinfeasibleでもPlan B/Cがfeasibleなら正しく表示できる。
      const firstFeasibleIdx = action.solutions.findIndex((s) => s.feasible !== false);
      const initialIdx = firstFeasibleIdx >= 0 ? firstFeasibleIdx : 0;
      const initialSol = action.solutions[initialIdx];
      return { ...state, isSolverLoading: false, solutions: action.solutions,
        selectedSolutionIndex: initialIdx,
        tasks: initialSol?.tasks ?? state.tasks,
        makespan: initialSol?.makespan ?? state.makespan,
        backendIssues: action.issues, solverErrorMessage: null };
    }
    case 'SOLVER_ERROR':
      return { ...state, isSolverLoading: false, solverErrorMessage: action.message };
    case 'SELECT_SOLUTION': {
      const sol = state.solutions[action.index];
      if (!sol) return state;
      // CVRP等tasksベースでないドメインのPlanには tasks/makespan が存在しない。
      // undefined で上書きすると他コンポーネントがクラッシュ（画面白化）するため、
      // 存在する場合のみ更新し、無ければ既存値を維持する。
      return {
        ...state,
        selectedSolutionIndex: action.index,
        tasks: sol.tasks ?? state.tasks,
        makespan: sol.makespan ?? state.makespan,
      };
    }
    case 'APPLY_FIX':
      return { ...state, issueActions: { ...state.issueActions, [action.issueId]: action.fixMode } };
    case 'BULK_ACCEPT': {
      const next = { ...state.issueActions };
      action.issueIds.forEach((id) => { next[id] = 'ACCEPT'; });
      return { ...state, issueActions: next };
    }
    case 'BULK_FIX': {
      const next = { ...state.issueActions };
      action.issueIds.forEach((id) => { next[id] = 'FIX'; });
      return { ...state, issueActions: next };
    }
    case 'APPEND_SOLUTIONS': {
      const planLetterStart = state.solutions.length;
      const renamed = action.solutions.map((s, i) => ({
        ...s,
        name: `Plan ${String.fromCharCode(65 + planLetterStart + i)}`,
        label: `Plan ${String.fromCharCode(65 + planLetterStart + i)} (Patched)`,
      }));
      const merged = [...state.solutions, ...renamed];
      const newIndex = planLetterStart;
      const newPatchedDsls = { ...state.patchedDsls };
      renamed.forEach((_, i) => { newPatchedDsls[planLetterStart + i] = action.patchedDsl; });
      return { ...state, isSolverLoading: false, solutions: merged, selectedSolutionIndex: newIndex,
        tasks: renamed[0]?.tasks ?? state.tasks, makespan: renamed[0]?.makespan ?? state.makespan,
        backendIssues: action.issues, solverErrorMessage: null, patchedDsls: newPatchedDsls };
    }
    case 'RESTORE_SESSION':
      return { ...state, solutions: action.solutions, selectedSolutionIndex: 0,
        tasks: action.solutions[0]?.tasks ?? state.tasks,
        makespan: action.solutions[0]?.makespan ?? state.makespan,
        issueActions: action.issueActions };
    case 'RESET':
      return { ...state, tasks: action.initialTasks, solutions: [], selectedSolutionIndex: 0,
        makespan: 0, backendIssues: [], issueActions: {}, solverErrorMessage: null,
        isSolverLoading: false, patchedDsls: {} };
    case 'APPEND_DEFERRED_ISSUES': {
      if (action.issues.length === 0) return state;
      const existingIds = new Set(state.backendIssues.map((i) => i.id));
      const newOnes = action.issues.filter((i) => !existingIds.has(i.id));
      if (newOnes.length === 0) return state;
      return { ...state, backendIssues: [...state.backendIssues, ...newOnes] };
    }
    default:
      return state;
  }
}

function loadSession(dslId: string): {
  solutions: SolutionPlan[]; issueActions: Record<string, IssueAction>;
  selectedSolutionIndex: number; backendIssues: Issue[];
} | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (saved.dslId === dslId && saved.solutions?.length > 0) {
      const selectedSolutionIndex = Number.isInteger(saved.selectedSolutionIndex)
        ? Math.min(Math.max(saved.selectedSolutionIndex, 0), saved.solutions.length - 1) : 0;
      return { solutions: saved.solutions, issueActions: saved.issueActions ?? {},
        selectedSolutionIndex, backendIssues: saved.backendIssues ?? [] };
    }
  } catch { /* ignore */ }
  return null;
}

function saveSession(dslId: string, solutions: SolutionPlan[], issueActions: Record<string, IssueAction>,
  selectedSolutionIndex = 0, backendIssues: Issue[] = []) {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(
      { dslId, solutions, issueActions, selectedSolutionIndex, backendIssues, savedAt: new Date().toISOString() }));
  } catch { /* ignore */ }
}

function clearSession() {
  try { localStorage.removeItem(SESSION_KEY); } catch { /* ignore */ }
}

// ─────────────────────────────────────────────
// hook 本体
// ─────────────────────────────────────────────
export function useStudioState({
  initialTasks, initialInputData, scenarioLabel, onBackToHome,
}: StudioControllerProps): StudioState {

  const yardBays = useMemo(() => {
    const bays = (initialInputData.resources?.yard_cranes ?? [])
      .flatMap((c: { bay: number[] }) => c.bay).filter((b: number) => b > 0);
    const unique = Array.from(new Set(bays)).sort((a, b) => a - b);
    return unique.length > 0 ? unique : [...YARD_CONFIG.BAYS];
  }, [initialInputData]);

  const shipBays = useMemo(() => {
    const bays = (initialInputData.resources?.ship_cranes ?? [])
      .flatMap((c: { bay: number[] }) => c.bay).filter((b: number) => b > 0);
    const unique = Array.from(new Set(bays)).sort((a, b) => a - b);
    return unique.length > 0 ? unique : [...VESSEL_CONFIG.BAYS];
  }, [initialInputData]);

  const [operationStartMin, setOperationStartMin] = useState(() => {
    const raw = (initialInputData.config as { operation_start?: string | number }).operation_start;
    return raw == null ? parseOperationStart('08:00') : parseOperationStart(raw);
  });

  const dslId = useMemo(() => getDslSessionId(initialInputData), [initialInputData]);
  const initialSession = useMemo(() => loadSession(dslId), [dslId]);
  const initialSelectedSolutionIndex = initialSession?.selectedSolutionIndex ?? 0;
  const initialBackendIssues = initialSession?.backendIssues ?? [];

  const [solverState, dispatch] = useReducer(solverReducer, {
    tasks: initialSession?.solutions[initialSelectedSolutionIndex]?.tasks ?? initialTasks,
    makespan: initialSession?.solutions[initialSelectedSolutionIndex]?.makespan ?? 0,
    solutions: initialSession?.solutions ?? [],
    selectedSolutionIndex: initialSelectedSolutionIndex,
    backendIssues: initialBackendIssues,
    issueActions: initialSession?.issueActions ?? {},
    isSolverLoading: false, solverErrorMessage: null, patchedDsls: {},
  });

  const { tasks, makespan, solutions, selectedSolutionIndex,
    backendIssues, issueActions, isSolverLoading, solverErrorMessage } = solverState;

  // 2026-07-24追加: 非同期解チェッカー（DESIGN_2026-07-21 3-3節）のポーリング状態。
  const [isCheckingDeferred, setIsCheckingDeferred] = useState(false);
  // solveが再実行されるたびに世代を進め、古いsolveに紐づくポーリングが
  // 後から結果を返してもbackendIssuesを汚染しないようにする（レース対策）。
  const pollGenerationRef = useRef(0);

  const [mode, setMode] = useState<StudioMode>('overview');
  const [currentTime, setCurrentTime] = useState(0);
  const [highlightId, setHighlightId] = useState<HighlightId>({ kind: 'none' });
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [selectedYardBay, setSelectedYardBay] = useState(yardBays[0]);
  const [selectedShipBay, setSelectedShipBay] = useState(shipBays[0]);
  const [isOverIssuePanel, setIsOverIssuePanel] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  // CVRP専用: Backendから返ったUI DSLを保持する
  const [cvrpUiDsl, setCvrpUiDsl] = useState<any>(null);
  // AskPanelを外部（制約見直しタブ等）から開いてプリフィルするためのトリガー。
  // nonceを毎回更新することで、同じテキストでも useEffect が発火するようにする。
  const [askPrefill, setAskPrefill] = useState<{ text: string; nonce: number } | null>(null);

  const currentTimeMin = useMemo(() => Math.floor(currentTime / 60), [currentTime]);
  const currentTimeHHMM = useMemo(() => minutesToHHMM(operationStartMin + currentTimeMin), [currentTimeMin, operationStartMin]);
  const operationStartHHMM = useMemo(() => minutesToHHMM(operationStartMin), [operationStartMin]);

  const setCurrentTimeMin = useCallback((min: number) => { setCurrentTime(Math.max(0, min * 60)); }, []);
  const setCurrentTimeHHMM = useCallback((hhmm: string) => {
    const absMin = HHMMToMinutes(hhmm);
    setCurrentTime(Math.max(0, absMin - operationStartMin) * 60);
  }, [operationStartMin]);
  const setOperationStartHHMM = useCallback((hhmm: string) => { setOperationStartMin(HHMMToMinutes(hhmm)); }, []);

  const searchHitIds = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return new Set<string>();
    return new Set(Array.from(new Set(tasks.map((t) => t.containerId))).filter((id) => id.toLowerCase().includes(q)));
  }, [searchQuery, tasks]);

  const handleSelectSolution = useCallback((index: number) => {
    dispatch({ type: 'SELECT_SOLUTION', index });
    setCurrentTime(0);
    // CVRP等tasksベースでないドメイン: Plan切り替時に選択されたPlanのui_dslも連動更新する。
    // ui_dslが無いPlan（YardPlanning等）に切り替えた場合は、直前のcvrpUiDslをそのまま残す
    // （undefinedで上書きすると描画中コンポーネントがクラッシュし白画面になるため）。
    const selectedSol = solutions[index] as { ui_dsl?: unknown } | undefined;
    if (selectedSol?.ui_dsl) setCvrpUiDsl(selectedSol.ui_dsl);

    if (solutions.length > 0) saveSession(dslId, solutions, issueActions, index, backendIssues);
  }, [solutions, issueActions, dslId, backendIssues]);


  const allIssues = useMemo(() => {
    const local = detectIssues(tasks);
    const combined = [...local];
    backendIssues.forEach((bi) => { if (!combined.find((ci) => ci.id === bi.id)) combined.push(bi); });
    return combined;
  }, [tasks, backendIssues]);

  const handleApplyFix = useCallback((issue: Issue, fixMode: 'FIX' | 'ACCEPT') => {
    const nextIssueActions = { ...issueActions, [issue.id]: fixMode };
    dispatch({ type: 'APPLY_FIX', issueId: issue.id, fixMode });
    if (solutions.length > 0) saveSession(dslId, solutions, nextIssueActions, selectedSolutionIndex, backendIssues);
  }, [issueActions, solutions, dslId, selectedSolutionIndex, backendIssues]);

  const handleBulkAccept = useCallback((issues: Issue[]) => {
    const issueIds = issues.map((i) => i.id);
    dispatch({ type: 'BULK_ACCEPT', issueIds });
    const nextIssueActions = { ...issueActions };
    issueIds.forEach((id) => { nextIssueActions[id] = 'ACCEPT'; });
    if (solutions.length > 0) saveSession(dslId, solutions, nextIssueActions, selectedSolutionIndex, backendIssues);
  }, [issueActions, solutions, dslId, selectedSolutionIndex, backendIssues]);

  // OverviewView の IssueMonitorPanel「全てFIX」ボタン用（2026-07-13追加）。
  // handleBulkAccept と同じ形だが、issueActions を 'ACCEPT' ではなく 'FIX' にする。
  const handleBulkFix = useCallback((issues: Issue[]) => {
    const issueIds = issues.map((i) => i.id);
    dispatch({ type: 'BULK_FIX', issueIds });
    const nextIssueActions = { ...issueActions };
    issueIds.forEach((id) => { nextIssueActions[id] = 'FIX'; });
    if (solutions.length > 0) saveSession(dslId, solutions, nextIssueActions, selectedSolutionIndex, backendIssues);
  }, [issueActions, solutions, dslId, selectedSolutionIndex, backendIssues]);

  useEffect(() => {
    const handler = (e: MouseEvent) => setMousePos({ x: e.clientX, y: e.clientY });
    window.addEventListener('mousemove', handler);
    return () => window.removeEventListener('mousemove', handler);
  }, []);

  const liveViewData = useMemo(
    () => buildLiveViewData(tasks, currentTime, initialInputData.containers ?? [], initialInputData.config),
    [tasks, currentTime, initialInputData.containers, initialInputData.config]
  );

  // 2026-07-24追加: 非同期解チェッカー（DESIGN_2026-07-21 3-3節、
  // HANDOFF_2026-07-24実装）のポーリング。/baselineが async_check_job_id を返した
  // 場合のみ呼ばれる（O(n²)チェックが閾値を超えて非同期に回された、稀なケース）。
  // GET /checks/status/<job_id> を一定間隔でポーリングし、stage="done"になったら
  // job.issues（各issueに_deferred_check=trueが付与済み）をbackendIssuesに追記する。
  // 「警告追記のみ」方針のため、既存のsolutions/tasks/issueActionsは一切書き換えない。
  const pollDeferredCheck = useCallback((jobId: string, generation: number) => {
    const POLL_INTERVAL_MS = 2000;
    const MAX_POLLS = 150; // 上限5分（2秒×150回）。張り付き防止の安全弁。
    setIsCheckingDeferred(true);

    let pollCount = 0;
    const poll = async () => {
      // 別のsolveが既に始まっていたら、この世代のポーリングは静かに打ち切る
      // （古いjobの結果が新しいsolveの画面を汚染しないようにする）。
      if (pollGenerationRef.current !== generation) return;

      pollCount += 1;
      try {
        const res = await fetch(`${SOLVER_CONFIG.API_BASE}/checks/status/${jobId}`);
        if (!res.ok) throw new Error(`checks/status API Error: ${res.status}`);
        const body = await res.json();
        const job = body?.job ?? {};

        if (job.stage === 'done') {
          if (pollGenerationRef.current === generation) {
            dispatch({ type: 'APPEND_DEFERRED_ISSUES', issues: job.issues ?? [] });
            setIsCheckingDeferred(false);
          }
          return;
        }
        if (job.stage === 'error') {
          console.warn(`[deferred check ${jobId}] failed: ${job.error}`);
          if (pollGenerationRef.current === generation) setIsCheckingDeferred(false);
          return;
        }
        // stage === 'running' など: 継続してポーリング
        if (pollCount >= MAX_POLLS) {
          console.warn(`[deferred check ${jobId}] gave up polling after ${MAX_POLLS} attempts`);
          if (pollGenerationRef.current === generation) setIsCheckingDeferred(false);
          return;
        }
        window.setTimeout(poll, POLL_INTERVAL_MS);
      } catch (err) {
        console.warn(`[deferred check ${jobId}] polling error:`, err);
        if (pollGenerationRef.current === generation) setIsCheckingDeferred(false);
      }
    };

    void poll();
  }, []);

  const buildSolverIssues = useCallback(
    (result: { issues?: Issue[]; skipped?: string[]; skip_reasons?: Record<string, string> }): Issue[] => {
      const skippedIds = result.skipped ?? [];
      const skipReasons = result.skip_reasons ?? {};
      return (result.issues ?? []).map((issue: Issue & { skipped?: boolean }) => ({
        ...issue, skipped: skippedIds.includes(issue.id), skip_reason: skipReasons[issue.id] ?? '',
      }));
    }, []
  );

  const getBackendErrorMessage = useCallback((result: any): string => {
    if (typeof result?.message === 'string' && result.message.trim() !== '') return result.message;
    if (Array.isArray(result?.issues) && result.issues.length > 0) {
      const details = result.issues.map((i: any) => i.description || i.title || i.message)
        .filter((t: any) => typeof t === 'string' && t.trim() !== '');
      if (details.length > 0) return details.join(' / ');
    }
    return typeof result?.status === 'string' ? `Solver returned status: ${result.status}` : 'Unknown error';
  }, []);

  const didRunSolver = useRef(false);

  const handleRunSolver = useCallback(async () => {
    if (isSolverLoading) return;
    dispatch({ type: 'SOLVER_START' });
    // 新しいsolveを開始するたびに世代を進め、前回solveに紐づく非同期チェックの
    // ポーリングが結果を汚染しないようにする（pollDeferredCheck参照）。
    pollGenerationRef.current += 1;
    const generation = pollGenerationRef.current;
    try {
      const response = await fetch(withLangParam(`${SOLVER_CONFIG.API_BASE}/baseline`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dsl: initialInputData, tasks, issueActions,
          containers: (initialInputData.containers ?? []).map((c) => ({
            containerId: c.id, currentPos: 'YARD', yard: c.yard, ship: { bay: 0, row: 0, tier: 0 },
          })),
        }),
      });
      if (!response.ok) throw new Error(`Solver API Error: ${response.status}`);
      const result = await response.json();

      // 2026-07-24追加: 解チェッカー（O(n²)以上）が閾値超過で非同期に回った場合のみ
      // async_check_job_id が返る。通常のsolveでは何もしない（HANDOFF_2026-07-24参照）。
      if (result.async_check_job_id) pollDeferredCheck(result.async_check_job_id, generation);

      // ── 汎用4DSLドメイン検知（ui_dsl.domain ベース）──
      const uiDslDomain = result.ui_dsl?.domain as string | undefined;
      if (uiDslDomain) {
        const issues = buildSolverIssues(result);
        const rawSolutions = result.solutions ?? [];
        const firstFeasibleIdx = rawSolutions.findIndex((s: any) => s.feasible !== false);
        const displaySolIdx = firstFeasibleIdx >= 0 ? firstFeasibleIdx : 0;
        const displaySol = rawSolutions[displaySolIdx];
        const uiDslForDisplay = displaySol?.ui_dsl ?? result.ui_dsl;
        
        dispatch({ type: 'SOLVER_SUCCESS', solutions: rawSolutions, issues });
        setCvrpUiDsl(uiDslForDisplay);  // 汎用化: 全ドメインで使用
        const feasible = firstFeasibleIdx >= 0;
        // ui_dsl.domain の値をそのままモード名として使用（完全自動化）
        setMode(feasible ? uiDslDomain as StudioMode : 'infeasible');
        return;
      }

      // ── problem_class ベースのフォールバック（ui_dsl.domain がない場合）──
      const pc = result.problem_class as string | undefined;
      if (pc === 'ProductionLotScheduler') {
        const issues = buildSolverIssues(result);
        const rawSolutions = result.solutions ?? [];
        dispatch({ type: 'SOLVER_SUCCESS', solutions: rawSolutions, issues });
        if (rawSolutions.length === 0 || rawSolutions[0]?.feasible === false) {
          setMode('infeasible');
        } else {
          setMode('production_lot_scheduler');
        }
        return;
      }

      // ── 通常フロー（YardPlanning 等）──
      if (result.status === 'ok') {
        const newSolutions: SolutionPlan[] = result.solutions ?? [];
        const issues = buildSolverIssues(result);
        dispatch({ type: 'APPEND_SOLUTIONS', solutions: newSolutions, issues, patchedDsl: initialInputData });
        setCurrentTime(0);
        // 2026-08-11修正: 従来はissueのid==='solve_failed'の有無だけで infeasible 画面
        // への切り替えを判定していたが、これは「msol自体が見つからない」全面的な
        // 求解失敗のみを想定した判定で、meeting_room等「一部の割当だけ諦めて部分解を
        // 返す」設計のソルバー（result.feasible=falseだが個別のunassigned_*issueで
        // 表現される）を取りこぼしていた（実機検証、2026-08-11発覚。meeting_room の
        // infeasibleシナリオがInfeasible画面に切り替わらずOverview/Issues画面のまま
        // 表示される退行があった）。既に420行目付近の4DSLパスで使っている
        // 「result.solutions のfeasible値ベースの判定」と同じ考え方に揃え、
        // トップレベルのresult.feasible===falseも trigger に含める。
        const hasSolveFailed =
          result.feasible === false ||
          issues.some((i) => i.id === 'solve_failed' || (i as any).type === 'SOLVE_FAILED');
        if (hasSolveFailed) setMode('infeasible');
      } else {
        const errorIssues = result.issues ?? [];
        const hasSolveFailedInError = errorIssues.some(
          (i: any) => i.id === 'solve_failed' || i.type === 'SOLVE_FAILED'
        );
        if (hasSolveFailedInError) {
          dispatch({ type: 'APPEND_SOLUTIONS', solutions: [], issues: errorIssues, patchedDsl: initialInputData });
          setMode('infeasible');
        } else {
          dispatch({ type: 'SOLVER_ERROR', message: getBackendErrorMessage(result) });
        }
      }
    } catch (err) {
      dispatch({ type: 'SOLVER_ERROR', message: `Optimization Failed ${err}` });
    }
  }, [isSolverLoading, initialInputData, tasks, issueActions, buildSolverIssues, getBackendErrorMessage, pollDeferredCheck]);

  const handleRunSolverRef = useRef(handleRunSolver);
  useEffect(() => { handleRunSolverRef.current = handleRunSolver; });

  useEffect(() => {
    if (didRunSolver.current) return;
    didRunSolver.current = true;
    if (!initialSession) void handleRunSolverRef.current();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleExportCsv = useCallback(() => {
    const SEC = 60;
    const _pc = (initialInputData as any).metadata?.problem_class ?? (initialInputData as any).problem_class;
    const isStaffing = _pc === 'EventStaffing' || _pc === 'NurseShiftWeeklyCap';
    const taskCsv = isStaffing ? [
      'staff_id,staff_name,task_id,task_name,start_min,end_min,duration_min,hourly_rate,task_cost',
      ...tasks.map((t) => {
        const st = t as any;
        const dur = Math.round((t.end - t.start) / SEC);
        return [st.staff_id ?? '', st.staff_name ?? '', st.task_id ?? t.containerId ?? '',
          st.task_name ?? '', Math.round(t.start / SEC), Math.round(t.end / SEC),
          dur, st.hourly_rate ?? '', st.cost ?? ''].join(',');
      }),
    ].join('\n') : [
      'containerId,operation,resource,start_min,end_min,duration_min',
      ...tasks.map((t) => `${t.containerId},${t.operation},${t.resource ?? ''},${Math.round(t.start / SEC)},${Math.round(t.end / SEC)},${Math.round((t.end - t.start) / SEC)}`),
    ].join('\n');
    const issueCsv = ['id,severity,title,status', ...allIssues.map((i) => {
      const action = issueActions[i.id] || 'NONE';
      const status = (i as any).skipped ? 'SKIPPED' : action !== 'NONE' ? action : 'UNRESOLVED';
      return `${i.id},${i.severity},"${i.title}",${status}`;
    })].join('\n');
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 16);
    const dl = (filename: string, content: string, delay: number) => window.setTimeout(() => {
      const a = Object.assign(document.createElement('a'), {
        href: URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8;' })), download: filename,
      });
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }, delay);
    dl(`optibuddy_${isStaffing ? 'staffing' : 'tasks'}_${ts}.csv`, taskCsv, 0);
    dl(`optibuddy_issues_${ts}.csv`, issueCsv, 300);
    const currentDsl = solverState.patchedDsls[selectedSolutionIndex] ?? initialInputData;
    const dlJson = (filename: string, content: string, delay: number) => window.setTimeout(() => {
      const a = Object.assign(document.createElement('a'), {
        href: URL.createObjectURL(new Blob([content], { type: 'application/json' })), download: filename,
      });
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }, delay);
    dlJson(`optibuddy_dsl_${ts}.json`, JSON.stringify(currentDsl, null, 2), 600);
  }, [tasks, allIssues, issueActions, solverState.patchedDsls, selectedSolutionIndex, initialInputData]);

  const handleReset = useCallback(() => {
    if (window.confirm('RESET TO INITIAL PLAN?')) {
      dispatch({ type: 'RESET', initialTasks });
      setCurrentTime(0);
      setCvrpUiDsl(null);
      clearSession();
    }
  }, [initialTasks]);

  const handleApplyAndReoptimize = useCallback(async (patchedDsl: Dsl): Promise<{ feasible: boolean }> => {
    if (isSolverLoading) return { feasible: true };
    dispatch({ type: 'SOLVER_START' });
    pollGenerationRef.current += 1;
    const generation = pollGenerationRef.current;
    try {
      const response = await fetch(withLangParam(`${SOLVER_CONFIG.API_BASE}/baseline`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dsl: patchedDsl, tasks, issueActions,
          containers: (patchedDsl.containers ?? []).map((c) => ({
            containerId: c.id, currentPos: 'YARD', yard: c.yard, ship: { bay: 0, row: 0, tier: 0 },
          })),
        }),
      });
      if (!response.ok) throw new Error(`Solver API Error: ${response.status}`);
      const result = await response.json();

      // 2026-07-24追加: 解チェッカーの非同期分岐（handleRunSolver参照）。
      if (result.async_check_job_id) pollDeferredCheck(result.async_check_job_id, generation);

      // CVRP / 汎用4DSLドメイン パッチ再最適化
      const uiDslDomainPatch = result.ui_dsl?.domain as string | undefined;
      if (uiDslDomainPatch) {
        const issues = buildSolverIssues(result);
        const rawSolutions = result.solutions ?? [];
        const firstFeasibleIdx = rawSolutions.findIndex((s: any) => s.feasible !== false);
        const displaySolIdx = firstFeasibleIdx >= 0 ? firstFeasibleIdx : 0;
        const displaySol = rawSolutions[displaySolIdx];
        const uiDslForDisplay = displaySol?.ui_dsl ?? result.ui_dsl;
        dispatch({ type: 'SOLVER_SUCCESS', solutions: rawSolutions, issues });
        setCvrpUiDsl(uiDslForDisplay);
        const feasible = firstFeasibleIdx >= 0;
        setMode(feasible ? uiDslDomainPatch as StudioMode : 'infeasible');
        return { feasible };
      }

      if (result.status === 'ok') {
        const newSolutions: SolutionPlan[] = result.solutions ?? [];
        const issues = buildSolverIssues(result);
        dispatch({ type: 'APPEND_SOLUTIONS', solutions: newSolutions, issues, patchedDsl });
        setCurrentTime(0);
        const hasSolveFailed = issues.some((i) => i.id === 'solve_failed' || (i as any).type === 'SOLVE_FAILED');
        setMode(hasSolveFailed ? 'infeasible' : newSolutions.length > 0 ? 'issues' : 'overview');
        return { feasible: !hasSolveFailed };
      } else {
        dispatch({ type: 'SOLVER_ERROR', message: getBackendErrorMessage(result) });
        return { feasible: false };
      }
    } catch (err) {
      dispatch({ type: 'SOLVER_ERROR', message: `Optimization Failed ${err}` });
      return { feasible: false };
    }
  }, [isSolverLoading, tasks, issueActions, buildSolverIssues, getBackendErrorMessage, pollDeferredCheck]);

  const timePoints = useMemo(() => {
    const lastEndByContainer = tasks.reduce<Record<string, number>>((acc, task) => {
      const prev = acc[task.containerId] ?? 0;
      acc[task.containerId] = Math.max(prev, task.end);
      return acc;
    }, {});
    const raw = [0, ...Object.values(lastEndByContainer)];
    return Array.from(new Set(raw)).sort((a, b) => a - b);
  }, [tasks]);

  const handleModeChange = useCallback((newMode: StudioMode) => {
    setMode(newMode);
    setHighlightId({ kind: 'none' });
  }, []);

  // 制約見直しタブ等から AskPanel をプリフィル付きで開く。
  const requestAskPanel = useCallback((text: string) => {
    setAskPrefill({ text, nonce: Date.now() });
  }, []);

  return {
    yardBays, shipBays, tasks, currentTime, setCurrentTime,
    currentTimeMin, setCurrentTimeMin, currentTimeHHMM, setCurrentTimeHHMM,
    operationStartMin, operationStartHHMM, setOperationStartHHMM,
    makespan, highlightId, setHighlightId, liveViewData, timePoints,
    selectedYardBay, setSelectedYardBay, selectedShipBay, setSelectedShipBay,
    solutions, selectedSolutionIndex, setSelectedSolutionIndex: handleSelectSolution,
    allIssues, issueActions, handleApplyFix, handleBulkAccept, handleBulkFix,
    patchedDsls: solverState.patchedDsls,
    isSolverLoading, solverErrorMessage, handleRunSolver, handleReset, handleExportCsv,
    searchQuery, setSearchQuery, searchHitIds,
    isOverIssuePanel, setIsOverIssuePanel, mousePos,
    mode, setMode: handleModeChange,
    initialInputData, scenarioLabel: scenarioLabel ?? null, handleApplyAndReoptimize, onBackToHome,
    // CVRP専用（as anyでStudioStateを拡張）
    cvrpUiDsl,
    askPrefill, requestAskPanel,
    // 2026-07-24追加: 非同期解チェッカーのポーリング状態
    isCheckingDeferred,
  } as any;
}
