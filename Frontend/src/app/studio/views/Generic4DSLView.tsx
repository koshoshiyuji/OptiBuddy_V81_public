// Frontend/src/app/studio/views/Generic4DSLView.tsx
// 汎用4DSLビュー：新規ドメイン追加時の完全自動化を実現
// ui_dsl.kpi_cards, ui_dsl.alerts を共通表示し、ドメイン固有データはJSON表示

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { StudioState } from '../types';
import type { GanttTask, HighlightId } from '../../../domain/types';
import { SolutionSelector } from '../components/SolutionSelector.tsx';
import { GenericResultTable } from '../components/GenericResultTable.tsx';
import { TaskGantt } from '../../../ui/TaskGantt.tsx';

interface KpiCard {
  id: string;
  label: string;
  value: string;
  sub?: string;
  color: string;
  icon?: string;
}

interface Alert {
  id: string;
  severity: string;
  title: string;
  message: string;
}

const COLOR = {
  bg: '#08080a',
  surface: '#121216',
  border: '#2a2a30',
  text: '#eee',
  muted: '#888',
  red: '#ef4444',
  orange: '#f97316',
  yellow: '#f59e0b',
};

export function Generic4DSLView({ studio }: { studio: StudioState }) {
  const { t } = useTranslation();
  const uiDsl = (studio as any).cvrpUiDsl;  // 汎用化: 全4DSLドメインで使用

  // 汎用Gantt表示用のローカル状態。table_sectionsと同じくGeneric4DSLView内で
  // 完結させ、YardPlanning固有のStudioState/GanttPanel（shifts/breaks/
  // operationStart等）には依存しない（ドメイン非依存に保つため）。
  const [ganttCurrentTime, setGanttCurrentTime] = useState(0);
  const [ganttHighlightId, setGanttHighlightId] = useState<HighlightId>({ kind: 'none' });

  if (!uiDsl) {
    return (
      <div style={{ padding: '20px', color: COLOR.muted, textAlign: 'center' }}>
        {t('generic4dsl.noData')}
      </div>
    );
  }

  const kpiCards: KpiCard[] = uiDsl.kpi_cards ?? [];
  const alerts: Alert[] = uiDsl.alerts ?? [];
  const feasible: boolean = uiDsl.feasible ?? true;
  const domain: string = uiDsl.domain ?? 'unknown';
  const domainLabel = domain.replace(/_/g, ' ').toUpperCase();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', padding: '16px', height: '100%', overflow: 'auto' }}>
      {/* ソリューションセレクター */}
      <SolutionSelector
        solutions={studio.solutions}
        selectedIndex={studio.selectedSolutionIndex}
        onSelect={studio.setSelectedSolutionIndex}
      />

      {/* ドメイン名表示 */}
      <div style={{
        fontSize: '24px',
        fontWeight: 'bold',
        color: COLOR.text,
        padding: '12px 16px',
        background: COLOR.surface,
        border: `1px solid ${COLOR.border}`,
        borderRadius: '8px',
      }}>
        📊 {domainLabel}
      </div>

      {/* 実行可能性バッジ */}
      {!feasible && (
        <div style={{
          padding: '12px 16px',
          background: 'rgba(239, 68, 68, 0.2)',
          border: `1px solid ${COLOR.red}`,
          borderRadius: '8px',
        }}>
          <span style={{ color: COLOR.red, fontWeight: 'bold', fontSize: '14px' }}>
            ⚠️ {t('generic4dsl.infeasibleSolution')}
          </span>
        </div>
      )}

      {/* KPIカード（共通） */}
      {kpiCards.length > 0 && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: '12px',
        }}>
          {kpiCards.map((card) => (
            <div
              key={card.id}
              style={{
                padding: '16px',
                background: COLOR.surface,
                border: `1px solid ${COLOR.border}`,
                borderRadius: '8px',
                borderLeft: `4px solid ${card.color}`,
              }}
            >
              <div style={{ fontSize: '12px', color: COLOR.muted, marginBottom: '4px' }}>
                {card.icon && <span>{card.icon} </span>}
                {card.label}
              </div>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: COLOR.text }}>
                {card.value}
              </div>
              {card.sub && (
                <div style={{ fontSize: '11px', color: '#666', marginTop: '4px' }}>
                  {card.sub}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* アラート（共通） */}
      {alerts.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <div style={{ fontSize: '14px', fontWeight: 'bold', color: COLOR.text, marginBottom: '4px' }}>
            🔔 {t('generic4dsl.alerts')}
          </div>
          {alerts.map((alert) => (
            <div
              key={alert.id}
              style={{
                padding: '12px 16px',
                background: alert.severity === 'CRITICAL' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(245, 158, 11, 0.1)',
                border: `1px solid ${alert.severity === 'CRITICAL' ? COLOR.red : COLOR.orange}`,
                borderRadius: '8px',
              }}
            >
              <div style={{ fontWeight: 'bold', marginBottom: '4px', fontSize: '13px' }}>
                {alert.severity === 'CRITICAL' ? '🔴' : '⚠️'} {alert.title}
              </div>
              <div style={{ fontSize: '12px', color: '#ccc' }}>{alert.message}</div>
            </div>
          ))}
        </div>
      )}

      {/* 詳細テーブル（V8.6: table_sections） */}
      {uiDsl.table_sections && uiDsl.table_sections.length > 0 && (
        <GenericResultTable sections={uiDsl.table_sections} />
      )}

      {/* Gantt（汎用: table_sectionsと同じパターンで、ドメインのui_converterが
          gantt_tasksを出力していれば表示する。table_sections同様、対応していない
          ドメインでは単に何も出ない） */}
      {uiDsl.gantt_tasks && uiDsl.gantt_tasks.length > 0 && (
        <div style={{
          height: '420px',
          // flexShrink:0が無いと、overflow:'hidden'を持つflexアイテムの自動最小サイズは
          // 0として扱われる（CSS Flexboxの仕様）。KPIカード/アラート/テーブルなど
          // 他のブロックはoverflowを設定していないため中身の分だけの最小高さを保持するが、
          // このGanttブロックだけ縮む余地が0になり、他ブロックとの合計が親の高さを
          // 超えたとき、ここだけほぼ0まで潰れて「見えない」状態になっていた。
          flexShrink: 0,
          background: COLOR.surface,
          border: `1px solid ${COLOR.border}`,
          borderRadius: '8px',
          overflow: 'hidden',
        }}>
          <TaskGantt
            tasks={uiDsl.gantt_tasks as GanttTask[]}
            currentTime={ganttCurrentTime}
            makespan={uiDsl.gantt_makespan ?? 0}
            highlightId={ganttHighlightId}
            onHighlightChange={setGanttHighlightId}
            onTimeChange={setGanttCurrentTime}
            problemClass={domain.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase()).replace(/\s+/g, '')}
          />
        </div>
      )}

      {/* ドメイン固有データ（JSON表示） */}
      <details style={{ marginTop: '16px' }}>
        <summary style={{
          cursor: 'pointer',
          color: COLOR.muted,
          fontSize: '12px',
          padding: '8px 12px',
          background: COLOR.surface,
          border: `1px solid ${COLOR.border}`,
          borderRadius: '6px',
          userSelect: 'none',
        }}>
          📄 {t('generic4dsl.showRawData')}
        </summary>
        <pre style={{
          marginTop: '8px',
          padding: '12px',
          background: '#0a0a0c',
          border: `1px solid ${COLOR.border}`,
          borderRadius: '8px',
          fontSize: '11px',
          color: COLOR.muted,
          overflow: 'auto',
          maxHeight: '400px',
          fontFamily: 'monospace',
        }}>
          {JSON.stringify(uiDsl, null, 2)}
        </pre>
      </details>
    </div>
  );
}
