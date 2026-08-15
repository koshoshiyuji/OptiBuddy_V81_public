import type { Location, SnapshotContainer, Task, DslContainer, YardSolverConfig } from "./types";

type ContainerSnapshot = Omit<SnapshotContainer, "containerId" | "weight" | "pod" | "pol">;

const NONE_LOC: Location = { domain: "NONE", bay: 0, row: 0, tier: 0 };

function yardSlotKey(pos: { bay: number; row: number; tier: number }) {
  return `${pos.bay}:${pos.row}:${pos.tier}`;
}

function locationToSnapshot(loc: Location): ContainerSnapshot {
  if (loc.domain === "NONE") return inTransitSnapshot();

  const domain = loc.domain === "YARD" ? "YARD" : "VESSEL";
  const pos = {
    bay: Number(loc.bay),
    row: Number(loc.row),
    tier: Number(loc.tier),
  };
  return {
    currentPos: domain,
    yard: domain === "YARD" ? pos : { bay: 0, row: 0, tier: 0 },
    ship: domain === "VESSEL" ? pos : { bay: 0, row: 0, tier: 0 },
    visibleInYard: domain === "YARD",
    visibleInVessel: domain === "VESSEL",
  };
}

/** クレーン搬送中 — ヤード／船のいずれのスロットも占有しない */
function inTransitSnapshot(): ContainerSnapshot {
  return {
    currentPos: "YARD",
    yard: { bay: 0, row: 0, tier: 0 },
    ship: { bay: 0, row: 0, tier: 0 },
    visibleInYard: false,
    visibleInVessel: false,
  };
}

function lastCompletedEnd(relevant: Task[], currentTime: number): number {
  const done = relevant.filter((t) => currentTime >= t.end);
  return done.length > 0 ? done[done.length - 1].end : -1;
}

/**
 * ソルバーが返す Task には from/to が含まれていないため、
 * DslContainer の yard/ship 座標から operation 別に復元する。
 *
 * operation  | from          | to
 * -----------|---------------|---------------
 * PICK       | YARD(yard)    | NONE
 * MOVE       | YARD(yard)    | NONE
 * REHANDLE   | YARD(yard)    | NONE
 * LOAD       | YARD(yard)    | VESSEL(ship)
 * DISCHARGE  | VESSEL(ship)  | NONE
 * PLACE      | NONE          | YARD(yard)
 */
function enrichTaskLocations(
  tasks: Task[],
  dslMap: Map<string, DslContainer>
): Task[] {
  return tasks.map((t) => {
    // すでに補完済みならスキップ
    if (t.from?.domain) return t;

    const dsl = dslMap.get(String(t.containerId));
    if (!dsl) return t;

    const yardLoc: Location = { domain: "YARD", bay: dsl.yard.bay, row: dsl.yard.row, tier: dsl.yard.tier };
    const shipLoc: Location = { domain: "VESSEL", bay: dsl.ship.bay, row: dsl.ship.row, tier: dsl.ship.tier };

    let from: Location;
    let to: Location;

    switch (t.operation) {
      case "PICK":
      case "MOVE":
      case "REHANDLE":
        from = yardLoc; to = NONE_LOC; break;
      case "LOAD":
        from = yardLoc; to = shipLoc; break;
      case "DISCHARGE":
        from = shipLoc; to = NONE_LOC; break;
      case "PLACE":
        from = NONE_LOC; to = yardLoc; break;
      default:
        from = NONE_LOC; to = NONE_LOC;
    }

    return { ...t, from, to };
  });
}

