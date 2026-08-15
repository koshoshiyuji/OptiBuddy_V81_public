import { SimulationController } from '../components/SimulationController.tsx';
import { SolutionSelector } from '../components/SolutionSelector.tsx';
import { YardVesselPanels } from '../components/YardVesselPanels.tsx';
import type { StudioState } from '../types.ts';

export function SpatialView({ studio }: { studio: StudioState }) {
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
      <YardVesselPanels studio={studio} />
    </div>
  );
}