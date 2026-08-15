import { useState } from 'react';
import { OverviewView } from './views/OverviewView.tsx';
import { TimelineView } from './views/TimelineView.tsx';
import { SpatialView } from './views/SpatialView.tsx';
import { IssuesView } from './views/IssuesView.tsx';
import { InfeasibleView } from './views/InfeasibleView.tsx';
import { StudioTopBar } from './components/StudioTopBar.tsx';
import { StudioModeTabs } from './components/StudioModeTabs.tsx';
import { ContainerTooltip } from './components/ContainerTooltip.tsx';
import { AskPanel } from './components/AskPanel.tsx';
import { SolvingOverlay } from './components/SolvingOverlay.tsx';
import { useStudioState } from './useStudioState.ts';
import type { StudioControllerProps } from './types.ts';
import { Generic4DSLView } from './views/Generic4DSLView.tsx';

const ROOT_STYLE: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', height: '100vh', width: '100vw',
  gap: '10px', padding: '10px', background: '#08080a', color: '#eee',
  boxSizing: 'border-box', overflow: 'hidden',
};

const CONTENT_STYLE: React.CSSProperties = {
  flex: 1, minHeight: 0, overflow: 'auto', display: 'flex', flexDirection: 'column',
};

// 既知ドメイン（専用ビューを持つドメイン）一覧。
// これ以外の problem_class は「未知ドメイン」として扱い、
// YardPlanning固有のOverviewView/タブを出さず、汎用のGeneric4DSLViewに一本化する。
// 2026-07-11: 基底パターンをTruckDispatcher/NurseShiftの2枚看板に整理する方針により、
// EventStaffing/GhostKitchen/ProjectPlanner/BinPacking/ProductionLotSchedulerの専用ビューは
// 削除済み。TruckDispatcher/NurseShiftはどちらも専用Viewを持たず、最初からGeneric4DSLView
// を使う方針なのでここには加えない。
const KNOWN_PROBLEM_CLASSES = [
  'YardPlanning', 'RCPSP',
];

const KNOWN_MODES = [
  'overview', 'timeline', 'spatial', 'issues', 'infeasible',
];

// AskPanel（右下チャット）が開いている間にメインコンテンツへ確保する右マージン。
// AskPanelのMODAL_STYLE（width:400）+ PANEL_STYLEのright:24 + 余白。
const CHAT_PANEL_RESERVED_WIDTH = 440;

export function StudioShell(props: StudioControllerProps) {
  const studio = useStudioState(props);
  // 2026-07-24追加: AskPanelは position:fixed の固定オーバーレイで、開いた状態でも
  // メインコンテンツの幅を自動では詰めない。そのままだと緩和案カードの文章などが
  // チャットパネルの下に隠れて読めなくなる（「表示エリアが狭い」報告の原因）ため、
  // 開閉状態を受け取ってコンテンツ側に右マージンを確保する。
  const [isChatOpen, setIsChatOpen] = useState(false);
  const dslContainers = studio.initialInputData?.containers ?? [];
  const problemClass =
    (studio.initialInputData as { metadata?: { problem_class?: string }; problem_class?: string })
      .metadata?.problem_class ??
    (studio.initialInputData as { problem_class?: string }).problem_class;

  const isUnknownDomain = !!problemClass && !KNOWN_PROBLEM_CLASSES.includes(problemClass);

  return (
    <div style={ROOT_STYLE}>
      <StudioTopBar studio={studio} />
      <StudioModeTabs
        mode={studio.mode}
        onModeChange={studio.setMode}
        issueCount={studio.allIssues.length}
        problemClass={problemClass}
      />

      {studio.solverErrorMessage && (
        <div style={{
          flexShrink: 0, background: 'rgba(255, 68, 68, 0.2)',
          border: '1px solid #ff4444', padding: '8px 15px', borderRadius: '6px',
        }}>
          <span style={{ fontSize: '12px', color: '#ff4444', fontWeight: 'bold' }}>
            ⚠️ {studio.solverErrorMessage}
          </span>
        </div>
      )}

      <div style={{
        flex: 1, minHeight: 0, position: 'relative', display: 'flex',
        marginRight: isChatOpen ? CHAT_PANEL_RESERVED_WIDTH : 0,
        transition: 'margin-right 0.2s',
      }}>
        <div style={{
          ...CONTENT_STYLE,
          opacity: studio.isSolverLoading ? 0.3 : 1,
          pointerEvents: studio.isSolverLoading ? 'none' : 'auto',
          transition: 'opacity 0.2s',
        }}>
          {/* 既知のモード（個別ビュー）。ただし未知ドメインの場合は overview もYardPlanning専用表示を出さず汎用ビューに一本化する */}
          {studio.mode === 'overview'   && (isUnknownDomain
            ? <Generic4DSLView studio={studio} />
            : <OverviewView   studio={studio} />)}
          {studio.mode === 'timeline'   && <TimelineView   studio={studio} />}
          {studio.mode === 'spatial'    && <SpatialView    studio={studio} />}
          {studio.mode === 'issues'     && <IssuesView     studio={studio} />}
          {studio.mode === 'infeasible' && <InfeasibleView studio={studio} />}

          {/* 旧専用ビュー（CapacitatedVehicleRoutingProblemView、EventStaffing/GhostKitchen/
              ProjectPlanner/BinPacking/ProductionLotSchedulerの各専用ビュー、RegisterView/
              OptimizeView/TaskViewの2026-07-13削除分）は削除済み。
              TruckDispatcher/NurseShiftは下のGeneric4DSLViewによって自動的に表示される。 */}

          {/* 未知のモード（汎用4DSLView）。overviewは上で分岐済みのためここでは除外 */}
          {studio.mode !== 'overview' && !KNOWN_MODES.includes(studio.mode as string) && (
            <Generic4DSLView studio={studio} />
          )}
        </div>

        {/* 2026-07-24追加: solve中に「固まっているのか進捗しているのか分からない」を解消する
            ローディングオーバーレイ。opacityで薄暗くなる上の内側divとは別階層に置き、
            スピナー自体は薄暗くならず視認できるようにする */}
        <SolvingOverlay visible={studio.isSolverLoading} />
      </div>

      {studio.mode !== 'issues' &&
        !studio.isOverIssuePanel &&
        studio.highlightId.kind === 'active' &&
        studio.highlightId.hoverContainerId && (
          <ContainerTooltip
            containerId={studio.highlightId.hoverContainerId}
            tasks={studio.tasks}
            liveViewData={studio.liveViewData}
            pos={studio.mousePos}
            currentTime={studio.currentTime}
            dslContainers={dslContainers}
          />
        )}

      <AskPanel
        dsl={studio.initialInputData}
        solution={{
          tasks:     studio.tasks,
          makespan:  studio.makespan,
          issues:    studio.allIssues,
          solutions: studio.solutions,
        }}
        onApplyAndReoptimize={studio.handleApplyAndReoptimize}
        initialQuestion={(studio as any).askPrefill?.text}
        openSignal={(studio as any).askPrefill?.nonce}
        onOpenChange={setIsChatOpen}
      />
    </div>
  );
}