function snapshotForContainer(relevant: Task[], currentTime: number, safetyGap: number = 0): ContainerSnapshot {
  // activeは「開始済みかつ未完了」= start < currentTime < end
  // end境界はすでに完了扱い（>= t.end）
  const active = relevant.find((t) => currentTime > t.start && currentTime < t.end);
  const lastDone = [...relevant].reverse().find((t) => currentTime >= t.end);

  if (active) {
    const { operation, from, to, start } = active;

    // 船からの揚げ作業中は船側スロットに残す
    if (operation === "DISCHARGE" && from.domain === "VESSEL") {
      return locationToSnapshot(from);
    }

    // LOAD: 安全ギャップまでは元のヤードに表示、以降は船側に表示
    if (operation === "LOAD") {
      if (currentTime < start + safetyGap) {
        return locationToSnapshot(from);
      }
      return locationToSnapshot(to);
    }

    // PLACE: 安全ギャップまでは搬送中、以降は目的ヤードに表示
    if (operation === "PLACE") {
      if (currentTime < start + safetyGap) {
        return inTransitSnapshot();
      }
      return locationToSnapshot(to);
    }

    // PICK: 安全ギャップまでは元のヤードに表示、以降は搬送中
    if (operation === "PICK") {
      if (currentTime < start + safetyGap) {
        return locationToSnapshot(from);
      }
      return inTransitSnapshot();
    }

    // MOVE / REHANDLE: 安全ギャップまでは元のヤードに表示、以降は to に表示
    if (operation === "MOVE" || operation === "REHANDLE") {
      if (currentTime < start + safetyGap) {
        return locationToSnapshot(from);
      }
      return to.domain === "YARD" ? locationToSnapshot(to) : inTransitSnapshot();
    }

    // それ以外は通常表示
    return locationToSnapshot(from);
  }

  // activeがない場合（タスク開始前 or タスク完了後）
  if (lastDone) {
    const loc = lastDone.to;
    if (!loc || loc.domain === "NONE") return inTransitSnapshot();
    return locationToSnapshot(loc);
  }

  // 全タスク未開始: 最初のタスクの from が初期位置
  const firstFrom = relevant[0].from;
  if (!firstFrom || firstFrom.domain === "NONE") return inTransitSnapshot();
  return locationToSnapshot(firstFrom);
}

/**
 * 現在時刻でのコンテナ位置スナップショット。
 * ヤードは同一 (bay,row,tier) に複数載せない（後着優先）。
 *
 * @param tasks         ソルバーが返したタスク一覧
 * @param currentTime   現在時刻（秒）
 * @param dslContainers DSLのコンテナ定義（from/to 復元に使用、省略可）
 * @param config        ヤードソルバー設定（safety_gap取得用、省略可）
 */
export function buildLiveViewData(
  tasks: Task[],
  currentTime: number,
  dslContainers: DslContainer[] = [],
  config?: YardSolverConfig
): Record<string, SnapshotContainer> {
  // safety_gap を設定から取得（デフォルト 0）
  const safetyGap = config?.safety_gap ?? 0;

  // DSLコンテナ情報をMapに変換し、from/to を補完
  const dslMap = new Map<string, DslContainer>(
    dslContainers.map((c) => [String(c.id ?? c.containerId), c])
  );
  const enriched = enrichTaskLocations(tasks, dslMap);

  const containerIds = Array.from(new Set(enriched.map((t) => t.containerId)));
  const containers: Record<string, SnapshotContainer> = {};
  const yardWinner = new Map<string, { cid: string; settledEnd: number }>();

  for (const cid of containerIds) {
    const relevant = enriched
      .filter((t) => t.containerId === cid)
      .sort((a, b) => a.start - b.start);
    if (relevant.length === 0) continue;

    const base = snapshotForContainer(relevant, currentTime, safetyGap);
    const first = relevant[0];

    containers[cid] = {
      containerId: cid,
      ...base,
      weight: first.weight || 0,
      pod: first.ports?.pod || "---",
      pol: first.ports?.pol || "---",
    };

    if (base.visibleInYard) {
      const key = yardSlotKey(base.yard);
      const end = lastCompletedEnd(relevant, currentTime);
      const prev = yardWinner.get(key);
      if (!prev || end > prev.settledEnd || (end === prev.settledEnd && cid < prev.cid)) {
        yardWinner.set(key, { cid, settledEnd: end });
      }
    }
  }

  const winnerIds = new Set(Array.from(yardWinner.values()).map((w) => w.cid));
  for (const cid of Object.keys(containers)) {
    if (containers[cid].visibleInYard && !winnerIds.has(cid)) {
      containers[cid] = { ...containers[cid], visibleInYard: false };
    }
  }

  return containers;
}