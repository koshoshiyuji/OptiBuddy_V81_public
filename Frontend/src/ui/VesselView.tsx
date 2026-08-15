import React from "react";
import type { SnapshotContainer, Task, HighlightId, DslContainer, YardSolverConfig } from "../domain/types";

// ★ 属性バッジ定義（YardViewと共通）
const ATTR_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  REEFER:      { label: "❄",  color: "#00b4d8", bg: "rgba(0,180,216,0.25)" },
  OOG:         { label: "⬛", color: "#9b5de5", bg: "rgba(155,93,229,0.25)" },
  IMO_CLASS_1: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_2: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_3: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_4: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_5: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_6: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_7: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_8: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
  IMO_CLASS_9: { label: "☢",  color: "#ff6b35", bg: "rgba(255,107,53,0.25)" },
};

function getAttrBadge(attrs: string[]) {
  for (const a of attrs) {
    if (a.startsWith("IMO_")) return ATTR_BADGE[a] ?? ATTR_BADGE["IMO_CLASS_1"];
  }
  if (attrs.includes("OOG"))    return ATTR_BADGE["OOG"];
  if (attrs.includes("REEFER")) return ATTR_BADGE["REEFER"];
  return null;
}

function getContainerBg(attrs: string[], isMain: boolean, isRelated: boolean): { bg: string; border: string } {
  if (isMain)    return { bg: "#ff9f1c", border: "2px solid #fff" };
  if (isRelated) return { bg: "#4a90e2", border: "1px solid #6aabff" };
  if (attrs.includes("REEFER"))         return { bg: "rgba(0,180,216,0.30)",  border: "1px solid #00b4d8" };
  if (attrs.some(a => a.startsWith("IMO_"))) return { bg: "rgba(255,107,53,0.30)", border: "1px solid #ff6b35" };
  if (attrs.includes("OOG"))            return { bg: "rgba(155,93,229,0.30)", border: "1px solid #9b5de5" };
  return { bg: "#34344a", border: "1px solid #444" };
}

interface VesselViewProps {
  containerData: Record<string, SnapshotContainer>;
  bayNumber: number;
  tasks: Task[];
  currentTime: number;
  highlightId?: HighlightId;
  onHighlightChange: (h: HighlightId) => void;
  // ★ 追加
  dslContainers?: DslContainer[];
  config?: YardSolverConfig;
}

export function VesselView({
  containerData,
  bayNumber,
  tasks,
  currentTime,
  highlightId,
  onHighlightChange,
  dslContainers = [],
  config,
}: VesselViewProps) {

  const isActive           = highlightId?.kind === "active";
  const hoveredContainerId = isActive ? highlightId.hoverContainerId : null;
  const relatedIds         = isActive ? (highlightId.relatedContainerIds ?? []) : [];
  const activeTask         = isActive
    ? tasks.find(t => `${t.containerId}_${t.operation}_${t.start}` === highlightId.selectedIssueId)
    : undefined;

  // ★ グリッドサイズ: DSL config > フォールバック
  const maxRows  = config?.max_vessel_rows  ?? 10;
  const maxTiers = config?.max_vessel_tiers ?? 8;
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

  // ★ このベイに存在する属性コンテナの凡例
  const legendAttrs = React.useMemo(() => {
    const found = new Set<string>();
    for (const c of dslContainers) {
      if (!c.attrs?.length) continue;
      // 船側のbayを確認
      if (Number(c.ship?.bay) !== bayNumber) continue;
      for (const a of c.attrs) {
        if (a.startsWith("IMO_")) { found.add("IMO"); break; }
        if (a === "REEFER") found.add("REEFER");
        if (a === "OOG")    found.add("OOG");
      }
    }
    return Array.from(found);
  }, [dslContainers, bayNumber]);

  return (
    <div style={{ background: "#16161a", padding: "15px", borderRadius: "10px", border: "1px solid #2a2a30" }}>
      {/* ヘッダー */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "15px" }}>
        <h3 style={{ color: "#888", fontSize: "0.7rem", margin: 0, textTransform: "uppercase", letterSpacing: "1px" }}>
          🚢 VESSEL - BAY {bayNumber}
        </h3>
        {/* 凡例 */}
        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
          {legendAttrs.map(attr => {
            const key = attr === "IMO" ? "IMO_CLASS_1" : attr;
            const badge = ATTR_BADGE[key];
            if (!badge) return null;
            return (
              <span key={attr} style={{
                fontSize: "9px", padding: "1px 5px", borderRadius: "3px",
                background: badge.bg, border: `1px solid ${badge.color}`,
                color: badge.color, fontWeight: 700,
              }}>
                {badge.label} {attr}
              </span>
            );
          })}
        </div>
      </div>

      {/* グリッド */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${ROWS.length}, 1fr)`, gap: "4px" }}>
        {TIERS.map(tier =>
          ROWS.map(row => {
            const container = Object.values(containerData).find(c =>
              c.visibleInVessel &&
              Number(c.ship.bay)  === bayNumber &&
              Number(c.ship.row)  === row &&
              Number(c.ship.tier) === tier
            );
            const cid     = container?.containerId;
            const attrs   = cid ? (attrsMap[cid] ?? []) : [];
            const isMain  = hoveredContainerId === cid;
            const isRel   = cid ? relatedIds.includes(cid) : false;
            const { bg, border } = cid
              ? getContainerBg(attrs, isMain, isRel)
              : { bg: "rgba(255,255,255,0.02)", border: "1px solid #2a2a40" };
            const badge = cid ? getAttrBadge(attrs) : null;

            return (
              <div
                key={`vessel-${bayNumber}-${row}-${tier}`}
                style={{
                  aspectRatio: "1.5 / 1",
                  background: bg,
                  border,
                  borderRadius: "3px",
                  display: "flex", justifyContent: "center", alignItems: "center",
                  position: "relative",
                }}
              >
                {cid && (
                  <div
                    onMouseEnter={() => {
                      const t = tasks.find(tk =>
                        tk.containerId === cid && tk.start <= currentTime && tk.end > currentTime
                      ) || tasks.find(tk => tk.containerId === cid);
                      onHighlightChange({
                        kind: "active",
                        hoverContainerId: cid,
                        selectedIssueId: t ? `${t.containerId}_${t.operation}_${t.start}` : "",
                        relatedContainerIds: [cid],
                      });
                    }}
                    onMouseLeave={() => onHighlightChange({ kind: "none" })}
                    style={{
                      width: "90%", height: "80%",
                      background: "transparent",
                      display: "flex", justifyContent: "center", alignItems: "center",
                      fontSize: "9px", fontWeight: "bold", color: "#fff",
                      cursor: "pointer", position: "relative", overflow: "visible",
                    }}
                  >
                    {badge && !isMain && (
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
                    {isMain && activeTask ? `${cid}(${activeTask.operation[0]})` : cid}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", marginTop: "10px", fontSize: "9px", color: "#444" }}>
        <span>ROW 1–{maxRows}</span>
        <span>TIER 1–{maxTiers}</span>
      </div>
    </div>
  );
}
