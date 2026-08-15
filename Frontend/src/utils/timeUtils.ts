// src/utils/timeUtils.ts

/**
 * 分単位の数値を hh:mm 形式の文字列に変換
 * 例: 480 → "08:00", 1439 → "23:59"
 */
export function minutesToHHMM(min: number): string {
  const totalMin = Math.max(0, Math.floor(min));
  const h = Math.floor(totalMin / 60) % 24;
  const m = totalMin % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/**
 * "hh:mm" 形式の文字列を分単位の数値に変換
 * 例: "08:00" → 480
 */
export function HHMMToMinutes(hhmm: string): number {
  const parts = hhmm.split(':');
  const h = parseInt(parts[0] ?? '0', 10);
  const m = parseInt(parts[1] ?? '0', 10);
  if (isNaN(h) || isNaN(m)) return 0;
  return h * 60 + m;
}

/**
 * DSL の operation_start フィールドを分単位に変換
 * - "08:00" 形式の文字列 → 480
 * - 数値（分単位）→ そのまま返す
 * - undefined / null → 0
 */
export function parseOperationStart(value: string | number | undefined | null): number {
  if (value == null) return 0;
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && value.includes(':')) return HHMMToMinutes(value);
  const parsed = parseInt(value, 10);
  return isNaN(parsed) ? 0 : parsed;
}
