// ============================================================
// IssueListView.tsx  (V7: 一括承認追加)
// ============================================================
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { Issue, HighlightId, IssueAction } from "../domain/types";

interface IssueListViewProps {
  issues: Issue[];
  issueActions: Record<string, IssueAction>;
  highlightId: HighlightId;
  onHighlightChange: (h: HighlightId) => void;
  onApplyFix: (issue: Issue, mode: "FIX" | "ACCEPT") => void;
  onBulkAccept?: (issues: Issue[]) => void; // V7追加
}

function canFix(issue: Issue): boolean {
  if (issue.id.startsWith("is_ship_"))   return false;
  if (issue.id.startsWith("weight-"))    return false;
  if (issue.id.startsWith("attr_reefer_")) return false;
  if (issue.id.startsWith("attr_imo_"))   return false;
  if (issue.id.startsWith("attr_oog_"))   return false;
  if (issue.id.startsWith("shift_"))      return false;
  if ((issue as any).skipped)            return false;
  return true;
}

function acceptLabel(issue: Issue, t: TFunction): string {
  if (issue.id.startsWith("is_ship_"))    return t('issueListView.acceptLabelShip');
  if (issue.id.startsWith("weight-"))     return t('issueListView.acceptLabelWeight');
  if (issue.id.startsWith("attr_reefer_")) return t('issueListView.acceptLabelReefer');
  if (issue.id.startsWith("attr_imo_"))   return t('issueListView.acceptLabelImo');
  if (issue.id.startsWith("attr_oog_"))   return t('issueListView.acceptLabelOog');
  if (issue.id.startsWith("shift_"))      return t('issueListView.acceptLabelShift');
  return "ACCEPT";
}

function attrBadge(issueId: string): { label: string; color: string } | null {
  if (issueId.startsWith("attr_reefer_")) return { label: "❄ REEFER", color: "#00b4d8" };
  if (issueId.startsWith("attr_imo_"))   return { label: "☢ IMO",    color: "#ff6b35" };
  if (issueId.startsWith("attr_oog_"))   return { label: "⬛ OOG",   color: "#9b5de5" };
  if (issueId.startsWith("shift_"))      return { label: "⏰ SHIFT", color: "#ffa500" };
  return null;
}

// 2026-07-24追加: 解チェッカー（category="SOLVER"）が検出したissueであることを示す
// バッジ。バグ疑いを示す情報であり、業務系issueと視覚的に区別する。
function solverCheckerBadge(issue: Issue): { label: string; color: string } | null {
  if ((issue as any).category !== "SOLVER") return null;
  return { label: "🛠 SOLVER", color: "#ff7043" };
}

// 2026-07-24追加: 非同期解チェッカー（DESIGN_2026-07-21 3-3節）が本来のsolve
// レスポンスより後に検出し、/checks/status/<job_id> 経由で追記されたissueであることを
// 示すバッジ。「警告追記のみ」方針（HANDOFF_2026-07-24参照）のため、既存の解を
// 自動的に覆すことはしていない旨をユーザーに明示する。ラベルはi18nキー経由。
function isDeferredCheck(issue: Issue): boolean {
  return (issue as any)._deferred_check === true;
}
const DEFERRED_BADGE_COLOR = "#f1c40f";

function issueMessage(issue: Issue, t: TFunction): string {
  if ((issue as any).skipped) {
    return (issue as any).skip_reason || t('issueListView.autoSkippedMessage');
  }
  if (issue.id.startsWith("is_ship_")) {
    return t('issueListView.shipConflictMessage');
  }
  if (issue.id.startsWith("weight-")) {
    return t('issueListView.weightConflictMessage');
  }
  return issue.message ?? "";
}

