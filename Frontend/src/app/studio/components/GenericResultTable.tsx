// Frontend/src/app/studio/components/GenericResultTable.tsx
//
// GenericResultTable — 汎用結果テーブルコンポーネント
// ============================================================
// 目的:
//   新規ドメイン追加時、{PascalCase}View.tsx の「詳細テーブル」部分を
//   手書きJSXで実装する代わりに、この共通コンポーネントに
//   ui_dsl.table_sections を渡すだけで表示できるようにする。
//
//   これにより:
//     - LLMが新規ドメイン追加時に生成するTSXコード量を削減する
//       （手書きtableはバグ・表記揺れの温床になりやすい）
//     - 列フォーマット・行の重要度ハイライト・CSVダウンロード・
//       列ソートの挙動をドメイン間で統一する
//
// 使い方:
//   import { GenericResultTable } from '../components/GenericResultTable.tsx';
//   <GenericResultTable sections={uiDsl.table_sections ?? []} />
//
// 設計方針（OptiBuddy_V86_addendum.md 参照）:
//   - 表示ロジックのみを担当する。データは Solver Output DSL → UI DSL
//     変換（Backend/dsl_transformer/{snake}_ui_converter.py、
//     Backend/dsl_transformer/table_sections.py 経由）で組み立て済み
//     であることを前提とする。
//   - KPIカード・アラート表示はドメイン固有のまま各Viewで手書きする。
//     本コンポーネントは「詳細テーブル」部分のみを置き換える。
//   - severity（CRITICAL/WARNING/INFO）による行ハイライトは、
//     OverviewView.tsx の SEVERITY_COLOR と統一する。
// ============================================================

import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

// ─── 型定義 ─────────────────────────────────────────────
// Backend/dsl_transformer/table_sections.py の TableColumn /
// build_table_section() が生成する辞書と1:1で対応する。
// 本来は Frontend/src/app/studio/types.ts に移して export すること。

// 'nightShift'/'nightShiftIcon' は2026-07-30 i18n対応で追加（DESIGN_2026-07-29_
// nurse_shift_i18n_implementation_spec.md 1-2節「行データ内の埋め込み値」）。
// backendは完成済み文字列("🌙 夜勤"等)ではなくbool値をセルに渡し、この2フォーマットが
// 表示直前に現在の表示言語で絵文字+ラベル（nightShift）/絵文字のみ（nightShiftIcon）
// へ変換する。
export type TableColumnFormat = 'number' | 'currency' | 'percent' | 'text' | 'badge' | 'nightShift' | 'nightShiftIcon';
export type TableColumnAlign = 'left' | 'right' | 'center';
export type RowSeverity = 'CRITICAL' | 'WARNING' | 'INFO' | null;

export interface TableColumn {
  key: string;
  label: string;
  align?: TableColumnAlign;
  format?: TableColumnFormat;
  unit?: string;
}

export interface TableRow {
  _id: string;
  _severity?: RowSeverity;
  [key: string]: unknown;
}

export interface TableSection {
  id: string;
  title: string;
  columns: TableColumn[];
  rows: TableRow[];
  allow_download?: boolean; // default: true
}

// ─── デザイントークン（OptiBuddy_V81_spec.md 4-4節と統一）───
const COLOR = {
  bg: '#08080a', surface: '#121216', border: '#2a2a30',
  text: '#eee', muted: '#888',
  blue: '#3b82f6', green: '#10b981', orange: '#f97316',
  purple: '#8b5cf6', teal: '#14b8a6', red: '#ef4444', yellow: '#f59e0b',
};

const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#ff4d4d',
  WARNING: '#ffb86c',
  INFO: '#8be9fd',
};

const CARD: React.CSSProperties = {
  background: COLOR.surface,
  border: `1px solid ${COLOR.border}`,
  borderRadius: '10px',
  padding: '16px',
};

