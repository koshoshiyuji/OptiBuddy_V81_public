import React from "react";
import { useTranslation } from "react-i18next";
import type { SnapshotContainer, Task, HighlightId, DslContainer, YardSolverConfig } from "../domain/types";

// ★ 属性バッジ定義
const ATTR_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  REEFER:      { label: "❄",  color: "#00b4d8", bg: "rgba(0,180,216,0.18)" },
  OOG:         { label: "⬛", color: "#9b5de5", bg: "rgba(155,93,229,0.18)" },
  IMO_CLASS_1: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_2: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_3: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_4: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_5: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_6: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_7: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_8: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
  IMO_CLASS_9: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.18)" },
};

function getAttrBadge(attrs: string[]) {
  for (const a of attrs) {
    if (a.startsWith("IMO_")) return ATTR_BADGE[a] ?? ATTR_BADGE["IMO_CLASS_1"];
  }
  if (attrs.includes("OOG"))    return ATTR_BADGE["OOG"];
  if (attrs.includes("REEFER")) return ATTR_BADGE["REEFER"];
  return null;
}

// ★ スロット種別
type SlotKind = "reefer" | "imo" | "oog_adjacent" | "normal";

interface RestrictedZones {
  reefer?: { bay: number; row: number; tier?: number }[];
  imo?: { bay: number; row?: number; rows?: number[] }[];
}

// ★ スロット種別の判定
function getSlotKind(
  bay: number,
  row: number,
  zones: RestrictedZones,
  oogExcluded: Set<string>
): SlotKind {
  // IMO優先（危険物は最優先）
  for (const z of zones.imo ?? []) {
    if (z.bay !== bay) continue;
    const rows = z.rows ?? (z.row !== undefined ? [z.row] : []);
    if (rows.includes(row)) return "imo";
  }
  // Reefer
  for (const s of zones.reefer ?? []) {
    if (s.bay === bay && s.row === row) return "reefer";
  }
  // OOG隣接
  if (oogExcluded.has(`${bay}:${row}`)) return "oog_adjacent";
  return "normal";
}

// ★ スロット種別ごとのスタイル（空スロット）
const SLOT_STYLE: Record<SlotKind, React.CSSProperties> = {
  reefer:       { background: "rgba(0,180,216,0.06)",   border: "1px dashed rgba(0,180,216,0.5)" },
  imo:          { background: "rgba(255,107,53,0.06)",   border: "1px dashed rgba(255,107,53,0.5)" },
  oog_adjacent: { background: "repeating-linear-gradient(45deg,rgba(155,93,229,0.08) 0px,rgba(155,93,229,0.08) 3px,transparent 3px,transparent 8px)", border: "1px dashed rgba(155,93,229,0.4)" },
  normal:       { background: "rgba(255,255,255,0.02)",  border: "1px solid #2a2a40" },
};

// ★ コンテナ+スロット種別から背景・ボーダーを決定
function getContainerStyle(
  attrs: string[],
  slotKind: SlotKind,
  isMainHighlight: boolean,
  isRelatedHighlight: boolean
): { bg: string; border: string } {
  if (isMainHighlight)    return { bg: "#ff9f1c", border: "2px solid #fff" };
  if (isRelatedHighlight) return { bg: "#4a90e2", border: "1px solid #6aabff" };

  const hasReefer = attrs.includes("REEFER");
  const hasImo    = attrs.some(a => a.startsWith("IMO_"));
  const hasOog    = attrs.includes("OOG");

  // コンテナ属性 × スロット種別の組み合わせ
  if (hasReefer && slotKind === "reefer") return { bg: "rgba(0,180,216,0.30)", border: "1px solid #00b4d8" };        // 正常
  if (hasReefer && slotKind !== "reefer") return { bg: "rgba(255,60,60,0.25)",  border: "2px solid #ff4444" };        // 違反
  if (hasImo    && slotKind === "imo")    return { bg: "rgba(255,107,53,0.30)", border: "1px solid #ff6b35" };        // 正常
  if (hasImo    && slotKind !== "imo")    return { bg: "rgba(255,60,60,0.25)",  border: "2px solid #ff4444" };        // 違反
  if (hasOog)                             return { bg: "rgba(155,93,229,0.30)", border: "1px solid #9b5de5" };        // OOGコンテナ
  if (slotKind === "reefer")              return { bg: "rgba(255,60,60,0.20)",  border: "2px solid #ff6666" };        // 非Reeferが占有（違反）
  if (slotKind === "imo")                 return { bg: "rgba(255,60,60,0.20)",  border: "2px solid #ff6666" };        // 非IMOが占有（違反）
  return { bg: "#34344a", border: "1px solid #444" };
}

