"""
Backend/solvers/base/constraint_applier.py

BaseConstraintApplier — 全ソルバー共通の制約ディスパッチ機構 + 汎用ハンドラ (層A)

設計方針:
  - ディスパッチテーブル方式（_CONSTRAINT_HANDLERS）と apply_all() は
    cplex_dynamic_solver.ConstraintApplier の実装をそのまま踏襲する。
    新しい制約タイプを追加する手順は変わらない:
      1. _handle_xxx(self, p: Dict) メソッドを追加
      2. _register_domain_handlers() で self._CONSTRAINT_HANDLERS に1エントリ追加
      ソルバー本体には一切触れない。

  - no_overlap / precedence / shift_window / shift_break は
    必須interval（is_optional_intervals=False、例: YardPlanning）と
    optional interval（is_optional_intervals=True。2026-07-11削除の
    EventStaffingがこの型だった。現在このフラグを使うドメインはないが、
    汎用フックとして維持）の両方で「同じ意味」を持つ制約だが、interval_var
    の取り扱いが異なる。この差異は _resolve_itv() / _add_window() /
    _add_break() の3つのフックメソッドに閉じ込め、サブクラスは
    is_optional_intervals のフラグと、必要なら絶対値変換（秒↔分など）の
    override だけで対応する。

  - cumulative（2026-07-18追加）: no_overlap（同時に1つの仕事にしか使えない）の
    対になる、容量付き資源の同時使用制約（同時に複数の仕事に使えるが上限がある）。
    ヒアリングシート§9「資源の同時使用に関するルール」の a/b選択に対応する
    （a→no_overlap, b→cumulative）。DESIGN_2026-07-18_layer_ab_pattern_library.md
    2-2-1節の決定に基づき追加。pulse関数の総和が capacity を超えないことを制約する
    標準的なdocplexパターンを用いる。

サブクラスが必ず実装するもの:
  - __init__ で self.mdl, self.task_itvs (or assignment_itvs) をセットしてから
    super().__init__() を呼ぶか、これらを直接代入する。
  - _register_domain_handlers(): ドメイン固有ハンドラを
    self._CONSTRAINT_HANDLERS に追加する。

サブクラスが必要に応じて override するもの:
  - is_optional_intervals (bool, default False)
  - time_unit_seconds (bool, default True): windows/breaks の数値が
    秒単位かどうか。False の場合は分単位として扱う。
  - _resolve_itv(task_id) -> interval_var | None
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BaseConstraintApplier:
    """
    Solver Input DSL の constraints 配列を CP Optimizer 制約式に動的マッピングする
    共通基底クラス。

    属性:
      mdl       : CpoModel
      task_itvs : Dict[str, interval_var]  タスク/割り当てID -> interval_var

    is_optional_intervals = True のサブクラス（2026-07-11削除のEventStaffing
    が該当していた。現在このフラグを使うドメインはない）では、
    task_itvs に optional interval_var を渡すこと。start_of/end_of の
    absentValue 引数 (第2引数 0) は本クラス側で自動的に付与する。
    """

    # --- サブクラスで override 可能な設定値 ---
    is_optional_intervals: bool = False
    time_unit_seconds: bool = True  # True: windows/breaksの値は秒。False: 分（自動判定）。

    # 複数windowが指定された場合の shift_window の挙動。
    #   "logical_or" : いずれかの窓に収まることを制約する（YardPlanning型）
    #   "skip"       : 何もしない。interval_var の生成時点で範囲が
    #                  設定済みであることを前提とする
    #                  （2026-07-11削除のEventStaffingがこの型だった）
    multi_window_policy: str = "logical_or"

    def __init__(self, mdl: Any, task_itvs: Dict[str, Any]):
        self.mdl = mdl
        self.task_itvs = task_itvs

        # 基底ハンドラを先に登録し、その後サブクラス固有ハンドラで拡張・追加する
        self._CONSTRAINT_HANDLERS: Dict[str, Callable[[Dict], None]] = {}
        self._CONSTRAINT_HANDLERS.update(self._base_handlers())
        self._register_domain_handlers()

    # -------------------------------------------------------------------------
    # 基底ハンドラの登録
    # -------------------------------------------------------------------------

    def _base_handlers(self) -> Dict[str, Callable[[Dict], None]]:
        """
        全ドメイン共通の制約ハンドラ。
        サブクラスは _register_domain_handlers() 内で
        self._CONSTRAINT_HANDLERS["no_overlap"] = self._my_no_overlap のように
        上書きすることもできるが、通常は不要。
        """
        return {
            "no_overlap":   self._handle_no_overlap,
            "cumulative":   self._handle_cumulative,
            "precedence":   self._handle_precedence,
            "shift_window": self._handle_shift_window,
            "shift_break":  self._handle_shift_break,
        }

    def _register_domain_handlers(self) -> None:
        """
        ドメイン固有の制約ハンドラを登録する。 (層C の差し込み口)

        実装例:
            self._CONSTRAINT_HANDLERS.update({
                "min_chief_count": self._handle_min_chief_count,
                "conflict_pair":   self._handle_conflict_pair,
            })

        基底クラスでは何もしない（ドメイン固有ハンドラがない場合はoverride不要）。
        """
        pass

    # -------------------------------------------------------------------------
    # 公開メソッド
    # -------------------------------------------------------------------------

    def apply_all(self, constraints: List[Dict]) -> Tuple[int, int]:
        """
        constraints DSL 配列を全件適用する。

        Returns:
            (applied_count, skipped_count)
        """
        applied = skipped = 0
        for c in constraints:
            c_type = c.get("type", "")
            c_params = c.get("params", {})
            handler = self._CONSTRAINT_HANDLERS.get(c_type)
            if handler:
                try:
                    handler(c_params)
                    applied += 1
                    logger.debug(f"[Constraint] APPLIED: {c_type}")
                except Exception as e:
                    logger.warning(f"[Constraint] ERROR {c_type}: {e}")
                    skipped += 1
            else:
                logger.warning(f"[Constraint] UNKNOWN type: '{c_type}' — スキップ")
                skipped += 1
        return applied, skipped

    # -------------------------------------------------------------------------
    # interval 解決フック（必須/optional の差異を吸収する層）
    # -------------------------------------------------------------------------

    def _resolve_itv(self, task_id: str) -> Optional[Any]:
        """task_id（または assignment_id）から interval_var を取得する。
        基底実装は単純な辞書ルックアップ。
        ドメイン固有のID変換が必要な場合はサブクラスでoverride。
        """
        return self.task_itvs.get(str(task_id))

    def _resolve_itvs(self, task_ids: List[str]) -> List[Any]:
        result = []
        for tid in task_ids:
            itv = self._resolve_itv(tid)
            if itv is not None:
                result.append(itv)
        return result

    def _start_of(self, itv):
        """optional interval の場合、absentValue=0 を指定して start_of を取得する。"""
        if self.is_optional_intervals:
            return self.mdl.start_of(itv, 0)
        return self.mdl.start_of(itv)

    def _end_of(self, itv):
        """optional interval の場合、absentValue=0 を指定して end_of を取得する。"""
        if self.is_optional_intervals:
            return self.mdl.end_of(itv, 0)
        return self.mdl.end_of(itv)

    def _unit(self) -> int:
        """windows/breaks の数値に掛ける係数。秒単位DSLなら60、分単位DSLなら1。
        time_unit_seconds=False の場合は _to_value() 側で自動判定するため
        ここでは 1 を返す。"""
        return 60 if self.time_unit_seconds else 1

    def _to_value(self, v: int) -> int:
        """windows/breaks の数値をモデル内部の単位（分）に変換する。

        time_unit_seconds=True  : v * 60 （秒 -> 分換算前提のYardPlanning型。
                                    実際には呼び出し側で *60 して秒のまま使う
                                    ケースもあるため、_unit() と併用する）
        time_unit_seconds=False : 1440分(=86400秒)より大きい値は秒とみなして
                                    分に変換する自動判定
                                    （2026-07-11削除のEventStaffingがこの型だった）。
        """
        if self.time_unit_seconds:
            return v
        return v // 60 if v > 1440 else v

    # -------------------------------------------------------------------------
    # 基底ハンドラ実装
    # -------------------------------------------------------------------------

    def _handle_no_overlap(self, p: Dict):
        """
        リソース非重複制約。
        is_optional_intervals=True の場合も docplex の no_overlap は
        optional interval_var をそのまま受け付けるため、特別な処理は不要。
        （absent な interval は no_overlap の対象から自動的に除外される）
        """
        task_ids = p.get("task_ids", [])
        itvs = self._resolve_itvs(task_ids)
        if len(itvs) > 1:
            self.mdl.add(self.mdl.no_overlap(itvs))

    def _handle_cumulative(self, p: Dict):
        """
        容量付き資源の同時使用制約（no_overlapの対）。

        params:
            task_ids:     List[str]  対象タスクID
            capacity:     int        資源の同時使用上限（例: 倉庫の同時停車枠数）
            requirements: Dict[str, int]  task_id -> 使用量（省略時は各タスク1）

        任意の時点における「稼働中タスクの使用量合計」が capacity を超えないことを
        pulse関数の総和で制約する。requirements省略時は「同時に何件まで」という
        単純な同時実行件数の上限（例: 1人が同時に3件まで担当できる）になる。
        """
        task_ids = p.get("task_ids", [])
        capacity = p.get("capacity", 1)
        requirements = p.get("requirements") or {}

        pulses = []
        for tid in task_ids:
            itv = self._resolve_itv(tid)
            if itv is None:
                continue
            height = requirements.get(str(tid), 1)
            pulses.append(self.mdl.pulse(itv, height))

        if pulses:
            self.mdl.add(self.mdl.sum(pulses) <= capacity)

    def _handle_precedence(self, p: Dict):
        from_id = p.get("from_task", "")
        to_id = p.get("to_task", "")
        delay = p.get("delay", 2)
        itv_from = self._resolve_itv(from_id)
        itv_to = self._resolve_itv(to_id)
        if itv_from is not None and itv_to is not None:
            self.mdl.add(self.mdl.end_before_start(itv_from, itv_to, delay=delay))

    def _handle_shift_window(self, p: Dict):
        """
        シフト時間窓制約。

        windows: [{start, end}, ...]

        time_unit_seconds=True  (YardPlanning型):
          値は秒として扱い、*60 して内部単位に変換する。
        time_unit_seconds=False （2026-07-11削除のEventStaffingがこの型だった）:
          値は _to_value() による分/秒自動判定を行う。

        multi_window_policy:
          "logical_or" : 複数窓のいずれかに収まることを制約する。
          "skip"       : 複数窓の場合は何もしない
                         （interval_var の生成時に範囲設定済みを前提とする）。
        """
        if self.time_unit_seconds:
            windows = [(w["start"], w["end"]) for w in p.get("windows", [])]
            unit = self._unit()
        else:
            windows = [(self._to_value(w["start"]), self._to_value(w["end"])) for w in p.get("windows", [])]
            unit = 1

        if len(windows) > 1 and self.multi_window_policy == "skip":
            return

        itvs = self._resolve_itvs(p.get("task_ids", []))

        for itv in itvs:
            if len(windows) == 1:
                w_s, w_e = windows[0]
                self.mdl.add(self._start_of(itv) >= w_s * unit)
                self.mdl.add(self._end_of(itv) <= w_e * unit)
            elif len(windows) > 1:
                self.mdl.add(self.mdl.logical_or([
                    self.mdl.logical_and(
                        self._start_of(itv) >= w_s * unit,
                        self._end_of(itv) <= w_e * unit,
                    )
                    for (w_s, w_e) in windows
                ]))

    def _handle_shift_break(self, p: Dict):
        """
        休憩時間制約。各タスクは breaks のいずれの区間ともオーバーラップしない
        （区間の前に終わるか、後に始まるか）。

        breaks: [{start, end}, ...]
        単位の扱いは _handle_shift_window と同様。
        """
        if self.time_unit_seconds:
            unit = self._unit()
            convert = lambda v: v
        else:
            unit = 1
            convert = self._to_value

        itvs = self._resolve_itvs(p.get("task_ids", []))

        for brk in p.get("breaks", []):
            b_s = convert(brk["start"])
            b_e = convert(brk["end"])
            for itv in itvs:
                self.mdl.add(self.mdl.logical_or(
                    self._end_of(itv) <= b_s * unit,
                    self._start_of(itv) >= b_e * unit,
                ))