const TH: React.CSSProperties = {
  padding: '6px 10px',
  textAlign: 'left',
  color: COLOR.muted,
  fontWeight: 800,
  fontSize: '9px',
  letterSpacing: '0.5px',
  borderBottom: `1px solid ${COLOR.border}`,
  cursor: 'pointer',
  userSelect: 'none',
  whiteSpace: 'nowrap',
};

const TD: React.CSSProperties = {
  padding: '8px 10px',
  fontSize: '11px',
  borderBottom: '1px solid #1a1a24',
  verticalAlign: 'middle',
};

// ─── フォーマッタ ───────────────────────────────────────
// t: react-i18next の t関数。'nightShift'/'nightShiftIcon' のみ使用する
// （他フォーマットは従来通り言語非依存の表示のため不要）。
function formatCell(value: unknown, column: TableColumn, t: (key: string) => string): string {
  if (value === null || value === undefined) return '—';
  switch (column.format) {
    case 'number':
      return typeof value === 'number'
        ? `${value.toLocaleString()}${column.unit ? ` ${column.unit}` : ''}`
        : String(value);
    case 'currency':
      return typeof value === 'number'
        ? `¥${value.toLocaleString()}`
        : String(value);
    case 'percent':
      return typeof value === 'number'
        ? `${Math.round(value * 100)}%`
        : String(value);
    case 'nightShift':
      return value ? t('genericResultTable.nightShift.night') : t('genericResultTable.nightShift.day');
    case 'nightShiftIcon':
      return value ? t('genericResultTable.nightShift.nightIcon') : t('genericResultTable.nightShift.dayIcon');
    case 'badge':
    case 'text':
    default:
      return String(value);
  }
}

function formatColor(value: unknown, column: TableColumn): string {
  if (column.format === 'badge') {
    const v = String(value).toUpperCase();
    if (v === 'CRITICAL' || v === 'ERROR' || v === 'CLOSED') return COLOR.red;
    if (v === 'WARNING' || v === 'OVER') return COLOR.orange;
    if (v === 'OK' || v === 'OPEN' || v === 'RESOLVED') return COLOR.green;
    return COLOR.muted;
  }
  if (column.format === 'currency') return COLOR.orange;
  if (column.format === 'number') return COLOR.teal;
  return COLOR.text;
}