interface YardViewProps {
  containerData: Record<string, SnapshotContainer>;
  bayNumber: number;
  tasks: Task[];
  currentTime: number;
  highlightId?: HighlightId;
  onHighlightChange: (h: HighlightId) => void;
  dslContainers?: DslContainer[];
  // ★ DSL config / restricted_zones を追加
  config?: YardSolverConfig;
  restrictedZones?: RestrictedZones;
}

export function YardView({
  containerData,
  bayNumber,
  tasks,
  currentTime,
  highlightId,
  onHighlightChange,
  dslContainers = [],
  config,
  restrictedZones = {},
}: YardViewProps) {
  const { t } = useTranslation();

  const safeHighlight: HighlightId = highlightId?.kind ? highlightId : { kind: "none" };
  const isActive        = safeHighlight.kind === "active";
  const currentHoverId  = isActive ? safeHighlight.hoverContainerId : null;
  const relatedIds      = isActive ? safeHighlight.relatedContainerIds : [];
  const activeTask      = isActive ? tasks.find(t => t.id === safeHighlight.selectedIssueId) : undefined;

  // ★ グリッドサイズ: DSL config > constants フォールバック
  const maxRows  = config?.max_rows  ?? 10;
  const maxTiers = config?.max_tiers ?? 6;
  const ROWS  = Array.from({ length: maxRows  }, (_, i) => i + 1);
  const TIERS = Array.from({ length: maxTiers }, (_, i) => maxTiers - i); // 上から

  // ★ コンテナID → attrs マップ
  const attrsMap = React.useMemo(() => {
    const map: Record<string, string[]> = {};
    for (const c of dslContainers) {
      if (c.attrs?.length) map[String(c.id)] = c.attrs as string[];
    }
    return map;
  }, [dslContainers]);

  // ★ OOG隣接スロットセット (bay:row キー)
  const oogExcluded = React.useMemo(() => {
    const set = new Set<string>();
    for (const c of dslContainers) {
      if (!c.attrs?.includes("OOG")) continue;
      const { bay, row } = c.yard;
      if (bay == null || row == null) continue;
      for (const delta of [-1, +1]) {
        const adj = row + delta;
        if (adj >= 1) set.add(`${bay}:${adj}`);
      }
    }
    return set;
  }, [dslContainers]);

  // ★ このベイに存在する属性の凡例
  const legendItems = React.useMemo(() => {
    const items: { label: string; color: string; bg: string; desc: string }[] = [];
    const hasReefer = (restrictedZones.reefer ?? []).some(s => s.bay === bayNumber);
    const hasImo    = (restrictedZones.imo    ?? []).some(z => z.bay === bayNumber);
    const hasOog    = dslContainers.some(c => c.attrs?.includes("OOG") && c.yard.bay === bayNumber);
    if (hasReefer) items.push({ label: "❄", color: "#00b4d8", bg: "rgba(0,180,216,0.18)", desc: t('yardView.legendReefer') });
    if (hasImo)    items.push({ label: "☢", color: "#ff6b35", bg: "rgba(255,107,53,0.18)", desc: t('yardView.legendImo') });
    if (hasOog)    items.push({ label: "⬛", color: "#9b5de5", bg: "rgba(155,93,229,0.18)", desc: t('yardView.legendOog') });
    return items;
  }, [restrictedZones, dslContainers, bayNumber, t]);

  return (
    <div style={{ background: "#16161a", padding: "15px", borderRadius: "10px", border: "1px solid #2a2a30" }}>
      {/* ヘッダー */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "15px" }}>
        <h3 style={{ color: "#888", fontSize: "0.7rem", margin: 0, textTransform: "uppercase" }}>
          🏗️ YARD - BAY {bayNumber}
        </h3>
        {/* 凡例 */}
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          {legendItems.map(item => (
            <span key={item.label} title={item.desc} style={{
              fontSize: "9px", padding: "1px 6px", borderRadius: "3px",
              background: item.bg, border: `1px solid ${item.color}`,
              color: item.color, fontWeight: 700, cursor: "help",
            }}>
              {item.label} {item.desc}
            </span>
          ))}
        </div>
      </div>

      {/* グリッド */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${ROWS.length}, 1fr)`, gap: "4px" }}>
        {TIERS.map(tier =>
          ROWS.map(row => {
            const slotKind = getSlotKind(bayNumber, row, restrictedZones, oogExcluded);

            const cellContainers = Object.values(containerData).filter(c =>
              c.visibleInYard &&
              Number(c.yard.bay)  === bayNumber &&
              Number(c.yard.row)  === row &&
              Number(c.yard.tier) === tier
            );

            const isEmpty = cellContainers.length === 0;
            const slotStyle = isEmpty ? SLOT_STYLE[slotKind] : { background: "rgba(255,255,255,0.04)", border: "1px solid #2a2a40" };

            // 空スロットのラベル
            const slotLabel = isEmpty && slotKind !== "normal" ? (
              slotKind === "reefer"       ? <span style={{ fontSize: "8px", color: "rgba(0,180,216,0.5)", userSelect: "none" }}>❄</span>
              : slotKind === "imo"        ? <span style={{ fontSize: "8px", color: "rgba(255,107,53,0.5)", userSelect: "none" }}>☢</span>
              : slotKind === "oog_adjacent" ? <span style={{ fontSize: "7px", color: "rgba(155,93,229,0.5)", userSelect: "none" }}>▧</span>
              : null
            ) : null;

            return (
              <div
                key={`yard-${bayNumber}-${row}-${tier}`}
                title={isEmpty ? `bay=${bayNumber} row=${row} tier=${tier} [${slotKind}]` : undefined}
                style={{
                  aspectRatio: "1.5 / 1",
                  position: "relative",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  ...slotStyle,
                }}
              >
                {slotLabel}
                {cellContainers.map(c => {
                  const attrs           = attrsMap[c.containerId] ?? [];
                  const isMainHighlight = currentHoverId === c.containerId;
                  const isRelated       = relatedIds.includes(c.containerId);
                  const { bg, border }  = getContainerStyle(attrs, slotKind, isMainHighlight, isRelated);
                  const badge           = getAttrBadge(attrs);

                  return (
                    <div
                      key={c.containerId}
                      onMouseEnter={() => {
                        const t = tasks.find(tk =>
                          tk.containerId === c.containerId &&
                          currentTime >= tk.start && currentTime < tk.end
                        ) || [...tasks]
                          .filter(tk => tk.containerId === c.containerId && currentTime >= tk.end)
                          .sort((a, b) => b.end - a.end)[0]
                          || tasks.find(tk => tk.containerId === c.containerId);
                        onHighlightChange({
                          kind: "active",
                          hoverContainerId: c.containerId,
                          selectedIssueId: t?.id,
                          relatedContainerIds: [c.containerId],
                        });
                      }}
                      onMouseLeave={() => onHighlightChange({ kind: "none" })}
                      style={{
                        width: "90%", height: "80%",
                        background: bg,
                        border,
                        borderRadius: "3px",
                        display: "flex", justifyContent: "center", alignItems: "center",
                        fontSize: "9px", fontWeight: "bold", color: "#fff",
                        cursor: "pointer",
                        zIndex: isMainHighlight ? 10 : 1,
                        transition: "all 0.1s ease",
                        position: "relative",
                        overflow: "visible",
                      }}
                    >
                      {badge && !isMainHighlight && (
                        <span style={{
                          position: "absolute", top: "-5px", left: "-5px",
                          fontSize: "8px", lineHeight: 1,
                          background: badge.bg, border: `1px solid ${badge.color}`,
                          borderRadius: "2px", padding: "0 2px",
                          color: badge.color, zIndex: 20, pointerEvents: "none",
                        }}>
                          {badge.label}
                        </span>
                      )}
                      {isMainHighlight && activeTask
                        ? `${c.containerId}(${activeTask.operation[0]})`
                        : c.containerId}
                    </div>
                  );
                })}
              </div>
            );
          })
        )}
      </div>

      {/* フッター：グリッドサイズ表示 */}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: "10px", fontSize: "9px", color: "#444" }}>
        <span>ROW 1–{maxRows}</span>
        <span>TIER 1–{maxTiers}</span>
      </div>
    </div>
  );
}
