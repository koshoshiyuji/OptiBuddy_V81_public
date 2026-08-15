import { YardView } from '../../../ui/YardView.tsx';
import { VesselView } from '../../../ui/VesselView.tsx';
import type { StudioState } from '../types.ts';

type PickState = Pick<
  StudioState,
  | 'yardBays'
  | 'shipBays'
  | 'selectedYardBay'
  | 'setSelectedYardBay'
  | 'selectedShipBay'
  | 'setSelectedShipBay'
  | 'liveViewData'
  | 'tasks'
  | 'currentTime'
  | 'highlightId'
  | 'setHighlightId'
  | 'initialInputData'
>;

export function YardVesselPanels({ studio }: { studio: PickState }) {
  const {
    yardBays, shipBays,
    selectedYardBay, setSelectedYardBay,
    selectedShipBay, setSelectedShipBay,
    liveViewData, tasks, currentTime,
    highlightId, setHighlightId,
    initialInputData,
  } = studio;

  const dslContainers  = initialInputData?.containers ?? [];
  const config         = initialInputData?.config;
  const restrictedZones = initialInputData?.resources?.restricted_zones ?? {};

  const panelStyle: React.CSSProperties = {
    background: '#121216', borderRadius: '10px', border: '1px solid #2a2a30',
    display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0, flex: 1,
  };
  const headerStyle: React.CSSProperties = {
    flexShrink: 0, background: '#1a1a22', padding: '8px 15px',
    borderBottom: '1px solid #2a2a30', display: 'flex',
    justifyContent: 'space-between', alignItems: 'center',
  };
  const selectStyle: React.CSSProperties = {
    background: '#0a0a0c', border: '1px solid #333', borderRadius: '4px',
    fontSize: '11px', fontWeight: 900, padding: '2px 4px', outline: 'none', cursor: 'pointer',
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', minHeight: 0, flex: 1 }}>
      {/* YARD */}
      <div style={panelStyle}>
        <div style={headerStyle}>
          <span style={{ fontSize: '11px', fontWeight: 800, color: '#aaa' }}>YARD STORAGE</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '10px', fontWeight: 700, color: '#444', textTransform: 'uppercase' }}>Bay</span>
            <select value={selectedYardBay} onChange={e => setSelectedYardBay(Number(e.target.value))}
              style={{ ...selectStyle, color: '#00e5ff' }}>
              {yardBays.map(b => <option key={b} value={b}>B{b}</option>)}
            </select>
          </div>
        </div>
        <div style={{ flex: 1, padding: '15px', overflow: 'auto', minHeight: 0 }}>
          <YardView
            containerData={liveViewData}
            bayNumber={selectedYardBay}
            tasks={tasks}
            currentTime={currentTime}
            highlightId={highlightId}
            onHighlightChange={setHighlightId}
            dslContainers={dslContainers}
            config={config}
            restrictedZones={restrictedZones}
          />
        </div>
      </div>

      {/* VESSEL */}
      <div style={panelStyle}>
        <div style={headerStyle}>
          <span style={{ fontSize: '11px', fontWeight: 800, color: '#aaa' }}>VESSEL PLAN</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '10px', fontWeight: 700, color: '#444', textTransform: 'uppercase' }}>Bay</span>
            <select value={selectedShipBay} onChange={e => setSelectedShipBay(Number(e.target.value))}
              style={{ ...selectStyle, color: '#ffea00' }}>
              {shipBays.map(b => <option key={b} value={b}>B{b}</option>)}
            </select>
          </div>
        </div>
        <div style={{ flex: 1, padding: '15px', overflow: 'auto', minHeight: 0 }}>
          <VesselView
            containerData={liveViewData}
            bayNumber={selectedShipBay}
            tasks={tasks}
            currentTime={currentTime}
            highlightId={highlightId}
            onHighlightChange={setHighlightId}
            dslContainers={dslContainers}
            config={config}
          />
        </div>
      </div>
    </div>
  );
}
