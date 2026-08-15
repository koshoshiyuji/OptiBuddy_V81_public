import type { SnapshotContainer, Task, DslContainer } from '../../../domain/types.ts';

const ATTR_STYLE: Record<string, { label: string; color: string }> = {
  REEFER: { label: "❄ REEFER",    color: "#00b4d8" },
  OOG:    { label: "⬛ OOG",      color: "#9b5de5" },
};

function getAttrDisplays(attrs: string[]): { label: string; color: string }[] {
  return attrs.map((a) => {
    if (a.startsWith("IMO_")) return { label: `☢ ${a.replace("_", " ")}`, color: "#ff6b35" };
    return ATTR_STYLE[a] ?? { label: a, color: "#888" };
  });
}

export function ContainerTooltip({
  containerId,
  tasks,
  liveViewData,
  pos,
  currentTime,
  dslContainers = [],
}: {
  containerId: string;
  tasks: Task[];
  liveViewData: Record<string, SnapshotContainer>;
  pos: { x: number; y: number };
  currentTime: number;
  // ★ DSLコンテナリスト（attrs参照用）
  dslContainers?: DslContainer[];
}) {
  const container = liveViewData[containerId];
  const allContainerTasks = tasks
    .filter((t) => t.containerId === containerId)
    .sort((a, b) => a.start - b.start);
  const firstTask = allContainerTasks[0];

  if (!container) return null;

  const weight = container.weight || (firstTask as { weight?: number })?.weight || null;
  const pol =
    container.pol !== '---'
      ? container.pol
      : ((firstTask as { ports?: { pol?: string }; pol?: string })?.ports?.pol ??
        (firstTask as { pol?: string })?.pol ??
        null);
  const pod =
    container.pod !== '---'
      ? container.pod
      : ((firstTask as { ports?: { pod?: string }; pod?: string })?.ports?.pod ??
        (firstTask as { pod?: string })?.pod ??
        null);

  const currentOp =
    allContainerTasks.find(
      (t) =>
        t.start !== undefined &&
        t.end !== undefined &&
        currentTime >= t.start &&
        currentTime < t.end
    )?.operation ?? 'IDLE';

  const order = (firstTask as { order?: number })?.order ?? null;

  // ★ DSLからattrsを取得
  const dslContainer = dslContainers.find((c) => String(c.id) === containerId);
  const attrs = dslContainer?.attrs ?? [];
  const attrDisplays = getAttrDisplays(attrs as string[]);

  const TOOLTIP_HEIGHT = 200 + (attrDisplays.length > 0 ? 30 : 0);
  const TOOLTIP_WIDTH = 260;
  const isNearBottom = pos.y > window.innerHeight - TOOLTIP_HEIGHT - 40;
  const isNearRight = pos.x > window.innerWidth - TOOLTIP_WIDTH - 40;

  const tooltipStyle: React.CSSProperties = {
    position: 'fixed',
    left: isNearRight ? pos.x - TOOLTIP_WIDTH - 10 : pos.x + 20,
    top: isNearBottom ? pos.y - TOOLTIP_HEIGHT - 10 : pos.y + 20,
    background: 'rgba(18, 18, 22, 0.98)',
    border: '1px solid #00e5ff',
    color: '#fff',
    padding: '14px',
    borderRadius: '12px',
    zIndex: 99999,
    pointerEvents: 'none',
    fontSize: '12px',
    boxShadow: '0 12px 40px rgba(0,0,0,0.9)',
    minWidth: `${TOOLTIP_WIDTH}px`,
    backdropFilter: 'blur(12px)',
    borderLeft: '5px solid #00e5ff',
  };

  return (
    <div style={tooltipStyle}>
      <div
        style={{
          color: '#00e5ff',
          fontWeight: 800,
          borderBottom: '1px solid #333',
          paddingBottom: '8px',
          marginBottom: '10px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <span style={{ fontSize: '13px' }}>{containerId}</span>
        {/* ★ 属性バッジをタイトル横に表示 */}
        {attrDisplays.map((a) => (
          <span
            key={a.label}
            style={{
              fontSize: '9px',
              padding: '1px 5px',
              borderRadius: '3px',
              background: `${a.color}22`,
              border: `1px solid ${a.color}`,
              color: a.color,
              fontWeight: 900,
            }}
          >
            {a.label}
          </span>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', gap: '6px', lineHeight: '1.6' }}>
        <span style={{ color: '#666' }}>LOCATION</span>
        <span style={{ color: '#ffea00' }}>{container.currentPos}</span>
        <span style={{ color: '#666' }}>COORDS</span>
        <span>
          {container.currentPos === 'YARD'
            ? `B${container.yard?.bay ?? '?'}-R${container.yard?.row ?? '?'}-T${container.yard?.tier ?? '?'}`
            : `B${container.ship?.bay ?? '?'}-R${container.ship?.row ?? '?'}-T${container.ship?.tier ?? '?'}`}
        </span>
        <span style={{ color: '#666' }}>OPERATION</span>
        <span style={{ color: '#00e5ff' }}>{currentOp}</span>
        {order !== null && order !== 999 && (
          <>
            <span style={{ color: '#666' }}>LOAD ORDER</span>
            <span style={{ color: '#ffea00' }}>#{order}</span>
          </>
        )}
        {weight !== null && (
          <>
            <span style={{ color: '#666' }}>WEIGHT</span>
            <span>{weight}t</span>
          </>
        )}
        {pol && (
          <>
            <span style={{ color: '#666' }}>POL</span>
            <span>{pol}</span>
          </>
        )}
        {pod && (
          <>
            <span style={{ color: '#666' }}>POD</span>
            <span>{pod}</span>
          </>
        )}
      </div>
    </div>
  );
}
