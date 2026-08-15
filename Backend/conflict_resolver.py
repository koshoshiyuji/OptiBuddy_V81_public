"""
conflict_resolver.py

ユーザーのFIX/ACCEPT/保留を受け取り、矛盾を検知・整理した上で
ソルバーに渡すprecedences（順序制約）を返す。

依存: networkx
"""

import networkx as nx
from typing import Any, Dict, List, Tuple


class ConflictResolver:
    def __init__(self, issues: List[Dict[str, Any]], containers: List[Dict[str, Any]]):
        """
        Args:
            issues:     フロントエンドから届いたissueActionsを含むイシューリスト
                        [{"id": "is_yard_C01_C02", "action": "FIX", "pair": ["C01", "C02"]}, ...]
            containers: DSLのコンテナリスト（物理制約の参照用）
        """
        self.issues     = issues
        self.containers = {str(c.get("id")): c for c in containers}
        self.graph      = nx.DiGraph()  # 順序制約グラフ
        self.precedences:    List[Tuple[str, str]] = []  # 解決済み制約
        self.resolved_ids:   List[str] = []  # 解決に成功したイシューID
        self.skipped_ids:    List[str] = []  # 矛盾のためスキップしたイシューID
        self.skip_reasons:   Dict[str, str] = {}  # スキップ理由

    def resolve(self) -> Tuple[List[Tuple[str, str]], List[str], Dict[str, str]]:
        """
        FIXリクエストを処理してprecedencesを返す。

        Returns:
            precedences:  [(before, after), ...]  ソルバーに渡す順序制約
            skipped_ids:  スキップされたイシューIDリスト
            skip_reasons: {issue_id: reason} スキップ理由
        """
        # IS_SHIPはスコープ外（ACCEPTのみ、FIXは無視）
        fix_requests = [
            i for i in self.issues
            if i.get("action") == "FIX"
            and not i.get("id", "").startswith("is_ship_")
        ]

        # 物理制約を先にグラフに追加（これは覆せない）
        self._add_physical_constraints()

        for req in fix_requests:
            issue_id = req.get("id", "")
            pair     = req.get("pair", [])

            if len(pair) != 2:
                self.skipped_ids.append(issue_id)
                self.skip_reasons[issue_id] = "pairが不正です（2要素必要）"
                continue

            before, after = pair[0], pair[1]

            # 循環チェック
            if self._would_cause_cycle(before, after):
                self.skipped_ids.append(issue_id)
                self.skip_reasons[issue_id] = (
                    f"{before} → {after} の順序制約が既存の制約と循環します。"
                    f"物理制約または他のFIXと矛盾するため自動スキップしました。"
                )
                continue

            # 制約を追加
            self.graph.add_edge(before, after)
            self.precedences.append((before, after))
            self.resolved_ids.append(issue_id)

        return self.precedences, self.skipped_ids, self.skip_reasons

    def _add_physical_constraints(self):
        """
        コンテナのyard/ship物理配置から覆せない順序制約をグラフに追加する。
        上にあるコンテナは下にあるコンテナより先に動かさなければならない。
        """
        # ヤード物理制約: 同一スタックで上にいるコンテナが先
        yard_stacks: Dict[Tuple, List] = {}
        for cid, c in self.containers.items():
            y = c.get("yard", {})
            key = (y.get("bay"), y.get("row"))
            if None not in key:
                yard_stacks.setdefault(key, []).append((cid, y.get("tier", 0)))

        for key, stack in yard_stacks.items():
            sorted_stack = sorted(stack, key=lambda x: x[1], reverse=True)  # 上から
            for i in range(len(sorted_stack) - 1):
                upper_cid = sorted_stack[i][0]
                lower_cid = sorted_stack[i + 1][0]
                # 上が先に動く必要がある
                if not self.graph.has_edge(upper_cid, lower_cid):
                    self.graph.add_edge(upper_cid, lower_cid, physical=True)

    def _would_cause_cycle(self, before: str, after: str) -> bool:
        """
        before → after のエッジを追加したときに循環が生じるか検査する。
        """
        test_graph = self.graph.copy()
        test_graph.add_edge(before, after)
        return not nx.is_directed_acyclic_graph(test_graph)

    def get_yard_swaps(self) -> List[Tuple[str, str]]:
        """
        解決済みのIS_YARDのFIXからyard座標swapのペアリストを返す。
        """
        swaps = []
        for req in self.issues:
            if (req.get("action") == "FIX"
                    and req.get("id", "").startswith("is_yard_")
                    and req.get("id") in self.resolved_ids):
                pair = req.get("pair", [])
                if len(pair) == 2:
                    swaps.append((pair[0], pair[1]))
        return swaps
