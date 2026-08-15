import { SimulationController } from '../components/SimulationController.tsx';
import { GanttPanel } from '../components/GanttPanel.tsx';
import { SolutionSelector } from '../components/SolutionSelector.tsx';
import type { StudioState } from '../types.ts';

export function TimelineView({ studio }: { studio: StudioState }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        minHeight: 0,
        flex: 1,
      }}
    >
      <SimulationController studio={studio} />
      <SolutionSelector
        solutions={studio.solutions}
        selectedIndex={studio.selectedSolutionIndex}
        onSelect={studio.setSelectedSolutionIndex}
      />
      <GanttPanel studio={studio} fill />
    </div>
  );
}
