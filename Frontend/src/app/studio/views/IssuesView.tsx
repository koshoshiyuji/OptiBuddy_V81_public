
// ============================================================
// IssuesView.tsx  (V8: パラメータパネル統合 + optimize タブ廃止)
// ============================================================
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { IssueListView } from '../../../ui/IssueListView.tsx';
import { RunOptimizationButton } from '../components/RunOptimizationButton.tsx';
import type { StudioState } from '../types.ts';

// ─────────────────────────────────────────────
// パラメータパネル
// ─────────────────────────────────────────────
interface SolverWeights {
  makespan: number;
  resource_usage_cost: number;
  preference_penalty: number;
}

const DEFAULT_WEIGHTS: SolverWeights = {
  makespan: 1.0,
  resource_usage_cost: 0.5,
  preference_penalty: 0.3,
};

function WeightSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
      <span style={{ fontSize: '11px', color: '#888', width: '150px', flexShrink: 0 }}>{label}</span>
      <input
        type="range"
        min={0} max={2} step={0.1}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={{ flex: 1, accentColor: '#00e5ff' }}
      />
      <span style={{
        fontSize: '11px', fontWeight: 900, color: '#00e5ff',
        width: '30px', textAlign: 'right', flexShrink: 0,
      }}>
        {value.toFixed(1)}
      </span>
    </div>
  );
}

function ParameterPanel({
  weights,
  onChange,
  onReset,
}: {
  weights: SolverWeights;
  onChange: (key: keyof SolverWeights, value: number) => void;
  onReset: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div style={{
      padding: '12px 16px',
      background: '#0d0d12',
      borderTop: '1px solid #2a2a30',
      display: 'flex',
      flexDirection: 'column',
      gap: '10px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '10px', fontWeight: 900, color: '#555', letterSpacing: '1px' }}>
          OBJECTIVE WEIGHTS
        </span>
        <button
          onClick={onReset}
          style={{
            fontSize: '10px', color: '#555', background: 'none', border: 'none',
            cursor: 'pointer', padding: '2px 6px',
          }}
        >
          reset
        </button>
      </div>
      <WeightSlider
        label={t('issuesView.weightMakespan')}
        value={weights.makespan}
        onChange={(v) => onChange('makespan', v)}
      />
      <WeightSlider
        label={t('issuesView.weightResourceCost')}
        value={weights.resource_usage_cost}
        onChange={(v) => onChange('resource_usage_cost', v)}
      />
      <WeightSlider
        label={t('issuesView.weightPreference')}
        value={weights.preference_penalty}
        onChange={(v) => onChange('preference_penalty', v)}
      />
    </div>
  );
}

// ─────────────────────────────────────────────
// IssuesView 本体
// ─────────────────────────────────────────────
export function IssuesView({ studio }: { studio: StudioState }) {
  const { t } = useTranslation();
  const pendingCount = studio.allIssues.filter(
    (i) => studio.issueActions[i.id] !== 'ACCEPT' && !(i as any).skipped
  ).length;

  const [showParams, setShowParams] = useState(false);
  const [weights, setWeights] = useState<SolverWeights>(DEFAULT_WEIGHTS);

  const handleWeightChange = (key: keyof SolverWeights, value: number) => {
    setWeights((prev) => ({ ...prev, [key]: value }));
  };

  const handleResetWeights = () => setWeights(DEFAULT_WEIGHTS);

  // weight を乗せた上で handleRunSolver を呼ぶ
  // useStudioState の handleRunSolver は initialInputData.objective を参照するので
  // weight_overrides を DSL に一時的にマージして渡す
  const handleRunWithWeights = () => {
    const withWeights = {
      ...studio.initialInputData,
      objective: {
        ...(studio.initialInputData as any).objective,
        weight_overrides: weights,
      },
    };
    // handleApplyAndReoptimize は patchedDsl を受け取るので流用する
    void studio.handleApplyAndReoptimize(withWeights as any);
  };

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', gap: '10px', height: '100%', boxSizing: 'border-box' }}
      onMouseEnter={() => studio.setIsOverIssuePanel(true)}
      onMouseLeave={() => studio.setIsOverIssuePanel(false)}
    >
      {/* メインエリア：Issue Monitor */}
      <div style={{ display: 'flex', gap: '10px', flex: 1, minHeight: 0, overflow: 'hidden' }}>

        {/* Issue Monitor */}
        <div style={{
          flex: 1, minWidth: 0,
          background: '#121216', borderRadius: '10px', border: '1px solid #2a2a30',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          <div style={{
            flexShrink: 0, padding: '12px 15px', background: '#1a1a22',
            borderBottom: '1px solid #2a2a30', fontSize: '11px', fontWeight: 900,
            color: '#666', display: 'flex', alignItems: 'center', gap: '8px',
          }}>
            ISSUE MONITOR
            <span style={{
              fontSize: '10px', fontWeight: 900, padding: '1px 7px', borderRadius: '10px',
              background: pendingCount > 0 ? '#ff4d4d22' : '#28a74522',
              color: pendingCount > 0 ? '#ff4d4d' : '#28a745',
              border: `1px solid ${pendingCount > 0 ? '#ff4d4d44' : '#28a74544'}`,
            }}>
              {pendingCount > 0 ? t('issuesView.remaining', { count: pendingCount }) : `✓ ${t('issuesView.allResolved')}`}
            </span>
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <IssueListView
              issues={studio.allIssues}
              issueActions={studio.issueActions}
              highlightId={studio.highlightId}
              onHighlightChange={studio.setHighlightId}
              onApplyFix={studio.handleApplyFix}
              onBulkAccept={studio.handleBulkAccept}
            />
          </div>
        </div>
      </div>

      {/* 下：パラメータ + 最適化ボタン */}
      <div style={{
        flexShrink: 0,
        background: '#16161c',
        borderRadius: '10px',
        border: '1px solid #2a2a30',
        overflow: 'hidden',
      }}>
        {/* 詳細設定トグル */}
        <button
          onClick={() => setShowParams((v) => !v)}
          style={{
            width: '100%', padding: '7px 16px',
            background: 'none', border: 'none', borderBottom: showParams ? '1px solid #2a2a30' : 'none',
            cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px',
            color: '#555', fontSize: '10px', fontWeight: 900, letterSpacing: '1px',
          }}
        >
          <span style={{ transform: showParams ? 'rotate(90deg)' : 'none', display: 'inline-block', transition: 'transform 0.15s' }}>▶</span>
          {t('issuesView.advancedParams')}
        </button>

        {showParams && (
          <ParameterPanel
            weights={weights}
            onChange={handleWeightChange}
            onReset={handleResetWeights}
          />
        )}

        {/* 実行ボタン */}
        <div style={{ padding: '8px 12px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
          <RunOptimizationButton
            isSolverLoading={studio.isSolverLoading}
            onClick={handleRunWithWeights}
          />
        </div>
      </div>
    </div>
  );
}

