// import React from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { GanttTask, HighlightId } from "../domain/types";

interface TaskGanttProps {
  tasks: GanttTask[];
  currentTime: number;
  makespan: number;
  highlightId: HighlightId;
  onHighlightChange: (h: HighlightId) => void;
  onTimeChange: (time: number) => void;
  shifts?: Array<{ id: string; start: number; end: number; cranes: string[] }>;
  breaks?: Array<{ shift: string; start: number; duration: number }>;
  operationStartSec?: number; // 位置オフセット（シフトありのみ非0）
  labelOffsetMin?: number;    // 目盛りラベルのベース（operation_startの分換算値）
  problemClass?: string;      // 後方互換のため残すが、凡例切替は2026-07-14よりtasksの形状から自動判定する
}

const OPERATION_COLORS: Record<string, { bg: string; border: string; label: string }> = {
  LOAD:      { bg: "rgba(80, 250, 123, 0.4)",  border: "#50fa7b", label: "LOAD" },
  DISCHARGE: { bg: "rgba(255, 121, 198, 0.4)", border: "#ff79c6", label: "DISCHARGE" },
  PLACE:     { bg: "rgba(139, 233, 253, 0.4)", border: "#8be9fd", label: "PLACE" },
  REHANDLE:  { bg: "rgba(255, 184, 108, 0.4)", border: "#ffb86c", label: "REHANDLE" },
  PICK:      { bg: "rgba(103, 218, 255, 0.4)", border: "#67daff", label: "PICKUP" },
  MOVE:      { bg: "rgba(189, 147, 249, 0.4)", border: "#bd93f9", label: "MOVE" },
  DEFAULT:   { bg: "rgba(98, 114, 164, 0.3)",  border: "#6272a4", label: "OTHER" },
  ASSIGN:    { bg: "rgba(139, 233, 253, 0.4)", border: "#8be9fd", label: "ASSIGN" },
};

// 汎用ドメイン（NurseShiftWeeklyCap等）用タスク色（colorKeyで色分け）。
// 2026-07-14: 以前は alpha=0.4 だったため、ホバー時の不透明な色（isHovered時の
// #ffb86c等）と比べて非ホバー時の見た目が非常に薄く、看護師シフトのように
// バーがまばら（YARDほど密集しない）なドメインだと暗い背景に溶け込んで
// 「マウスを乗せた瞬間だけ見える」ように見えるほど視認性が低かった。
// alpha を上げてホバーなしでも通常表示ではっきり見えるようにする。
const STAFFING_COLORS = [
  { bg: "rgba(80, 250, 123, 0.75)",  border: "#50fa7b" },
  { bg: "rgba(255, 121, 198, 0.75)", border: "#ff79c6" },
  { bg: "rgba(255, 184, 108, 0.75)", border: "#ffb86c" },
  { bg: "rgba(189, 147, 249, 0.75)", border: "#bd93f9" },
  { bg: "rgba(103, 218, 255, 0.75)", border: "#67daff" },
  { bg: "rgba(241, 250, 140, 0.75)", border: "#f1fa8c" },
];

// リスクレベル色（2026-07-14追加）: task.riskLevel が指定されているドメイン
// （例: TruckDispatcherの時間指定余裕度）専用の意味付き3色。colorKeyの
// 自動色分け（カテゴリが多いと6色を使い回すだけで意味を持たない）より優先する。
const RISK_COLORS: Record<"ok" | "warning" | "critical", { bg: string; border: string }> = {
  ok:       { bg: "rgba(80, 250, 123, 0.75)", border: "#50fa7b" },
  warning:  { bg: "rgba(255, 184, 108, 0.75)", border: "#ffb86c" },
  critical: { bg: "rgba(255, 85, 85, 0.75)",   border: "#ff5555" },
};
const RISK_LABELS: Record<"ok" | "warning" | "critical", string> = {
  ok: "余裕あり",
  warning: "タイト",
  critical: "時間指定違反",
};
const RISK_ORDER: Array<"ok" | "warning" | "critical"> = ["ok", "warning", "critical"];

const SEC_PER_UNIT = 60;
const toMin = (sec: number) => sec / SEC_PER_UNIT;

