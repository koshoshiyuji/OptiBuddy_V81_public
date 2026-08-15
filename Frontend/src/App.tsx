import { useState } from 'react';
import { HomeScreen } from './app/HomeScreenNew.tsx';
import { StudioController } from './app/StudioController.tsx';
import type { Dsl } from './domain/types';

export default function App() {
  // null = ホーム画面, Dsl = StudioController表示
  const [selectedDsl, setSelectedDsl] = useState<Dsl | null>(null);
  const [selectedScenarioLabel, setSelectedScenarioLabel] = useState<string | null>(null);

  const handleSelect = (dsl: Dsl, label?: string | null) => {
    setSelectedDsl(dsl);
    setSelectedScenarioLabel(label ?? null);
  };

  const handleBackToHome = () => {
    setSelectedDsl(null);
    setSelectedScenarioLabel(null);
  };

  if (!selectedDsl) {
    return <HomeScreen onSelect={handleSelect} />;
  }

  return (
    <div style={{ width: '100vw', height: '100vh', background: '#0a0a0c' }}>
      <StudioController
        key={JSON.stringify(selectedDsl)} // DSLが変わったら完全リマウント
        initialTasks={[]}
        initialInputData={selectedDsl}
        scenarioLabel={selectedScenarioLabel ?? undefined}
        onBackToHome={handleBackToHome}
      />
    </div>
  );
}
