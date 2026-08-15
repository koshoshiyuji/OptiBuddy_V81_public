import type { Issue, IssueAction, Task } from '../../domain/types.ts';

export function calcKpi(
  tasks: Task[],
  makespan: number,
  allIssues: Issue[],
  issueActions: Record<string, IssueAction>
) {
  const SEC = 60;
  const makespanMin = Math.round(makespan / SEC);

  const resources = Array.from(new Set(tasks.map((t) => t.resource || t.resourceId).filter(Boolean))) as string[];
  const craneUtil = resources.map((rid) => {
    const busy = tasks
      .filter((t) => (t.resource || t.resourceId) === rid)
      .reduce((s, t) => s + (t.end - t.start), 0);
    const util = makespan > 0 ? Math.round((busy / makespan) * 100) : 0;
    return { id: rid, util };
  });

  const rehandleCount = tasks.filter((t) => t.operation === 'REHANDLE').length;

  let totalGap = 0;
  let gapCount = 0;
  resources.forEach((rid) => {
    const rTasks = tasks.filter((t) => (t.resource || t.resourceId) === rid).sort((a, b) => a.start - b.start);
    for (let i = 1; i < rTasks.length; i++) {
      const gap = rTasks[i].start - rTasks[i - 1].end;
      if (gap > 0) {
        totalGap += gap;
        gapCount++;
      }
    }
  });
  const avgWaitMin = gapCount > 0 ? Math.round(totalGap / gapCount / SEC) : 0;

  const resolvedCount = Object.values(issueActions).filter(
    (a) => a === 'FIX' || a === 'ACCEPT'
  ).length;
  const skippedCount = allIssues.filter((i) => (i as { skipped?: boolean }).skipped).length;
  const remainCount = allIssues.length - resolvedCount;

  // ── EventStaffing専用KPI ──────────────────────────────
  // 総人件費（各タスクの cost フィールドを合算）
  const totalCost = tasks.reduce((sum, t) => sum + ((t as unknown as { cost?: number }).cost ?? 0), 0);

  // 希望未充足ペナルティ（preference_satisfied=falseのタスク数）
  const prefPenalty = tasks.filter(
    (t) => (t as unknown as { has_preference?: boolean }).has_preference &&
           !(t as unknown as { preference_satisfied?: boolean }).preference_satisfied
  ).length;

  // グレード別人数
  const gradeCounts: Record<string, number> = {};
  tasks.forEach((t) => {
    const grade = (t as unknown as { grade?: string }).grade;
    if (grade) gradeCounts[grade] = (gradeCounts[grade] ?? 0) + 1;
  });

  // 割り当て済みスタッフ数（重複除去）
  const assignedStaffIds = new Set(
    tasks.map((t) => (t as unknown as { staff_id?: string }).staff_id).filter(Boolean)
  );

  return {
    makespanMin, craneUtil, rehandleCount, avgWaitMin,
    resolvedCount, skippedCount, remainCount,
    // EventStaffing
    totalCost, prefPenalty, gradeCounts,
    assignedStaffCount: assignedStaffIds.size,
  };
}
