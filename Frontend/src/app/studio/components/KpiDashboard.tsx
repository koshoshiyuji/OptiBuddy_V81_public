import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Issue, IssueAction, Task } from '../../../domain/types.ts';
import { calcKpi } from '../calcKpi.ts';

export function KpiDashboard({
  tasks,
  makespan,
  allIssues,
  issueActions,
  onExportCsv,
  currentTimeHHMM,
  setCurrentTimeHHMM,
  operationStartHHMM,
  setOperationStartHHMM,
  operationDate,
  problemClass,
}: {
  tasks: Task[];
  makespan: number;
  allIssues: Issue[];
  issueActions: Record<string, IssueAction>;
  onExportCsv: () => void;
  currentTimeHHMM: string;
  setCurrentTimeHHMM: (hhmm: string) => void;
  operationStartHHMM: string;
  setOperationStartHHMM: (hhmm: string) => void;
  operationDate?: string;
  problemClass?: string;
}) {
  const { t } = useTranslation();
  const kpi = useMemo(
    () => calcKpi(tasks, makespan, allIssues, issueActions),
    [tasks, makespan, allIssues, issueActions]
  );

  if (tasks.length === 0) return null;

  const isStaffing = problemClass === 'EventStaffing';

  const cardBase: React.CSSProperties = {
    background: '#121216',
    border: '1px solid #2a2a30',
    borderRadius: '10px',
    padding: '12px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    minWidth: '120px',
  };

  const dateLabel = operationDate ?? new Date().toISOString().slice(0, 10);

  // グレード表示順
  const GRADE_ORDER = ['CHIEF', 'SENIOR', 'STANDARD', 'JUNIOR'];
  const GRADE_COLORS: Record<string, string> = {
    CHIEF: '#ff79c6', SENIOR: '#ffb86c', STANDARD: '#8be9fd', JUNIOR: '#6272a4',
  };

  return (
    <div style={{
      flexShrink: 0, display: 'flex', gap: '8px',
      alignItems: 'stretch', overflowX: 'auto', paddingBottom: '2px',
    }}>

      {/* ① CURRENT TIME（共通） */}
      <div style={{ ...cardBase, borderTop: '3px solid #00e5ff', minWidth: '160px' }}>
        <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>CURRENT TIME</span>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          <span style={{ fontSize: '10px', color: '#666', fontFamily: 'monospace' }}>📅 {dateLabel}</span>
          <div style={{ display: 'grid', gap: '8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
              <span style={{ fontSize: '10px', color: '#666', fontFamily: 'monospace' }}>START</span>
              <input type="time" value={operationStartHHMM} onChange={(e) => setOperationStartHHMM(e.target.value)}
                style={{ background: '#0a0a0c', border: '1px solid #2a2a3a', borderRadius: '5px', color: '#00e5ff',
                  fontSize: '0.95rem', fontFamily: 'monospace', fontWeight: 700, padding: '2px 6px',
                  outline: 'none', cursor: 'pointer', colorScheme: 'dark', width: '100px' }}
                onFocus={(e) => (e.currentTarget.style.borderColor = '#00e5ff')}
                onBlur={(e) => (e.currentTarget.style.borderColor = '#2a2a3a')} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
              <span style={{ fontSize: '10px', color: '#666', fontFamily: 'monospace' }}>CURRENT</span>
              <input type="time" value={currentTimeHHMM} onChange={(e) => setCurrentTimeHHMM(e.target.value)}
                style={{ background: '#0a0a0c', border: '1px solid #2a2a3a', borderRadius: '5px', color: '#00e5ff',
                  fontSize: '1.4rem', fontFamily: 'monospace', fontWeight: 900, padding: '2px 6px',
                  outline: 'none', cursor: 'pointer', colorScheme: 'dark', width: '100%' }}
                onFocus={(e) => (e.currentTarget.style.borderColor = '#00e5ff')}
                onBlur={(e) => (e.currentTarget.style.borderColor = '#2a2a3a')} />
            </div>
          </div>
        </div>
      </div>

      {isStaffing ? (
        <>
          {/* ② TOTAL COST */}
          <div style={{ ...cardBase, borderTop: '3px solid #00e5ff' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>TOTAL COST</span>
            <span style={{ fontSize: '1.4rem', fontWeight: 900, color: '#00e5ff', lineHeight: 1, fontFamily: 'monospace' }}>
              ¥{kpi.totalCost.toLocaleString()}
            </span>
          </div>

          {/* ③ PREF PENALTY */}
          <div style={{ ...cardBase, borderTop: '3px solid #ffb86c' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>PREF PENALTY</span>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
              <span style={{
                fontSize: '1.6rem', fontWeight: 900, lineHeight: 1, fontFamily: 'monospace',
                color: kpi.prefPenalty === 0 ? '#50fa7b' : '#ffb86c',
              }}>
                {kpi.prefPenalty}
              </span>
              <span style={{ fontSize: '10px', color: '#555' }}>{t('kpiDashboard.unitCount')}</span>
            </div>
            <span style={{ fontSize: '9px', color: '#555' }}>{t('kpiDashboard.prefUnmet')}</span>
          </div>

          {/* ④ STAFF ASSIGNMENT */}
          <div style={{ ...cardBase, borderTop: '3px solid #bd93f9' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>STAFF ASSIGN</span>
            <span style={{ fontSize: '1.6rem', fontWeight: 900, color: '#bd93f9', lineHeight: 1, fontFamily: 'monospace' }}>
              {kpi.assignedStaffCount}
              <span style={{ fontSize: '0.7rem', marginLeft: '3px', opacity: 0.6 }}>{t('kpiDashboard.unitPeople')}</span>
            </span>
            <span style={{ fontSize: '9px', color: '#555' }}>{t('kpiDashboard.assigned')}</span>
          </div>

          {/* ⑤ GRADE MIX */}
          <div style={{ ...cardBase, borderTop: '3px solid #6272a4', minWidth: '160px' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>GRADE MIX</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '2px' }}>
              {GRADE_ORDER.filter((g) => kpi.gradeCounts[g]).map((g) => (
                <div key={g} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '9px', color: GRADE_COLORS[g], width: '56px', flexShrink: 0, fontFamily: 'monospace', fontWeight: 700 }}>{g}</span>
                  <span style={{ fontSize: '11px', color: '#eee', fontFamily: 'monospace', fontWeight: 900 }}>{kpi.gradeCounts[g]}</span>
                  <span style={{ fontSize: '9px', color: '#555' }}>{t('kpiDashboard.unitPeople')}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      ) : (
        <>
          {/* ② MAKESPAN（YardPlanning） */}
          <div style={{ ...cardBase, borderTop: '3px solid #00e5ff' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>MAKESPAN</span>
            <span style={{ fontSize: '1.6rem', fontWeight: 900, color: '#00e5ff', lineHeight: 1, fontFamily: 'monospace' }}>
              {kpi.makespanMin}<span style={{ fontSize: '0.7rem', marginLeft: '3px', opacity: 0.6 }}>m</span>
            </span>
          </div>

          {/* ③ CRANE UTILIZATION */}
          <div style={{ ...cardBase, borderTop: '3px solid #bd93f9', minWidth: '180px' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>CRANE UTILIZATION</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '2px' }}>
              {kpi.craneUtil.map((c) => (
                <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span style={{ fontSize: '9px', color: '#888', width: '36px', flexShrink: 0, fontFamily: 'monospace' }}>{c.id}</span>
                  <div style={{ flex: 1, height: '8px', background: '#1e1e28', borderRadius: '4px', overflow: 'hidden' }}>
                    <div style={{
                      width: `${c.util}%`, height: '100%', borderRadius: '4px',
                      background: c.util > 80 ? '#50fa7b' : c.util > 50 ? '#ffea00' : '#ff6b35',
                      transition: 'width 0.5s ease',
                    }} />
                  </div>
                  <span style={{ fontSize: '9px', color: '#aaa', width: '28px', textAlign: 'right', fontFamily: 'monospace' }}>{c.util}%</span>
                </div>
              ))}
            </div>
          </div>

          {/* ④ REHANDLE */}
          <div style={{ ...cardBase, borderTop: '3px solid #ffb86c' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>REHANDLE</span>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
              <span style={{
                fontSize: '1.6rem', fontWeight: 900, lineHeight: 1, fontFamily: 'monospace',
                color: kpi.rehandleCount > 0 ? '#ffb86c' : '#50fa7b',
              }}>{kpi.rehandleCount}</span>
              <span style={{ fontSize: '10px', color: '#555' }}>{t('kpiDashboard.unitTimes')}</span>
            </div>
          </div>

          {/* ⑤ AVG WAIT */}
          <div style={{ ...cardBase, borderTop: '3px solid #6272a4' }}>
            <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>AVG WAIT</span>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
              <span style={{ fontSize: '1.6rem', fontWeight: 900, color: '#6272a4', lineHeight: 1, fontFamily: 'monospace' }}>
                {kpi.avgWaitMin}
              </span>
              <span style={{ fontSize: '10px', color: '#555' }}>m</span>
            </div>
          </div>
        </>
      )}

      {/* ISSUES（共通） */}
      <div style={{ ...cardBase, borderTop: '3px solid #ff4d4d', minWidth: '140px' }}>
        <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px' }}>ISSUES</span>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '2px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#28a745' }} />
              <span style={{ fontSize: '10px', color: '#aaa' }}>{t('kpiDashboard.resolved')} <span style={{ color: '#50fa7b', fontWeight: 700 }}>{kpi.resolvedCount}</span></span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#555' }} />
              <span style={{ fontSize: '10px', color: '#aaa' }}>{t('kpiDashboard.skipped')} <span style={{ color: '#888', fontWeight: 700 }}>{kpi.skippedCount}</span></span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#ff4d4d' }} />
              <span style={{ fontSize: '10px', color: '#aaa' }}>{t('kpiDashboard.remaining')} <span style={{ color: '#ff4d4d', fontWeight: 700 }}>{kpi.remainCount}</span></span>
            </div>
          </div>
        </div>
      </div>

      {/* EXPORT CSV（共通） */}
      <div style={{ ...cardBase, borderTop: '3px solid #2a2a30', justifyContent: 'center', alignItems: 'center', cursor: 'pointer' }}
        onClick={onExportCsv}
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#00e5ff'; }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = '#2a2a30'; }}>
        <span style={{ fontSize: '18px' }}>⬇️</span>
        <span style={{ fontSize: '9px', color: '#555', fontWeight: 800, letterSpacing: '1px', textAlign: 'center' }}>EXPORT CSV</span>
      </div>
    </div>
  );
}
