import type { SolutionPlan } from '../types.ts';

export function SolutionSelector({
  solutions,
  selectedIndex,
  onSelect,
}: {
  solutions: SolutionPlan[];
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  if (solutions.length <= 1) return null;

  const SEC_PER_MIN = 60;

  return (
    <div
      style={{
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '10px 14px',
        background: '#121216',
        border: '1px solid #2a2a30',
        borderRadius: '10px',
      }}
    >
      <span
        style={{
          fontSize: '10px',
          color: '#555',
          fontWeight: 800,
          letterSpacing: '2px',
          marginRight: '4px',
          whiteSpace: 'nowrap',
        }}
      >
        SOLUTION
      </span>
      {solutions.map((sol, i) => {
        const isActive = i === selectedIndex;
        // CVRP等tasksベースでないドメインでは makespan が存在しないため、
        // その場合は "NaNm" を表示せず、行自体を省略する。
        const hasMakespan = typeof sol.makespan === 'number' && !Number.isNaN(sol.makespan);
        const makespanMin = hasMakespan ? Math.round(sol.makespan / SEC_PER_MIN) : null;
        return (
          <button
            key={`${sol.name}-${i}`}
            onClick={() => onSelect(i)}
            style={{
              padding: '6px 16px',
              background: isActive ? '#00e5ff' : '#1a1a22',
              color: isActive ? '#000' : '#666',
              border: `1px solid ${isActive ? '#00e5ff' : '#2a2a30'}`,
              borderRadius: '6px',
              fontSize: '11px',
              fontWeight: 900,
              cursor: 'pointer',
              fontFamily: 'monospace',
              letterSpacing: '1px',
              transition: 'all 0.15s ease',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '2px',
              lineHeight: 1,
            }}
          >
            <span>{sol.name}</span>
            {makespanMin !== null && (
              <span style={{ fontSize: '9px', fontWeight: 700, color: isActive ? '#005060' : '#444' }}>
                {makespanMin}m
              </span>
            )}
            {sol.label && (
              <span style={{ fontSize: '8px', fontWeight: 600, color: isActive ? '#007080' : '#333', letterSpacing: '0.5px' }}>
                {sol.label}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