export function IssueListView({
  issues,
  issueActions,
  highlightId,
  onHighlightChange,
  onApplyFix,
  onBulkAccept,
}: IssueListViewProps) {
  const { t } = useTranslation();
  const [hideAccepted, setHideAccepted] = useState(true);

  const acceptedCount = issues.filter(
    (i) => issueActions[i.id] === "ACCEPT" || (i as any).skipped
  ).length;

  const pendingIssues = issues.filter(
    (i) => issueActions[i.id] !== "ACCEPT" && !(i as any).skipped
  );

  const visibleIssues = hideAccepted ? pendingIssues : issues;

  const handleBulkAccept = () => {
    if (onBulkAccept && pendingIssues.length > 0) onBulkAccept(pendingIssues);
  };

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      flex: 1,
      minHeight: 0,
      height: "100%",
      boxSizing: "border-box",
      padding: "12px 8px 0 12px",
    }}>
      {/* ヘッダー */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        borderBottom: "1px solid #333",
        paddingBottom: "10px",
        marginBottom: "10px",
        flexShrink: 0,
      }}>
        <h3 style={{ margin: 0, color: "#888", fontSize: "0.8rem" }}>
          ⚠️ DETECTED ISSUES ({visibleIssues.length}
          {hideAccepted && acceptedCount > 0 && (
            <span style={{ color: "#555", fontWeight: "normal" }}>
              {" "}/ {t('issueListView.acceptedCount', { count: acceptedCount })}
            </span>
          )}
          )
        </h3>

        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
          {/* V7: 一括ACCEPT */}
          {pendingIssues.length > 0 && onBulkAccept && (
            <button
              onClick={handleBulkAccept}
              style={{
                fontSize: "9px", fontWeight: 900, letterSpacing: "1px",
                padding: "3px 8px", borderRadius: "4px", cursor: "pointer",
                background: "#28a74522", border: "1px solid #28a745", color: "#28a745",
              }}
            >
              {t('issueListView.acceptAllCount', { count: pendingIssues.length })}
            </button>
          )}

          {acceptedCount > 0 && (
            <button
              onClick={() => setHideAccepted((v) => !v)}
              style={{
                fontSize: "9px",
                fontWeight: 900,
                letterSpacing: "1px",
                padding: "3px 8px",
                borderRadius: "4px",
                cursor: "pointer",
                transition: "all 0.15s",
                background: hideAccepted ? "transparent" : "#28a74522",
                border: `1px solid ${hideAccepted ? "#444" : "#28a745"}`,
                color: hideAccepted ? "#555" : "#28a745",
              }}
            >
              {hideAccepted ? t('issueListView.showAccepted') : t('issueListView.pendingOnly')}
            </button>
          )}
        </div>
      </div>

      {/* イシューリスト（2列グリッド） */}
      <div style={{
        flex: 1,
        minHeight: 0,
        overflowY: "auto",
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gridAutoRows: "min-content",
        gap: "12px",
        paddingRight: "5px",
        paddingBottom: "8px",
      }}>
        {visibleIssues.length === 0 && (
          <div style={{ gridColumn: "1 / -1", color: "#444", fontSize: "12px", textAlign: "center", paddingTop: "24px" }}>
            {t('issueListView.noPendingIssues')}
          </div>
        )}

        {visibleIssues.map((issue) => {
          const action      = issueActions[issue.id] || "NONE";
          // const isHighlighted =
          //   highlightId.kind === "active" &&
          //   highlightId.selectedIssueId === issue.id;
          const isHighlighted = 
            highlightId.kind === "active" && 
            (
              highlightId.selectedIssueId === issue.id || 
              // ここが重要：ガントのレーンIDと、検索ヒットID(relatedContainerIds)を比較
              highlightId.relatedContainerIds?.includes(issue.containerId ?? '') // ← issue.containerIdがレーンIDならこれ
            );

          const fixable   = !!issue.containerId && canFix(issue);
          const isShip    = issue.id.startsWith("is_ship_");
          const isSkipped = (issue as any).skipped === true;
          const isAccepted = action === "ACCEPT";
          const badge     = attrBadge(issue.id);
          const solverBadge   = solverCheckerBadge(issue);
          const showDeferredBadge = isDeferredCheck(issue);

          const getCardStyle = () => {
            const base = {
              padding: "10px",
              borderRadius: "4px",
              fontSize: "12px",
              transition: "all 0.2s ease",
              boxShadow: isHighlighted ? "0 0 10px rgba(0, 229, 255, 0.4)" : "none",
              display: "flex",
              flexDirection: "column" as const,
              boxSizing: "border-box" as const,
            };
            if (isSkipped)    return { ...base, borderLeft: "4px solid #555",    background: "#141418", opacity: 0.7 };
            if (isAccepted)   return { ...base, borderLeft: "4px solid #28a745", background: "rgba(40, 167, 69, 0.08)", opacity: 0.75 };
            if (action === "FIX") return { ...base, borderLeft: "4px solid #007bff", background: "rgba(0, 123, 255, 0.1)" };
            if (issue.id.startsWith("attr_reefer_")) return { ...base, borderLeft: "4px solid #00b4d8", background: "#0d1a1f" };
            if (issue.id.startsWith("attr_imo_"))   return { ...base, borderLeft: "4px solid #ff6b35", background: "#1f0f0a" };
            if (issue.id.startsWith("attr_oog_"))   return { ...base, borderLeft: "4px solid #9b5de5", background: "#130d1f" };
            if (issue.id.startsWith("shift_"))      return { ...base, borderLeft: "4px solid #ffa500", background: "#1f1a0a" };
            if (issue.severity === "CRITICAL") return { ...base, borderLeft: "4px solid #ff4d4d", background: "#1a1a20" };
            return { ...base, borderLeft: "4px solid #ffea00", background: "#1a1a20" };
          };

          return (
            <div
              key={issue.id}
              onMouseEnter={() =>
                onHighlightChange({
                  kind: "active",
                  hoverContainerId: issue.containerId,
                  selectedIssueId: issue.id,
                  relatedContainerIds: issue.relatedContainerIds,
                } as HighlightId)
              }
              onMouseLeave={() => onHighlightChange({ kind: "none" })}
              style={getCardStyle()}
            >
              {/* タイトル行 */}
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                marginBottom: "8px",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", flex: 1, marginRight: "8px", flexWrap: "wrap" }}>
                  {badge && (
                    <span style={{
                      fontSize: "9px",
                      padding: "2px 5px",
                      borderRadius: "3px",
                      background: `${badge.color}22`,
                      border: `1px solid ${badge.color}`,
                      color: badge.color,
                      fontWeight: 900,
                      flexShrink: 0,
                      letterSpacing: "0.5px",
                    }}>
                      {badge.label}
                    </span>
                  )}
                  {solverBadge && (
                    <span title={t('issueListView.solverBadgeTooltip')} style={{
                      fontSize: "9px",
                      padding: "2px 5px",
                      borderRadius: "3px",
                      background: `${solverBadge.color}22`,
                      border: `1px solid ${solverBadge.color}`,
                      color: solverBadge.color,
                      fontWeight: 900,
                      flexShrink: 0,
                      letterSpacing: "0.5px",
                    }}>
                      {solverBadge.label}
                    </span>
                  )}
                  {showDeferredBadge && (
                    <span title={t('issueListView.deferredBadgeTooltip')} style={{
                      fontSize: "9px",
                      padding: "2px 5px",
                      borderRadius: "3px",
                      background: `${DEFERRED_BADGE_COLOR}22`,
                      border: `1px solid ${DEFERRED_BADGE_COLOR}`,
                      color: DEFERRED_BADGE_COLOR,
                      fontWeight: 900,
                      flexShrink: 0,
                      letterSpacing: "0.5px",
                    }}>
                      {t('issueListView.deferredBadgeLabel')}
                    </span>
                  )}
                  <span style={{
                    fontWeight: "bold",
                    color: isSkipped || isAccepted ? "#555"
                      : action !== "NONE" ? "#fff"
                      : (issue.severity === "CRITICAL" ? "#ff4d4d" : "#ffea00"),
                  }}>
                    {issue.title}
                  </span>
                </div>
                {isSkipped && (
                  <span style={{
                    fontSize: "9px", padding: "2px 6px", borderRadius: "10px",
                    background: "#333", color: "#888", fontWeight: "bold", flexShrink: 0,
                  }}>
                    SKIPPED
                  </span>
                )}
                {!isSkipped && action !== "NONE" && (
                  <span style={{
                    fontSize: "9px", padding: "2px 6px", borderRadius: "10px",
                    background: action === "FIX" ? "#007bff" : "#28a745",
                    color: "#fff", fontWeight: "bold", flexShrink: 0,
                  }}>
                    {action === "FIX" ? "FIXED" : "ACCEPTED"}
                  </span>
                )}
              </div>

              <div style={{
                color: isShip ? "#ffb86c" : "#aaa",
                fontSize: "11px",
                marginBottom: "6px",
                lineHeight: 1.5,
              }}>
                {issueMessage(issue, t)}
              </div>

              {issue.containerId && (
                <div style={{
                  color: "#aaa",
                  fontSize: "11px",
                  marginBottom: "12px",
                  flexGrow: 1,
                }}>
                  Container: <span style={{ color: "#eee" }}>{issue.containerId}</span>
                  {(issue.relatedContainerIds?.length ?? 0) > 0 && (
                    <div style={{ marginTop: "2px" }}>
                      Related: <span style={{ color: "#eee" }}>{issue.relatedContainerIds.join(", ")}</span>
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: "flex", gap: "8px", marginTop: "auto" }}>
                {fixable && (
                  <button
                    onClick={() => onApplyFix(issue, "FIX")}
                    style={{
                      flex: 1, padding: "8px 4px", fontSize: "10px", cursor: "pointer",
                      background: action === "FIX" ? "#007bff" : "transparent",
                      border: "1px solid #007bff",
                      color: action === "FIX" ? "#fff" : "#007bff",
                      borderRadius: "3px", fontWeight: "bold", transition: "all 0.1s",
                    }}
                  >
                    FIX (SWAP)
                  </button>
                )}
                <button
                  onClick={() => onApplyFix(issue, "ACCEPT")}
                  style={{
                    flex: fixable ? 1 : undefined,
                    width: fixable ? undefined : "100%",
                    padding: "8px 4px", fontSize: "10px", cursor: "pointer",
                    background: isAccepted ? "#28a745" : "transparent",
                    border: "1px solid #28a745",
                    color: isAccepted ? "#fff" : "#28a745",
                    borderRadius: "3px", fontWeight: "bold", transition: "all 0.1s",
                  }}
                >
                  {acceptLabel(issue, t)}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}