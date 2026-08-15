import type { StudioState } from '../types.ts';

type PickState = Pick<
  StudioState,
  | 'currentTime'
  | 'setCurrentTime'
  | 'timePoints'
  | 'handleReset'
  | 'currentTimeMin'
  | 'currentTimeHHMM'
  | 'setCurrentTimeHHMM'
>;

export function SimulationController({
  studio,
  compact = false,
}: {
  studio: PickState;
  compact?: boolean;
}) {
  const {
    currentTime,
    setCurrentTime,
    timePoints,
    handleReset,
    currentTimeHHMM,
    setCurrentTimeHHMM,
  } = studio;

  const elapsedMin = Math.floor(currentTime / 60);

  return (
    <div
      style={{
        background: '#121216',
        borderRadius: '10px',
        border: '1px solid #2a2a30',
        padding: compact ? '10px 14px' : '15px',
        flexShrink: 0,
      }}
    >
      {/* ── 上段：TIME ELAPSED + CURRENT TIME + リセット ── */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-end',
          marginBottom: '10px',
          gap: '10px',
          flexWrap: 'wrap',
        }}
      >
        {/* 左：TIME ELAPSED と CURRENT TIME を同じ高さで揃える */}
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: '24px' }}>

          {/* TIME ELAPSED */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>
              TIME ELAPSED
            </span>
            <div
              style={{
                color: '#00e5ff',
                fontSize: compact ? '1.6rem' : '2.2rem',
                fontWeight: 900,
                fontFamily: 'monospace',
                lineHeight: 1,
              }}
            >
              {String(elapsedMin).padStart(3, '0')}
              <span style={{ fontSize: '0.75rem', marginLeft: '4px', opacity: 0.5 }}>MIN</span>
            </div>
          </div>

          {/* ★ CURRENT TIME — フォントサイズを TIME ELAPSED に揃える */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <span style={{ fontSize: '10px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>
              CURRENT TIME
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span style={{ fontSize: '11px', color: '#555', lineHeight: 1 }}>📅</span>
              <input
                type="time"
                value={currentTimeHHMM}
                onChange={(e) => setCurrentTimeHHMM(e.target.value)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  borderBottom: '1px solid #2a2a3a',
                  borderRadius: 0,
                  color: '#00e5ff',
                  fontSize: compact ? '1.6rem' : '2.2rem',
                  fontFamily: 'monospace',
                  fontWeight: 900,
                  lineHeight: 1,
                  padding: '0 2px',
                  outline: 'none',
                  cursor: 'pointer',
                  colorScheme: 'dark',
                  width: compact ? '6.5rem' : '8rem',
                }}
                onFocus={(e) => (e.currentTarget.style.borderBottomColor = '#00e5ff')}
                onBlur={(e) => (e.currentTarget.style.borderBottomColor = '#2a2a3a')}
              />
            </div>
          </div>
        </div>

        {/* 右：クイックジャンプボタン + RESET */}
        {!compact && (
          <div
            style={{
              display: 'flex',
              gap: '5px',
              flexWrap: 'wrap',
              justifyContent: 'flex-end',
              alignItems: 'center',
              maxWidth: '55%',
            }}
          >
            {timePoints.map((sec) => (
              <button
                key={sec}
                onClick={() => setCurrentTime(sec)}
                style={{
                  padding: '3px 7px',
                  background: currentTime === sec ? '#00e5ff' : '#1a1a20',
                  color: currentTime === sec ? '#000' : '#666',
                  fontSize: '10px',
                  border: '1px solid #333',
                  borderRadius: '3px',
                  cursor: 'pointer',
                  fontFamily: 'monospace',
                  fontWeight: currentTime === sec ? 800 : 400,
                }}
              >
                {Math.round(sec / 60)}m
              </button>
            ))}
            <button
              onClick={handleReset}
              style={{
                background: '#221111',
                color: '#ff4444',
                padding: '3px 10px',
                borderRadius: '4px',
                fontSize: '10px',
                border: '1px solid #441111',
                cursor: 'pointer',
                fontWeight: 800,
              }}
            >
              RESET
            </button>
          </div>
        )}
      </div>

      {/* ── スライダー ── */}
      <input
        type="range"
        min={0}
        max={Math.max(600, ...timePoints)}
        value={currentTime}
        onChange={(e) => setCurrentTime(Number(e.target.value))}
        style={{ width: '100%', accentColor: '#00e5ff' }}
      />
    </div>
  );
}
