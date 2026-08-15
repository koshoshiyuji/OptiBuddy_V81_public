export function RunOptimizationButton({
  isSolverLoading,
  onClick,
}: {
  isSolverLoading: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={isSolverLoading}
      style={{
        width: '100%',
        padding: '14px',
        backgroundColor: isSolverLoading ? '#333' : '#007bff',
        color: '#fff',
        border: 'none',
        borderRadius: '6px',
        fontWeight: 900,
        cursor: isSolverLoading ? 'wait' : 'pointer',
        boxShadow: '0 4px 15px rgba(0,0,0,0.3)',
      }}
    >
      {isSolverLoading ? 'OPTIMIZING...' : 'RUN GLOBAL OPTIMIZATION'}
    </button>
  );
}