// 目盛りラベルの間隔候補（分）。ズーム倍率に応じて、ラベル同士が最低
// MIN_PX_PER_TICKpx空くように、この中から「きりのいい」最小の値を選ぶ。
const MIN_PX_PER_TICK = 60;
const NICE_TICK_STEPS_MIN = [1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440, 2880, 4320, 10080];
function pickTickIntervalMin(scalePxPerMin: number): number {
  if (!(scalePxPerMin > 0)) return 60;
  const desiredMin = MIN_PX_PER_TICK / scalePxPerMin;
  for (const step of NICE_TICK_STEPS_MIN) {
    if (step >= desiredMin) return step;
  }
  return NICE_TICK_STEPS_MIN[NICE_TICK_STEPS_MIN.length - 1];
}

const ZOOM_BTN_STYLE: React.CSSProperties = {
  width: "20px", height: "20px", lineHeight: "18px",
  fontSize: "12px", fontWeight: "bold", color: "#8be9fd",
  background: "#1a1b26", border: "1px solid #2d2d3d", borderRadius: "4px",
  cursor: "pointer", padding: 0,
};

export function TaskGantt({
  tasks,
  currentTime,
  makespan,
  highlightId,
  onHighlightChange,
  onTimeChange,
  shifts = [],
  breaks = [],
  operationStartSec = 0,
  labelOffsetMin = 0,
  // problemClass は props としては後方互換のため残すが、凡例切替はtasksの形状
  // から自動判定するため、ここでは分割代入せず未使用にしておく
}: TaskGanttProps) {
  const { t } = useTranslation();
  // ドメイン非依存化（2026-07-14）: YARD操作種別（LOAD/DISCHARGE等）の凡例は
  // task.operationが実際に指定されているタスクだけで意味を持つ。operationを
  // 一切持たないタスク（NurseShiftWeeklyCap等の「スタッフィング系」ドメイン）では、
  // 汎用のcolorKeyベースの色分けに自動的に切り替える。problemClassでドメイン名を
  // 都度追記する方式は廃止し、データの形から判定することで、新しいドメインを
  // 追加してもこのファイルを変更しなくて済むようにする
  // （problemClassプロパティ自体は後方互換のため残す）。
  const isGeneric = tasks.length > 0 && !tasks.some((task) => Boolean(task.operation));
  // riskLevelを持つタスクが1件でもあれば、そのドメインは「意味付き3色」表示に
  // 統一する（colorKeyの自動色分けより優先。凡例もriskLevel専用に切り替える）。
  const hasRiskLevels = tasks.some((task) => Boolean(task.riskLevel));

  // 色分け・表示用の共通アクセサ。GanttTaskは全フィールドoptionalなので、
  // Yard/Vessel系の既存フィールド（containerId/resource等）にもフォールバックする。
  const getColorKey = (task: GanttTask) => task.colorKey ?? task.task_id ?? task.containerId ?? task.id;
  const getDisplayLabel = (task: GanttTask) => task.label ?? task.containerId ?? task.id;
  const getResourceKey = (task: GanttTask) => task.resourceId ?? task.resource ?? "Unknown";
  const getResourceLabel = (task: GanttTask) =>
    task.resourceLabel ?? task.resource ?? task.resourceId ?? "Unknown";

  // 色分け用: colorKeyからインデックスを決定（汎用ドメイン用、riskLevel未使用時のみ）
  const taskColorMap: Record<string, number> = {};
  let colorIdx = 0;
  tasks.forEach((task) => {
    const key = getColorKey(task);
    if (key && !(key in taskColorMap)) taskColorMap[key] = colorIdx++ % STAFFING_COLORS.length;
  });
  const uniqueTasks = tasks;
  const resources = Array.from(new Set(uniqueTasks.map(getResourceKey))).sort();
  // レーンヘッダーの表示名（レーンのグルーピングキーごとに代表の表示名を1つ持つ）
  const resourceLabelByKey: Record<string, string> = {};
  tasks.forEach((task) => {
    const key = getResourceKey(task);
    if (!(key in resourceLabelByKey)) resourceLabelByKey[key] = getResourceLabel(task);
  });

  const HEADER_WIDTH     = 100;
  const DEFAULT_SCALE    = 20;   // フォールバック（コンテナ幅未計測時のみ使用）
  const LANE_HEIGHT      = 65;
  const PADDING_INTERVAL = 5;    // dynamicMaxTimeの丸め単位（表示密度とは無関係）
  const ZOOM_STEP        = 1.4;
  const MIN_ZOOM         = 0.05;
  const MAX_ZOOM         = 20;

  // 位置オフセット（分）: シフトありのみ非0
  const operationStartMin = toMin(operationStartSec);

  // チャート幅: makespanからオフセットを引いた相対時間で計算
  const makespanMin = toMin(makespan || 0);
  const relativeMakespanMin = Math.max(30, makespanMin - operationStartMin);
  const dynamicMaxTime = Math.ceil(relativeMakespanMin / PADDING_INTERVAL) * PADDING_INTERVAL + PADDING_INTERVAL;

  // ズーム（2026-07-14追加）: 表全体の横幅がスクロール領域の実測幅にちょうど
  // 収まる「フィット倍率」を基準(zoomMultiplier=1)とし、＋/－ボタンで
  // その倍率を掛け引きして横方向を圧縮・拡大する。これにより長い horizon
  // （複数週の看護師シフト等）でも初期状態で全体が見え、必要なときだけ
  // ズームインして詳細を見られるようにする。
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const [scrollAreaWidth, setScrollAreaWidth] = useState(0);
  const [zoomMultiplier, setZoomMultiplier] = useState(1);

  useEffect(() => {
    const el = scrollAreaRef.current;
    if (!el) return;
    const update = () => setScrollAreaWidth(el.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const availableChartWidth = Math.max(0, scrollAreaWidth - HEADER_WIDTH);
  const fitScale = availableChartWidth > 0 && dynamicMaxTime > 0
    ? availableChartWidth / dynamicMaxTime
    : DEFAULT_SCALE;
  const TIME_SCALE = fitScale * zoomMultiplier;
  const CHART_INNER_WIDTH = dynamicMaxTime * TIME_SCALE;

  // 目盛りラベルの間隔（2026-07-14追加）: 以前はTICK_INTERVAL=5分固定だったため、
  // horizonが長いドメイン（TruckDispatcherの949分、NurseShiftWeeklyCapの
  // 10500分等）をFIT表示すると、5分刻みのラベルが数百個詰め込まれて文字が
  // 重なり合い判読不能になっていた（「チャートが変」に見えた原因）。
  // ズーム倍率（TIME_SCALE）に応じて、ラベル同士が最低MIN_PX_PER_TICKpx
  // 空くように「きりのいい」間隔を動的に選ぶ。
  const TICK_INTERVAL = pickTickIntervalMin(TIME_SCALE);

  const zoomIn  = () => setZoomMultiplier((z) => Math.min(MAX_ZOOM, z * ZOOM_STEP));
  const zoomOut = () => setZoomMultiplier((z) => Math.max(MIN_ZOOM, z / ZOOM_STEP));
  const zoomFit = () => setZoomMultiplier(1);

  const currentTimeMin = toMin(currentTime);

  const shiftColors: Record<string, string> = {
    day:   "rgba(135, 206, 250, 0.08)",
    night: "rgba(25, 25, 112, 0.15)",
  };

  const craneShifts: Record<string, Array<{ start: number; end: number; shiftId: string }>> = {};
  const craneBreaks: Record<string, Array<{ start: number; end: number }>> = {};

  shifts.forEach((shift) => {
    (shift.cranes ?? []).forEach((crane) => {
      if (!craneShifts[crane]) craneShifts[crane] = [];
      craneShifts[crane].push({ start: shift.start, end: shift.end, shiftId: shift.id });
    });
  });

  breaks.forEach((brk) => {
    const shift = shifts.find((s) => s.id === brk.shift);
    if (shift) {
      (shift.cranes ?? []).forEach((crane) => {
        if (!craneBreaks[crane]) craneBreaks[crane] = [];
        craneBreaks[crane].push({ start: brk.start, end: brk.start + brk.duration });
      });
    }
  });

  return (
    <div style={{
      padding: "16px", background: "#0d0f12", borderRadius: "12px", border: "1px solid #2d2d3d",
      display: "flex", flexDirection: "column", height: "100%", boxSizing: "border-box", color: "#e2e2e9"
    }}>
      <style>{`
        .custom-scroll::-webkit-scrollbar { height: 12px; display: block; }
        .custom-scroll::-webkit-scrollbar-track { background: #1a1b26; border-radius: 6px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #565f89; border: 2px solid #1a1b26; border-radius: 6px; }
        .custom-scroll::-webkit-scrollbar-thumb:hover { background: #7aa2f7; }
      `}</style>

      {/* ヘッダー */}
      <div style={{ flexShrink: 0, marginBottom: "12px" }}>
        {/* 1行目: タイトル・ズームコントロール・ELAPSED/MAKESPAN。
            凡例（2行目）がどれだけ長くなっても、ここは常に1行に収めて
            折り返しさせない（ズームボタンが凡例に押し出されて見えなくなる
            不具合が過去にあったため）。 */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "nowrap" }}>
          <div style={{ fontSize: "14px", fontWeight: "bold", color: "#82a1ff", letterSpacing: "1px", flexShrink: 0 }}>
            OPERATIONAL GANTT
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "16px", flexShrink: 0 }}>
            {/* ズームコントロール（横方向の圧縮・拡大） */}
            <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              <button
                onClick={zoomOut}
                title={t('taskGantt.zoomOut')}
                style={ZOOM_BTN_STYLE}
              >
                −
              </button>
              <button
                onClick={zoomFit}
                title={t('taskGantt.zoomFit')}
                style={{ ...ZOOM_BTN_STYLE, width: "auto", padding: "0 8px", fontSize: "9px" }}
              >
                FIT
              </button>
              <button
                onClick={zoomIn}
                title={t('taskGantt.zoomIn')}
                style={ZOOM_BTN_STYLE}
              >
                ＋
              </button>
            </div>
            <div style={{
              display: "flex", alignItems: "baseline", gap: "4px",
              color: "#00e5ff", fontWeight: "bold", fontFamily: "monospace", fontSize: "16px"
            }}>
              <span style={{ fontSize: "10px", color: "#6272a4" }}>ELAPSED</span>
              {Math.ceil(currentTimeMin)}m
              <span style={{ color: "#44475a", margin: "0 4px" }}>/</span>
              <span style={{ fontSize: "10px", color: "#6272a4" }}>MAKESPAN</span>
              {Math.ceil(makespanMin)}m
            </div>
          </div>
        </div>

        {/* 2行目: 凡例。汎用ドメインはcolorKeyの数だけ増えうる（例: 日毎に
            task_idが変わるINRC由来のデータでは数十件になることがある）ため、
            折り返し＋最大高さ＋内部スクロールにして、1行目を押し出さないようにする。 */}
        <div style={{
          display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap",
          marginTop: "8px", maxHeight: "54px", overflowY: "auto",
        }}>
          {hasRiskLevels ? (
            // リスクレベル凡例（例: TruckDispatcherの時間指定余裕度）
            RISK_ORDER.filter((level) => tasks.some((task) => task.riskLevel === level)).map((level) => {
              const c = RISK_COLORS[level];
              return (
                <div key={level} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <div style={{ width: "10px", height: "10px", background: c.bg, border: `1px solid ${c.border}`, borderRadius: "2px" }} />
                  <span style={{ fontSize: "10px", color: "#6272a4", fontWeight: "bold" }}>{RISK_LABELS[level]}</span>
                </div>
              );
            })
          ) : isGeneric ? (
            // 汎用ドメイン（operationなし）: タスク名/colorKeyで色分け凡例
            Object.entries(taskColorMap).map(([tid, idx]) => {
              const c = STAFFING_COLORS[idx];
              return (
                <div key={tid} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <div style={{ width: "10px", height: "10px", background: c.bg, border: `1px solid ${c.border}`, borderRadius: "2px" }} />
                  <span style={{ fontSize: "10px", color: "#6272a4", fontWeight: "bold" }}>{tid}</span>
                </div>
              );
            })
          ) : (
            // YardPlanning: オペレーション種別凡例
            Object.entries(OPERATION_COLORS).filter(([k]) => k !== 'ASSIGN').map(([key, cfg]) => (
              <div key={key} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                <div style={{
                  width: "10px", height: "10px",
                  background: cfg.bg, border: `1px solid ${cfg.border}`, borderRadius: "2px"
                }} />
                <span style={{ fontSize: "10px", color: "#6272a4", fontWeight: "bold", textTransform: "uppercase" }}>
                  {cfg.label}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* スクロールエリア */}
      <div ref={scrollAreaRef} className="custom-scroll" style={{
        flex: 1, overflow: "scroll", border: "1px solid #282a36",
        background: "#16161e", position: "relative"
      }}>
        <div style={{ width: `${HEADER_WIDTH + CHART_INNER_WIDTH}px`, position: "relative" }}>

          {/* 目盛りのグリッド線・現在時刻線（2026-07-14変更）。
              以前は position:sticky の目盛り行の「子要素」に height:370px/400px の
              固定値を持たせてレーン部分までオーバーフローさせる実装だったが、
              Safari(WebKit)でこの「stickyな親＋大きくはみ出す子」という構成だと
              スクロール領域内の再描画が壊れ、タスクバーがマウスホバーで強制再描画
              されるまで表示されない不具合が発生した。目盛り行から完全に独立した、
              普通の position:absolute オーバーレイに変更し、height:100% で
              親（このコンテナ）の実高さに自動追従させる（レーン数が変わっても
              固定値のズレが出ない）。 */}
          <div style={{
            position: "absolute", top: 0, left: HEADER_WIDTH, right: 0, height: "100%",
            pointerEvents: "none", zIndex: 5,
          }}>
            {Array.from({ length: Math.ceil(dynamicMaxTime / TICK_INTERVAL) + 1 }).map((_, i) => (
              <div key={i} style={{
                position: "absolute", left: `${i * TICK_INTERVAL * TIME_SCALE}px`,
                top: 0, height: "100%", borderLeft: "1px solid #222",
              }} />
            ))}
            {/* 現時刻インジケーター */}
            <div style={{
              position: "absolute",
              left: `${currentTimeMin * TIME_SCALE}px`,
              top: 0, height: "100%", width: "2px",
              backgroundColor: "#ff4444",
              boxShadow: "0 0 8px rgba(255, 68, 68, 0.6)",
              transition: "left 0.1s linear"
            }} />
          </div>

          {/* 目盛り (Sticky Top): ラベル表示専用。グリッド線・現在時刻線は上の
              独立オーバーレイ側が担当するので、ここは高さ30pxのみで完結する。 */}
          <div style={{
            display: "flex", height: "30px", borderBottom: "1px solid #282a36",
            background: "#1a1b26", position: "sticky", top: 0, zIndex: 100
          }}>
            <div style={{
              width: HEADER_WIDTH, flexShrink: 0, background: "#1a1b26",
              position: "sticky", left: 0, zIndex: 110, borderRight: "1px solid #282a36"
            }} />
            <div style={{ position: "relative", flexGrow: 1 }}>
              {Array.from({ length: Math.ceil(dynamicMaxTime / TICK_INTERVAL) + 1 }).map((_, i) => (
                <div key={i} style={{
                  position: "absolute", left: `${i * TICK_INTERVAL * TIME_SCALE}px`,
                  fontSize: "9px", color: "#6272a4",
                  paddingLeft: "4px", pointerEvents: "none"
                }}>
                  {/* ラベルは operation_start ベースの実分数で表示 */}
                  {i * TICK_INTERVAL + Math.round(labelOffsetMin)}m
                </div>
              ))}
            </div>
          </div>

          {/* レーン */}
          {resources.map((res) => (
            <div key={res} style={{
              display: "flex", height: `${LANE_HEIGHT}px`,
              borderBottom: "1px solid #232433", position: "relative"
            }}>
              <div style={{
                width: HEADER_WIDTH, flexShrink: 0, background: "#1c1c24",
                borderRight: "1px solid #282a36", display: "flex", alignItems: "center",
                justifyContent: "center", position: "sticky", left: 0, zIndex: 50
              }}>
                <span style={{ fontSize: "11px", fontWeight: "bold", color: isGeneric ? "#8be9fd" : (res.includes("GC") ? "#50fa7b" : "#bd93f9") }}>
                  {resourceLabelByKey[res] ?? res}
                </span>
              </div>

              <div style={{ position: "relative", flexGrow: 1, height: "100%" }}>
                {/* シフト背景（オフセット適用） */}
                {craneShifts[res]?.map((shift, idx) => (
                  <div key={`shift-${idx}`} style={{
                    position: "absolute",
                    left: `${(shift.start - operationStartMin) * TIME_SCALE}px`,
                    width: `${(shift.end - shift.start) * TIME_SCALE}px`,
                    height: "100%",
                    background: shiftColors[shift.shiftId] || "rgba(100,100,100,0.05)",
                    pointerEvents: "none", zIndex: 1,
                  }} />
                ))}
                {/* 休憩帯（オフセット適用） */}
                {craneBreaks[res]?.map((brk, idx) => (
                  <div key={`break-${idx}`} style={{
                    position: "absolute",
                    left: `${(brk.start - operationStartMin) * TIME_SCALE}px`,
                    width: `${(brk.end - brk.start) * TIME_SCALE}px`,
                    height: "100%",
                    background: "rgba(128,128,128,0.3)",
                    pointerEvents: "none", zIndex: 2,
                    borderLeft: "2px dashed #888", borderRight: "2px dashed #888",
                  }} />
                ))}

                {/* タスクバー */}
                {uniqueTasks
                  .filter(task => getResourceKey(task) === res)
                  .map((task) => {
                    const taskKey = task.id;
                    // 後方互換: Yard系はcontainerIdでハイライトの相互参照を行っていたため、
                    // containerIdが無ければidをフォールバックとして使う
                    const highlightKey = task.containerId ?? task.id;
                    const isHovered = highlightId.kind === "active" &&
                      (highlightId.selectedIssueId === taskKey ||
                       highlightId.relatedContainerIds.includes(taskKey) ||
                       highlightId.relatedContainerIds.includes(highlightKey));
                    const colorKey = getColorKey(task);
                    const cfg = task.riskLevel
                      ? RISK_COLORS[task.riskLevel]
                      : isGeneric
                        ? STAFFING_COLORS[taskColorMap[colorKey] ?? 0]
                        : (OPERATION_COLORS[task.operation ?? "DEFAULT"] || OPERATION_COLORS.DEFAULT);

                    // start/end は CP Optimizer の 0 起点相対秒
                    // operationStartMin を引いて位置オフセット（シフトなしは0なので影響なし）
                    const startMin    = toMin(task.start) - operationStartMin;
                    const durationMin = toMin(task.end - task.start);

                    // ラベル表示用の実時刻（operation_start からの絶対分）
                    const absStartMin = startMin + labelOffsetMin;

                    return (
                      <div
                        key={taskKey}
                        style={{
                          position: "absolute",
                          top: "12px",
                          left:  `${startMin    * TIME_SCALE}px`,
                          width: `${Math.max(4, durationMin * TIME_SCALE - 1)}px`,
                          height: "35px",
                          background: isHovered ? "#ffb86c" : cfg.bg,
                          border: `2px solid ${isHovered ? "#fff" : cfg.border}`,
                          boxSizing: "border-box", borderRadius: "4px",
                          zIndex: isHovered ? 150 : 10,
                          display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: "10px", cursor: "pointer", fontWeight: "bold", color: "#fff",
                          overflow: "hidden", transition: "all 0.2s ease",
                        }}
                        title={isGeneric
                          ? `${getDisplayLabel(task)} | ${t('taskGantt.staffPrefix')}${getResourceLabel(task)} | ${toMin(task.start)}m → ${toMin(task.end)}m (${Math.round(durationMin)}m)${task.riskLevel ? ` | ${RISK_LABELS[task.riskLevel]}` : ''}`
                          : `${task.containerId} | ${task.operation} | ${Math.round(absStartMin)}m → ${Math.round(absStartMin + durationMin)}m`
                        }
                        onMouseEnter={() => onHighlightChange({
                          kind: "active",
                          hoverContainerId: highlightKey,
                          selectedIssueId: taskKey,
                          relatedContainerIds: [highlightKey]
                        })}
                        onMouseLeave={() => onHighlightChange({ kind: "none" })}
                      >
                        {getDisplayLabel(task)}
                      </div>
                    );
                  })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* シークバー */}
      <div style={{ marginTop: "16px", flexShrink: 0 }}>
        <input
          type="range"
          min="0"
          max={dynamicMaxTime}
          step="0.5"
          value={currentTimeMin}
          onChange={(e) => onTimeChange(parseFloat(e.target.value) * SEC_PER_UNIT)}
          style={{ width: "100%", accentColor: "#00e5ff", cursor: "pointer" }}
        />
      </div>
    </div>
  );
}

