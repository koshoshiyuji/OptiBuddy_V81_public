// Frontend/src/app/studio/views/OverviewView.tsx
// ※ 完全差し替えファイル

import { useTranslation } from 'react-i18next';
import { KpiDashboard } from '../components/KpiDashboard.tsx';
import { GanttPanel } from '../components/GanttPanel.tsx';
import { SolutionSelector } from '../components/SolutionSelector.tsx';
import type { StudioState, Issue, IssueAction } from '../types.ts';

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#ff4d4d', WARNING: '#ffb86c', INFO: '#8be9fd',
};
const SEVERITY_ORDER: Record<string, number> = { CRITICAL: 0, WARNING: 1, INFO: 2 };

// ─────────────────────────────────────────────────────────────
// IssuePreviewCard（変更なし）
// ─────────────────────────────────────────────────────────────

function IssuePreviewCard({
  issue,
  action,
  onFix,
  onAccept,
}: {
  issue:    Issue;
  action:   IssueAction;
  onFix:    () => void;
  onAccept: () => void;
}) {
  const color    = SEVERITY_COLOR[issue.severity] ?? '#888';
  const resolved = action === 'FIX' || action === 'ACCEPT';
  return (
    <div style={{
      padding:     '10px 14px',
      borderRadius:'8px',
      borderLeft:  `3px solid ${resolved ? '#444' : color}`,
      background:  resolved ? '#111' : '#16161e',
      opacity:     resolved ? 0.5 : 1,
      display:     'flex', alignItems: 'flex-start', gap: '12px',
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '3px' }}>
          <span style={{
            fontSize: '8px', padding: '1px 6px', borderRadius: '4px', fontWeight: 900,
            background: `${color}22`, color, border: `1px solid ${color}44`,
          }}>{issue.severity}</span>
          <span style={{ fontSize: '11px', fontWeight: 700, color: resolved ? '#555' : '#eee' }}>
            {issue.title}
          </span>
        </div>
        <p style={{ margin: 0, fontSize: '10px', color: '#666', lineHeight: 1.5 }}>
          {issue.message}
        </p>
      </div>
      {!resolved && (
        <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
          <button onClick={onFix} style={{
            padding: '4px 10px', fontSize: '10px', fontWeight: 700, cursor: 'pointer',
            background: '#00e5ff22', border: '1px solid #00e5ff', color: '#00e5ff',
            borderRadius: '5px',
          }}>FIX</button>
          <button onClick={onAccept} style={{
            padding: '4px 10px', fontSize: '10px', fontWeight: 700, cursor: 'pointer',
            background: 'transparent', border: '1px solid #444', color: '#666',
            borderRadius: '5px',
          }}>ACCEPT</button>
        </div>
      )}
      {resolved && (
        <span style={{ fontSize: '10px', color: '#444', fontWeight: 700, flexShrink: 0 }}>
          {action === 'FIX' ? '✅ FIX' : '⏭ ACCEPT'}
        </span>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// IssueMonitorPanel — トップレベルに定義（関数内定義から移動）
// ─────────────────────────────────────────────────────────────

function IssueMonitorPanel({
  accentColor,
  onDetailClick,
  detailLabel,
  sortedIssues,
  unresolvedCount,
  studio,
}: {
  accentColor:     string;
  onDetailClick:   () => void;
  detailLabel:     string;
  sortedIssues:    Issue[];
  unresolvedCount: number;
  studio:          StudioState;
}) {
  const { t } = useTranslation();
  return (
    <div style={{
      flex: 1, minHeight: 0, overflow: 'auto',
      background: '#121216', border: '1px solid #2a2a30', borderRadius: '10px', padding: '16px',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px',
      }}>
        <span style={{ fontSize: '11px', fontWeight: 800, color: accentColor, letterSpacing: '1px' }}>
          ISSUE MONITOR
          {unresolvedCount > 0 && (
            <span style={{
              marginLeft: '8px', fontSize: '9px', padding: '2px 8px', borderRadius: '8px',
              background: '#ff4d4d33', color: '#ff4d4d', border: '1px solid #ff4d4d44', fontWeight: 900,
            }}>{t('overview.remaining', { count: unresolvedCount })}</span>
          )}
          {/* 2026-07-24追加: 非同期解チェッカー（DESIGN_2026-07-21 3-3節）が
              バックグラウンドで実行中の間だけ表示するインジケータ。O(n²)以上の
              チェックが閾値を超えた稀なケースのみ発生する（通常は表示されない）。 */}
          {studio.isCheckingDeferred && (
            <span style={{
              marginLeft: '8px', fontSize: '9px', padding: '2px 8px', borderRadius: '8px',
              background: '#f1c40f22', color: '#f1c40f', border: '1px solid #f1c40f44', fontWeight: 900,
            }}>{t('overview.checkingDeferred')}</span>
          )}
        </span>
        {studio.allIssues.length > 0 && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <button onClick={() => studio.handleBulkFix(studio.allIssues)} style={{
              padding: '4px 12px', fontSize: '10px', fontWeight: 700, cursor: 'pointer',
              background: '#00e5ff22', border: '1px solid #00e5ff', color: '#00e5ff', borderRadius: '5px',
            }}>{t('overview.fixAll')}</button>
            <button onClick={() => studio.handleBulkAccept(studio.allIssues)} style={{
              padding: '4px 12px', fontSize: '10px', fontWeight: 700, cursor: 'pointer',
              background: 'transparent', border: '1px solid #444', color: '#666', borderRadius: '5px',
            }}>{t('overview.acceptAll')}</button>
          </div>
        )}
      </div>

      {sortedIssues.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#50fa7b', fontSize: '13px', fontWeight: 700, padding: '40px 0' }}>
          ✅ {t('overview.noIssues')}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {sortedIssues.map((issue) => (
            <IssuePreviewCard
              key={issue.id}
              issue={issue}
              action={studio.issueActions[issue.id] || 'NONE'}
              onFix={() => studio.handleApplyFix(issue, 'FIX')}
              onAccept={() => studio.handleApplyFix(issue, 'ACCEPT')}
            />
          ))}
        </div>
      )}

      <button onClick={onDetailClick} style={{
        marginTop: '12px', width: '100%', padding: '8px',
        background: 'transparent', border: `1px solid ${accentColor}`,
        color: accentColor, fontSize: '11px', fontWeight: 700, cursor: 'pointer', borderRadius: '6px',
      }}>
        {detailLabel}
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// OverviewView
// ─────────────────────────────────────────────────────────────

export function OverviewView({ studio }: { studio: StudioState }) {
  const { t } = useTranslation();
  const operationDate =
    (studio.initialInputData.config as { operation_date?: string }).operation_date ??
    new Date().toISOString().slice(0, 10);

  const problemClass =
    (studio.initialInputData as { metadata?: { problem_class?: string }; problem_class?: string })
      .metadata?.problem_class ??
    (studio.initialInputData as { problem_class?: string }).problem_class ??
    'YardPlanning';

  const isStaffing   = problemClass === 'EventStaffing';
  const isKitchen    = problemClass === 'GhostKitchen';
  const isBinPacking = problemClass === 'BinPacking';

  const sortedIssues = [...studio.allIssues].sort((a, b) =>
    (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  );
  const unresolvedCount = sortedIssues.filter(
    (i) => (studio.issueActions[i.id] || 'NONE') === 'NONE'
  ).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', minHeight: 0, flex: 1 }}>
      <KpiDashboard
        tasks={studio.tasks}
        makespan={studio.makespan}
        allIssues={studio.allIssues}
        issueActions={studio.issueActions}
        onExportCsv={studio.handleExportCsv}
        currentTimeHHMM={studio.currentTimeHHMM}
        setCurrentTimeHHMM={studio.setCurrentTimeHHMM}
        operationStartHHMM={studio.operationStartHHMM}
        setOperationStartHHMM={studio.setOperationStartHHMM}
        operationDate={operationDate}
        problemClass={problemClass}
      />

      <SolutionSelector
        solutions={studio.solutions}
        selectedIndex={studio.selectedSolutionIndex}
        onSelect={studio.setSelectedSolutionIndex}
      />

      {isBinPacking ? (
        <IssueMonitorPanel
          accentColor="#34d399"
          onDetailClick={() => studio.setMode('bin_packing')}
          detailLabel={t('overview.detailLinkBinPacking')}
          sortedIssues={sortedIssues}
          unresolvedCount={unresolvedCount}
          studio={studio}
        />
      ) : isKitchen ? (
        <IssueMonitorPanel
          accentColor="#f97316"
          onDetailClick={() => studio.setMode('kitchen')}
          detailLabel={t('overview.detailLinkKitchen')}
          sortedIssues={sortedIssues}
          unresolvedCount={unresolvedCount}
          studio={studio}
        />
      ) : isStaffing ? (
        <IssueMonitorPanel
          accentColor="#82a1ff"
          onDetailClick={() => studio.setMode('issues')}
          detailLabel={t('overview.detailLinkIssues')}
          sortedIssues={sortedIssues}
          unresolvedCount={unresolvedCount}
          studio={studio}
        />
      ) : (
        /* YardPlanning: 既存ガント */
        <>
          <GanttPanel studio={studio} fill />
          {unresolvedCount > 0 && (
            <div style={{
              flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '12px 16px', background: '#1a1214', border: '1px solid #442222', borderRadius: '10px',
            }}>
              <span style={{ fontSize: '12px', color: '#ff6b6b' }}>
                {t('overview.unresolvedIssuesPrefix')} <strong>{unresolvedCount}</strong> {t('overview.unresolvedIssuesSuffix')}
              </span>
              <button onClick={() => studio.setMode('issues')} style={{
                padding: '6px 14px', background: 'transparent', border: '1px solid #ff4d4d',
                color: '#ff4d4d', borderRadius: '6px', fontSize: '11px', fontWeight: 800, cursor: 'pointer',
              }}>
                {t('overview.goToIssues')}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}