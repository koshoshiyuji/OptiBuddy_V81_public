// detectIssues.ts
//
// イシュー検知はバックエンド（full_yard_solver.py）に統一しました。
// このファイルは互換性のために残していますが、常に空配列を返します。
//
import type { Task, Issue } from "../domain/types";

export function detectIssues(_tasks: Task[]): Issue[] {
  return [];
}