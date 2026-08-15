// Frontend/src/app/studio/views/LineChangeoverSchedulerView.tsx
// LineChangeoverScheduler — 専用ビューコンポーネント

import { GenericResultTable } from '../components/GenericResultTable.tsx';
import type { StudioState } from '../types.ts';

// ─── 型定義 ─────────────────────────────────────────────
interface LcsKpiCard {
  label: string;
  value: number | string;
  unit:  string;
  color: string;
}

interface LcsAlert {
  id:       string;
  severity: string;
  title:    string;
  message:  string;
}

interface LcsUiDsl {
  domain:         string;
  feasible:       boolean;
  summary:        { makespan: number; task_count: number; instance: string };
  kpi_cards:      LcsKpiCard[];
  table_sections: any[];
  alerts:         LcsAlert[];
  raw_kpi:        Record<string, number>;
}

// ─── デザイントークン ────────────────────────────────────
const COLOR = {
  bg: '#08080a', surface: '#121216', border: '#2a2a30',
  text: '#eee', muted: '#888',
  blue: '#3b82f6', green: '#10b981', orange: '#f97316',
  purple: '#8b5cf6', teal: '#14b8a6', red: '#ef4444', yellow: '#f59e0b',
};

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#ff4d4d',
  WARNING:  '#ffb86c',
  INFO:     '#8be9fd',
};

const CARD: React.CSSProperties = {
  background: COLOR.surface,
  border: `1px solid ${COLOR.border}`,
  borderRadius: '10px',
  padding: '16px',
};

// ─── KPI カード ─────────────────────────────────────────
function KpiCard({ card }: { card: LcsKpiCard }) {
  return (
    <div style={{ ...CARD, flex: 1, minWidth: '120px', textAlign: 'center' }}>
      <div style={{ fontSize: '20px', fontWeight: 900, color: card.color, fontFamily: 'monospace' }}>
        {typeof card.value === 'number' ? card.value.toLocaleString() : card.value}
        {card.unit && (
          <span style={{ fontSize: '11px', color: COLOR.muted, marginLeft: '4px' }}>{card.unit}</span>
        )}
      </div>
      <div style={{ fontSize: '9px', color: COLOR.muted, fontWeight: 800, letterSpacing: '0.5px', marginTop: '4px' }}>
        {card.label}
      </div>
    </div>
  );
}

// ─── アラートバナー ──────────────────────────────────────
function AlertBanner({ alert }: { alert: LcsAlert }) {
  const color = SEVERITY_COLOR[alert.severity] ?? COLOR.muted;
  return (
    <div style={{
      padding: '10px 14px', borderRadius: '8px',
      borderLeft: `3px solid ${color}`,
      background: `${color}11`,
      display: 'flex', alignItems: 'flex-start', gap: '10px',
    }}>
      <span style={{
        fontSize: '8px', padding: '2px 6px', borderRadius: '4px', fontWeight: 900,
        background: `${color}22`, color, border: `1px solid ${color}44`, flexShrink: 0,
      }}>
        {alert.severity}
      </span>
      <div>
        <div style={{ fontSize: '11px', fontWeight: 700, color: COLOR.text, marginBottom: '2px' }}>
          {alert.title}
        </div>
        <div style={{ fontSize: '10px', color: '#888', lineHeight: 1.5 }}>
          {alert.message}
        </div>
      </div>
    </div>
  );
}

// ─── メインビュー ────────────────────────────────────────
export function LineChangeoverSchedulerView({ studio }: { studio: StudioState }) {
  const uiDsl = (studio as any).lineChangeoverSchedulerUiDsl as LcsUiDsl | null;

  if (!uiDsl) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', color: COLOR.muted, fontSize: '13px',
      }}>
        データがありません。最適化を実行してください。
      </div>
    );
  }

  const alerts    = uiDsl.alerts ?? [];
  const kpiCards  = uiDsl.kpi_cards ?? [];
  const sections  = uiDsl.table_sections ?? [];
  const summary   = uiDsl.summary ?? {};

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', flex: 1, minHeight: 0 }}>

      {/* ヘッダー */}
      <div style={{ ...CARD, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: '13px', fontWeight: 900, color: COLOR.text, letterSpacing: '1px' }}>
              LINE CHANGEOVER SCHEDULER
            </div>
            {summary.instance && (
              <div style={{ fontSize: '10px', color: COLOR.muted, marginTop: '2px' }}>
                {summary.instance}
              </div>
            )}
          </div>
          <span style={{
            fontSize: '10px', padding: '3px 10px', borderRadius: '8px', fontWeight: 900,
            background: uiDsl.feasible ? '#10b98122' : '#ef444422',
            color:      uiDsl.feasible ? COLOR.green   : COLOR.red,
            border:     `1px solid ${uiDsl.feasible ? '#10b98144' : '#ef444444'}`,
          }}>
            {uiDsl.feasible ? '✅ FEASIBLE' : '❌ INFEASIBLE'}
          </span>
        </div>
      </div>

      {/* 1. KPI カード */}
      {kpiCards.length > 0 && (
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', flexShrink: 0 }}>
          {kpiCards.map((card, i) => <KpiCard key={i} card={card} />)}
        </div>
      )}

      {/* 2. アラート */}
      {alerts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', flexShrink: 0 }}>
          {alerts.map((a) => <AlertBanner key={a.id} alert={a} />)}
        </div>
      )}

      {/* 3. 詳細テーブル（GenericResultTable 必須） */}
      <GenericResultTable sections={sections} />
    </div>
  );
}