// ─── CSVダウンロード ─────────────────────────────────────
// 2026-07-30 i18n対応: ヘッダーはcolumn.label（表示言語に連動）ではなく
// column.key（言語非依存の安定識別子）を使う。表示言語を切り替えてもCSVの
// 列名が変わらなくなり、後続の自動処理が壊れなくなる
// （DESIGN_2026-07-29_ui_i18n_status_and_plan.md 3.1節）。
function downloadCsv(section: TableSection, t: (key: string) => string): void {
  const header = section.columns.map((c) => c.key).join(',');
  const lines = section.rows.map((row) =>
    section.columns
      .map((c) => {
        const raw = row[c.key];
        // nightShift/nightShiftIcon はセル値がbool（is_night_shift等）のため、
        // "true"/"false"ではなく表示と同じ絵文字+ラベルをCSVにも出力する。
        const cell = c.format === 'nightShift' || c.format === 'nightShiftIcon'
          ? formatCell(raw, c, t)
          : raw === null || raw === undefined ? '' : String(raw);
        // カンマ・改行・ダブルクォートを含む場合はダブルクォートで囲む
        if (/[",\n]/.test(cell)) {
          return `"${cell.replace(/"/g, '""')}"`;
        }
        return cell;
      })
      .join(',')
  );
  const csv = [header, ...lines].join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${section.id || 'table'}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ─── 単一セクションのテーブル ─────────────────────────────
function SingleTableSection({ section }: { section: TableSection }) {
  const { t } = useTranslation();
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);

  const sortedRows = useMemo(() => {
    if (!sortKey) return section.rows;
    const copy = [...section.rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortAsc ? av - bv : bv - av;
      }
      const as = String(av ?? '');
      const bs = String(bv ?? '');
      return sortAsc ? as.localeCompare(bs) : bs.localeCompare(as);
    });
    return copy;
  }, [section.rows, sortKey, sortAsc]);

  const handleHeaderClick = (key: string) => {
    if (sortKey === key) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const allowDownload = section.allow_download !== false;

  return (
    <div style={{ ...CARD, flex: '0 0 auto', minHeight: '160px', maxHeight: '400px', overflow: 'auto' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: '12px',
      }}>
        <div style={{
          fontSize: '11px', fontWeight: 800, color: COLOR.text, letterSpacing: '1px',
          display: 'flex', alignItems: 'center', gap: '8px',
        }}>
          {section.title}
          <span style={{
            fontSize: '10px', padding: '1px 8px', borderRadius: '8px',
            background: '#ffffff11', border: `1px solid ${COLOR.border}`,
            color: COLOR.muted, fontWeight: 900,
          }}>
            {t('genericResultTable.rowCount', { count: section.rows.length })}
          </span>
        </div>
        {allowDownload && section.rows.length > 0 && (
          <button
            onClick={() => downloadCsv(section, t)}
            style={{
              padding: '4px 12px', fontSize: '10px', fontWeight: 700, cursor: 'pointer',
              background: 'transparent', border: `1px solid ${COLOR.border}`,
              color: COLOR.muted, borderRadius: '5px',
            }}
          >
            ⬇ CSV
          </button>
        )}
      </div>

      {section.rows.length === 0 ? (
        <div style={{ color: '#444', textAlign: 'center', padding: '24px', fontSize: '12px' }}>
          {t('genericResultTable.noData')}
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11px' }}>
          <thead>
            <tr>
              {section.columns.map((col) => (
                <th
                  key={col.key}
                  style={{ ...TH, textAlign: col.align ?? 'left' }}
                  onClick={() => handleHeaderClick(col.key)}
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span style={{ marginLeft: '4px' }}>{sortAsc ? '▲' : '▼'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row) => {
              const severity = row._severity ?? null;
              const rowColor = severity ? SEVERITY_COLOR[severity] : undefined;
              return (
                <tr
                  key={row._id}
                  style={{
                    borderLeft: rowColor ? `3px solid ${rowColor}` : undefined,
                    background: rowColor ? `${rowColor}0d` : undefined,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = rowColor ? `${rowColor}22` : '#1a1a24')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = rowColor ? `${rowColor}0d` : 'transparent')}
                >
                  {section.columns.map((col) => (
                    <td
                      key={col.key}
                      style={{
                        ...TD,
                        textAlign: col.align ?? 'left',
                        fontFamily:
                          col.format === 'number' || col.format === 'currency' || col.format === 'percent'
                            ? 'monospace'
                            : undefined,
                        color: formatColor(row[col.key], col),
                        fontWeight: col.format === 'badge' ? 800 : undefined,
                      }}
                    >
                      {formatCell(row[col.key], col, t)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── メインコンポーネント ─────────────────────────────────
export function GenericResultTable({ sections }: { sections: TableSection[] }) {
  const { t } = useTranslation();
  if (!sections || sections.length === 0) {
    return (
      <div style={{ ...CARD, textAlign: 'center', color: COLOR.muted, fontSize: '12px' }}>
        {t('genericResultTable.noTableData')}
      </div>
    );
  }
  // 2026-07-14: 以前は flex:1 でGeneric4DSLView内の残り空間を埋める設計だったが、
  // Ganttパネル（固定高さ420px）追加後、複数ブロックが同時にflex-growを取り合う
  // ことで空き領域が負になり、各テーブルの高さがほぼ0まで潰れる不具合が発生した。
  // Generic4DSLViewの外側コンテナは元々overflow:autoでページ全体がスクロールする
  // 設計なので、ここは残り空間を取り合わずに自然な高さで積み上げる方式に変更する。
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', flex: '0 0 auto' }}>
      {sections.map((section) => (
        <SingleTableSection key={section.id} section={section} />
      ))}
    </div>
  );
}
