import { useMemo } from 'react';
import type { YardSolverConfig } from '../../../domain/types.ts';
import { TaskGantt } from '../../../ui/TaskGantt.tsx';
import type { StudioState } from '../types.ts';

type PickState = Pick<
  StudioState,
  | 'tasks'
  | 'makespan'
  | 'highlightId'
  | 'setHighlightId'
  | 'currentTime'
  | 'setCurrentTime'
  | 'initialInputData'
>;

export function GanttPanel({ studio, fill }: { studio: PickState; fill?: boolean }) {
  const { tasks, makespan, highlightId, setHighlightId, currentTime, setCurrentTime, initialInputData } = studio;

  // operationStartSec:
  //   シフト制約がある場合のみ operation_start を秒換算してオフセットに使う。
  //   シフトなしシナリオでは CP Optimizer が 0 起点で解を返すため 0 固定。
  //   operation_start はラベル表示のベースとして TaskGantt 内で使用する。
  const operationStartSec = useMemo(() => {
    const config = initialInputData?.config ?? ({} as Partial<YardSolverConfig>);
    const hasShifts = Array.isArray(config.shifts) && config.shifts.length > 0;

    // シフトなし → オフセット不要
    if (!hasShifts) return 0;

    // シフトあり → operation_start を秒換算してオフセットに使う
    const raw = config.operation_start as string | number | undefined;
    if (raw == null) return 0;
    if (typeof raw === 'number') return raw * 60; // 分→秒
    const parts = String(raw).split(':').map(Number);
    return ((parts[0] || 0) * 60 + (parts[1] || 0)) * 60;
  }, [initialInputData]);

  // ラベル表示用のベース分（オフセットに関わらず常に計算）
  const labelOffsetMin = useMemo(() => {
    const config = initialInputData?.config ?? ({} as Partial<YardSolverConfig>);
    const raw = config.operation_start as string | number | undefined;
    if (raw == null) return 0;
    if (typeof raw === 'number') return raw; // すでに分単位
    const parts = String(raw).split(':').map(Number);
    return (parts[0] || 0) * 60 + (parts[1] || 0);
  }, [initialInputData]);

  const resourceCount = Array.from(new Set(tasks.map((t) => t.resourceId || t.resource || 'Unknown'))).length;
  const LANE_HEIGHT = 65;
  const HEADER_HEIGHT = 30 + 16 + 16;
  const SLIDER_HEIGHT = 40;
  const ganttHeight = Math.min(
    Math.max(160, resourceCount * LANE_HEIGHT + HEADER_HEIGHT + SLIDER_HEIGHT),
    fill ? 800 : 360
  );

  return (
    <div
      style={{
        flex: fill ? 1 : undefined,
        flexShrink: fill ? 1 : 0,
        minHeight: fill ? 200 : undefined,
        height: fill ? undefined : `${ganttHeight}px`,
        background: '#121216',
        borderRadius: '10px',
        border: '1px solid #2a2a30',
        position: 'relative',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <TaskGantt
          tasks={tasks}
          makespan={makespan}
          highlightId={highlightId}
          onHighlightChange={setHighlightId}
          currentTime={currentTime}
          onTimeChange={setCurrentTime}
          shifts={(initialInputData.config as { shifts?: { id: string; start: number; end: number; cranes: string[] }[] })?.shifts}
          breaks={(initialInputData.config as { breaks?: { shift: string; start: number; duration: number }[] })?.breaks}
          operationStartSec={operationStartSec}
          labelOffsetMin={labelOffsetMin}
          problemClass={
            (initialInputData as { metadata?: { problem_class?: string }; problem_class?: string })
              .metadata?.problem_class ??
            (initialInputData as { problem_class?: string }).problem_class
          }
        />
      </div>
    </div>
  );
}
