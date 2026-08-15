"""
pilot_cpmpy_nurse_shift_weekly_cap.py — CPMpyパイロット検証 (NurseShiftWeeklyCap)

目的:
  Backend/solvers/nurse_shift_weekly_cap_solver.py の docplex.cp モデルの中核
  （optional interval_var + presence_of + no_overlap + end_before_start(delay)）
  を CPMpy(ortools/CP-SATバックエンド)で等価に組めるかを検証する。

  DESIGN_2026-08-12_cp_sat_backend_support.md の「追記2」で car_sequencing
  （純粋な組合せ最適化）とmeeting_room（interval_varのstart/end/sizeが全て
  定数＝実質「固定時間枠＋割当先選択」のみ）の2ドメインが検証済みだったが、
  meeting_roomパイロットのコメントに明記されている通り「真のフレキシブル
  スケジューリング（start/endが決定変数になるRCPSP系: nurse_shift_weekly_cap
  等）をこの結果だけで検証したことにはならない」として未検証のまま残されていた。
  本スクリプトはその「次のステップ」候補（DESIGN文書内で明示的に名指しされている）
  としてのパイロット。

このスクリプトが検証する対応関係（本ドメイン固有の新規部分に絞る）:
  docplex.cp                                          CPMpy
  --------------------------------------------------------------------------
  mdl.interval_var(start=(sw,ew), end=(sw,ew),        present = cp.boolvar()
    size=dur, optional=True)                          start = cp.intvar(sw, ew-dur)
    ※ start=(sw,ew)かつend=(sw,ew)かつsize=durという                          end   = start + dur
      3条件の組み合わせは、実質 start∈[sw, ew-dur] という
      「窓 ew-sw が duration を超える場合は実際に自由度が
      残る」区間変数（本ドメインの本質）。
  mdl.presence_of(itv)                                present（そのままbool）
  mdl.no_overlap(itvs_for_staff)（optional interval    cp.NoOverlapOptional(starts, durs,
    のリストなので暗黙にoptional対応版）                  ends, presents)
  mdl.end_before_start(nc_itv, dc_itv, delay=d)         (present[nc] & present[dc]).implies(
    （両方optionalなので「両方presentなら」の含意）          end[nc] + d <= start[dc])
  Σ present_of(...) >= required_count（hard）           cp.sum(present...) >= required_count

  【本パイロットで意図的に対象外にした部分】（理由: いずれもpresence布尓変数上の
  純粋な線形sum制約で、interval_var固有の新規性が無く、car_sequencing
  パイロットのスライディングウィンドウ制約と同一パターンで既に構造検証済みのため、
  本パイロットでは「真にフレキシブルなstart変数 × no_overlap/end_before_start」
  という本質部分の検証に絞った）:
    - 最低CHIEF数（Σ presence(CHIEF) >= min_chiefs）
    - 日次労働時間上限（Σ duration×presence <= cap）
    - ローリング週次夜勤上限（solvers/base/rolling_window.py 相当のsum窓制約）
    - quantity_requirement の soft モード（shortfallペナルティ化。meeting_room
      パイロットのUNASSIGNED_PENALTYと同一パターンで検証済み）

  目的関数はヒアリング項1（総人件費）のみを対象にした（項2/項3はpresence上の
  線形項で、meeting_roomパイロットのUNASSIGNED_PENALTY/waste/bonus項と同一
  パターンのため新規性が無い）。

注意（このサンドボックスの制約）:
  docplex.cp / CP Optimizerの実行エンジンがこの環境には無いため、docplex.cp側を
  実際に解いて数値比較することはできない。代わりに、スタッフ数・タスク数を
  小さく抑えたテストケースについて、full assignment（どのスタッフをどのタスクに
  割り当てるか）とフレキシブル区間のstart値の全組み合わせを尽くす全探索で真の
  最適値を求め、CPMpy(CP-SAT)の解と一致するかを検証する。

  CPLEX側との実行結果の数値一致は、CPLEX Optimization Studioがインストール
  された環境で、本スクリプト末尾の compare_with_docplex() を使って別途確認する
  必要がある（呼び出し先は本番の NurseShiftWeeklyCapSolver ではなく、本パイロットが
  対象外にした制約群も含む本番モデルとの比較になるため、対象外にした制約が
  効かないシナリオ（CHIEF不要・日次上限余裕あり・週次夜勤余裕あり）を使うこと）。
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Tuple

import cpmpy as cp


def build_candidates(staff_list: List[Dict], tasks: List[Dict]) -> List[Dict]:
    """本パイロットでは資格照合を省略し、全スタッフが全タスクの候補になる
    （本番のbuild_candidates()の資格フィルタ部分はrequirement_expr.py側の
    純粋なブール式評価であり、interval_var/スケジューリングとは無関係のため
    対象外）。"""
    candidates = []
    for task in tasks:
        for staff in staff_list:
            candidates.append({
                "id": f"{task['id']}__{staff['id']}",
                "task_id": task["id"],
                "staff_id": staff["id"],
                "start_window": task["start_window"],
                "end_window": task["end_window"],
                "duration": task["duration"],
                "day": task["day"],
                "is_night_shift": task.get("is_night_shift", False),
                "required_count": task.get("required_count", 1),
                "hourly_rate": staff["hourly_rate"],
            })
    return candidates


def build_cpmpy_model(candidates: List[Dict], config: Dict):
    min_rest = config.get("min_rest_after_night", 480)

    present: Dict[str, "cp.boolvar"] = {c["id"]: cp.boolvar(name=f"present_{c['id']}") for c in candidates}
    start: Dict[str, "cp.intvar"] = {
        c["id"]: cp.intvar(c["start_window"], c["end_window"] - c["duration"], name=f"start_{c['id']}")
        for c in candidates
    }
    end = {c["id"]: start[c["id"]] + c["duration"] for c in candidates}

    m = cp.Model()

    # --- ハード制約: タスクごとの必要人数（quantity_requirement, hard, at_least）
    by_task: Dict[str, List[Dict]] = {}
    for c in candidates:
        by_task.setdefault(c["task_id"], []).append(c)
    for tid, tcands in by_task.items():
        required = tcands[0]["required_count"]
        m += (cp.sum([present[c["id"]] for c in tcands]) >= required)

    # --- ハード制約: スタッフごとの no_overlap（optional interval、真にフレキシブルな
    #     start込み） + 夜勤後最低休憩（end_before_start + delay、両方presentの場合のみ）
    by_staff: Dict[str, List[Dict]] = {}
    for c in candidates:
        by_staff.setdefault(c["staff_id"], []).append(c)

    for sid, scands in by_staff.items():
        if len(scands) > 1:
            starts = [start[c["id"]] for c in scands]
            durs   = [c["duration"] for c in scands]
            ends   = [end[c["id"]] for c in scands]
            pres   = [present[c["id"]] for c in scands]
            m += cp.NoOverlapOptional(starts, durs, ends, pres)

        night_cands = [c for c in scands if c["is_night_shift"]]
        day_cands   = [c for c in scands if not c["is_night_shift"]]
        for nc in night_cands:
            for dc in day_cands:
                if dc["day"] > nc["day"]:
                    both = present[nc["id"]] & present[dc["id"]]
                    m += both.implies(end[nc["id"]] + min_rest <= start[dc["id"]])

    # --- 目的関数: 総人件費（ヒアリング項1のみ）
    # 2026-08-13修正（実機比較で発覚）: 当初 `hourly_rate * duration` のみで
    # 組んでおり、本番実装（nurse_shift_weekly_cap_solver.py Step4項1）が
    # 使う `unit_cost = (duration/60.0) * hourly_rate` の「分単位→時間単位」
    # 換算(/60)が抜けていた。CPMpy(CP-SAT)は目的関数にfloat係数を受け付けない
    # ため、meeting_roomパイロットと同じSCALE手法で整数化してから解き、
    # 結果をSCALEで割り戻す。
    SCALE = 100  # 本番の `int(unit_cost * 100)` と同じスケール
    cost_terms = [
        round((c["duration"] / 60.0) * c["hourly_rate"] * SCALE) * present[c["id"]]
        for c in candidates
    ]
    m.minimize(cp.sum(cost_terms))

    return m, present, start, end


def solve_with_cpmpy(candidates: List[Dict], config: Dict, time_limit=10):
    SCALE = 100
    m, present, start, end = build_cpmpy_model(candidates, config)
    ok = m.solve(solver="ortools", time_limit=time_limit)
    if not ok:
        return {"feasible": False, "objective": None, "assignment": None}
    assignment = {}
    for c in candidates:
        if present[c["id"]].value():
            assignment[c["task_id"]] = assignment.get(c["task_id"], []) + [
                (c["staff_id"], int(start[c["id"]].value()), int(end[c["id"]].value()))
            ]
    obj_scaled = m.objective_value()
    objective = (obj_scaled / SCALE) if obj_scaled is not None else None
    return {"feasible": True, "objective": objective, "assignment": assignment}


# ---------------------------------------------------------------------------
# 独立検証: 全探索（docplex.cpにもCPMpyにも依存しない）
# ---------------------------------------------------------------------------

def brute_force_optimal(candidates: List[Dict], config: Dict):
    """
    コスト（項1）は「どのタスクをどのスタッフに割り当てるか」という presence
    の組み合わせのみに依存し、フレキシブルなstart値そのものには依存しない
    （duration・hourly_rateは固定値のため）。したがって:
      1. まずタスク→担当スタッフ集合（サイズ=required_count）の割当を全探索
      2. 各割当について、no_overlap・夜勤後休憩を満たすstart値の組が
         存在するか（フレキシブルなタスクのみ）を全探索でチェック
      3. 実行可能な割当の中でコスト最小のものを採用
    という2段探索で、真の最適値を厳密に求められる。
    """
    min_rest = config.get("min_rest_after_night", 480)

    tasks: Dict[str, Dict] = {}
    for c in candidates:
        tasks.setdefault(c["task_id"], {
            "task_id": c["task_id"], "start_window": c["start_window"],
            "end_window": c["end_window"], "duration": c["duration"],
            "day": c["day"], "is_night_shift": c["is_night_shift"],
            "required_count": c["required_count"],
        })
    staff_ids = sorted({c["staff_id"] for c in candidates})
    rate = {c["staff_id"]: c["hourly_rate"] for c in candidates}
    task_ids = list(tasks.keys())

    def flex_domain(t):
        return list(range(t["start_window"], t["end_window"] - t["duration"] + 1))

    def staff_feasible(assigned_task_ids: List[str]):
        """このスタッフに割り当てられたタスク集合について、no_overlap・夜勤後休憩を
        満たすstartの組み合わせが存在するかを全探索。存在すればTrueを返す。"""
        if len(assigned_task_ids) <= 1:
            return True
        domains = [flex_domain(tasks[tid]) for tid in assigned_task_ids]
        for combo in itertools.product(*domains):
            s = dict(zip(assigned_task_ids, combo))
            e = {tid: s[tid] + tasks[tid]["duration"] for tid in assigned_task_ids}
            ok = True
            # no_overlap
            for i in range(len(assigned_task_ids)):
                for j in range(i + 1, len(assigned_task_ids)):
                    ti, tj = assigned_task_ids[i], assigned_task_ids[j]
                    if s[ti] < e[tj] and s[tj] < e[ti]:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                continue
            # 夜勤後休憩
            for tid_n in assigned_task_ids:
                if not tasks[tid_n]["is_night_shift"]:
                    continue
                for tid_d in assigned_task_ids:
                    if tasks[tid_d]["is_night_shift"]:
                        continue
                    if tasks[tid_d]["day"] > tasks[tid_n]["day"]:
                        if e[tid_n] + min_rest > s[tid_d]:
                            ok = False
                            break
                if not ok:
                    break
            if ok:
                return True
        return False

    # required_count は全タスク1固定の前提（本パイロットのシナリオ範囲）で、
    # 「どのスタッフ1名を割り当てるか」の全組み合わせを尽くす。
    for t in tasks.values():
        assert t["required_count"] == 1, "brute_force_optimal は required_count=1 前提の簡易実装"

    best = None
    for combo in itertools.product(staff_ids, repeat=len(task_ids)):
        task_to_staff = dict(zip(task_ids, combo))
        staff_to_tasks: Dict[str, List[str]] = {}
        for tid, sid in task_to_staff.items():
            staff_to_tasks.setdefault(sid, []).append(tid)

        if not all(staff_feasible(tids) for tids in staff_to_tasks.values()):
            continue

        # 2026-08-13修正: build_cpmpy_model()と同じ (duration/60.0)*rate 換算に揃える
        # （本番のunit_cost定義と同一。修正前は換算が抜けており、CPMpy側とは
        # 一致していてもdocplex.cp側の実測とは60倍ずれていた）。
        cost = sum(
            (tasks[tid]["duration"] / 60.0) * rate[sid]
            for tid, sid in task_to_staff.items()
        )
        if best is None or cost < best[0] - 1e-9:
            best = (cost, dict(task_to_staff))
    return best


def _scenario_flexible_conflict():
    """
    真のフレキシブルスケジューリングの核心を突く最小シナリオ。

    2026-08-13修正: 実機（CPLEXあり環境）でのcompare_with_docplex()実測で、
    docplex.cp側のtotal_cost=1・CPMpy側=150という乖離が発覚した。原因は
    このシナリオが元々「抽象的な小さい整数」（duration=3等）だったため、
    本番の`round((duration/60.0)*hourly_rate)`という「分→時間換算してから
    1件ごとに四捨五入」する処理が、duration=3分では(3/60)*10=0.5円のような
    極小値になり、Pythonの偶数丸め（round-half-to-even）で0に潰れてしまい
    （複数件が0円に丸められ、docplex.cp側のtotal_costがほぼ消えていた）。
    CPMpy側の計算式そのものにも `/60.0` 換算漏れという別のバグが重なっていた
    （上のbuild_cpmpy_model()参照）ため、2つの問題が重なって数値が大きく
    乖離していた。

    今回、全ての時間値を×20して duration=60分（ちょうど1時間）に揃えた。
    これにより (duration/60.0) = 1.0 と割り切れ、四捨五入による誤差が
    一切発生しない。構造（どのタスクが自由度を持つか・T1/T4が常に衝突する
    ことに変わりはない）は元のシナリオと相似形のまま維持している:

      T1: day1, window[0,120], dur=60   → 自由度あり(start∈{0..60})
      T2: day1, window[200,260], dur=60, night_shift → 自由度なし(start=200固定)
      T3: day2, window[400,520], dur=60 → 自由度あり(start∈{400..460})
      T4: day1, window[40,100], dur=60  → 自由度なし(start=40固定)。T1の
          start∈{0..60}のどれを選んでも[40,100)と重ならないstartが存在しない
          （T1終了<=40は不可能、T1開始>=100も不可能）→ T1/T4は常に衝突。
      min_rest_after_night=160: T2(night, end=260固定)とT3(day2)を同一スタッフが
      担当する場合、start(T3) >= 260+160 = 420 でなければならない。
      S1(rate=10) < S2(rate=20) のコスト差で、T1/T4分割時に「安い方に3タスク、
      高い方に1タスク」を選ぶ動機を作る（想定最適解: 片方3件×10円+片方1件×20円
      = 50円、docplex.cp側のtotal_costとも一致するはず）。
    """
    staff = [
        {"id": "S1", "hourly_rate": 10},
        {"id": "S2", "hourly_rate": 20},
    ]
    tasks = [
        {"id": "T1", "day": 1, "start_window": 0,   "end_window": 120, "duration": 60, "is_night_shift": False, "required_count": 1},
        {"id": "T2", "day": 1, "start_window": 200, "end_window": 260, "duration": 60, "is_night_shift": True,  "required_count": 1},
        {"id": "T3", "day": 2, "start_window": 400, "end_window": 520, "duration": 60, "is_night_shift": False, "required_count": 1},
        {"id": "T4", "day": 1, "start_window": 40,  "end_window": 100, "duration": 60, "is_night_shift": False, "required_count": 1},
    ]
    config = {"min_rest_after_night": 160}
    return staff, tasks, config


def debug_check_single_staff_triple():
    """
    2026-08-13追加（診断用）: compare_with_docplex()の実測で
    docplex.cp側が想定の最適解（S1={T1,T2,T3}(30) + S2={T4}(20) = 50）ではなく
    S1={T1,T2}(20) + S2={T3,T4}(40) = 60 を返した原因を切り分けるための関数。

    スタッフをS1だけ・タスクをT1,T2,T3だけに絞り込み、「T1,T2,T3を1人の
    スタッフが同時に担当することが本番モデル上そもそも可能か」を単独で
    検証する。もしこれ単体でinfeasibleになるなら、no_overlap/夜勤後休憩
    以外に見落としている制約がある。feasibleでcost=30になるなら、
    2人モデルでの全体最適化側（探索・目的関数の組み方）に問題がある。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver

    staff, tasks, config = _scenario_flexible_conflict()
    s1_only = [s for s in staff if s["id"] == "S1"]
    t123 = [t for t in tasks if t["id"] in ("T1", "T2", "T3")]
    min_rest = config.get("min_rest_after_night", 480)

    solver_input = {
        "meta": {"instance_name": "debug_s1_triple"},
        "staff": [
            {
                **s,
                "availability": {"start": -1_000_000, "end": 1_000_000},
                "work_limits": {"min_rest_after_night_shift": min_rest},
            }
            for s in s1_only
        ],
        "tasks": t123,
        "config": {**config, "time_limit": 10, "preference_penalty_weight": 0},
        "issue_statuses": {},
    }
    result = NurseShiftWeeklyCapSolver(solver_input).solve()
    print(f"feasible = {result['feasible']}")
    print(f"issues = {[i.get('id') + ': ' + i.get('title','') for i in result.get('issues', [])]}")
    if result["solutions"]:
        sol = result["solutions"][0]
        print(f"total_cost = {sol['metrics']['total_cost']}")
        print(f"assigned tasks = {[(t['task_id'], t['staff_id'], t['start'], t['end']) for t in sol['tasks']]}")
    return result


def compare_with_docplex(staff: List[Dict], tasks: List[Dict], config: Dict):
    """CPLEX Optimization Studio(cpoptimizer)がローカルにあるユーザー環境で実行
    する用。Backend/solvers/nurse_shift_weekly_cap_solver.py の本番クラスを
    直接叩いて、CPMpy(CP-SAT)の目的関数値と突き合わせる。

    注意: 本番クラスは本パイロットが対象外にしたCHIEF/日次上限/週次夜勤上限/
    希望考慮/資格照合も含むフルモデルなので、_scenario_flexible_conflict()の
    ようにそれらが効かない（min_chiefs=0, is_chief不使用, 日次上限に余裕あり,
    週次夜勤上限に余裕あり, 資格要件なし, preferences未設定）シナリオでのみ
    項1（総人件費）が一致するはず。

    2026-08-13修正（実機比較で発覚した2つ目の問題）: 本番のbuild_candidates()は
    `avail_start_abs = avail["start"] + day_offset`（day_offset=(task_day-1)*1440）
    という「スタッフのavailabilityは日ごとに繰り返す相対時刻」という前提で
    day_offsetを加算するが、タスク側のstart_window/end_windowには
    day_offsetを加算しない（既にconverter.pyが絶対分に変換済みという前提）。
    本パイロットのシナリオはこの規約を満たしておらず（T3のstart_window=400は
    「day2の絶対分」のつもりだったが、実際には「day1の絶対分」としてしか
    扱われない）、availability={"start":0,...}だとday_offset=1440適用後に
    avail_start_abs=1440 > T3のstart_window=400となり、T3の割当候補が
    全スタッフで0件になってfeasibility自体が変わってしまっていた（症状:
    docplex.cp側のtotal_costが想定よりtaskが1件少ない分だけ安く出る）。
    シナリオ側のstart_window/end_windowをconverter.py互換の絶対分に作り直す
    より、availability.startを十分小さい負の値にして「どのday_offsetが
    足されても実質無制限」にする方が変更が小さく安全なため、こちらで対応する。
    Cowork(このサンドボックス)ではcpoptimizerが無いため実行できない。"""
    # 2026-08-13修正（実機比較で発覚した3つ目・最大の問題）: debug_check_single_staff_triple()
    # の実測で「S1が1人でT1,T2,T3を担当する」という、本パイロットが前提としていた
    # 割当が本番モデル上そもそもinfeasibleと判明した（T3がunderstaffedのまま返る）。
    # 原因は config.get("min_rest_after_night", ...) という本パイロット独自の
    # （config直下の）キー設計が、本番実装の実際のフィールド位置と一致していな
    # かったこと。本番は`config`ではなく**スタッフ個別**の
    # `staff.work_limits.min_rest_after_night_shift`を見ており
    # （nurse_shift_weekly_cap_solver.py 508行目）、本パイロットのconfigに
    # "min_rest_after_night"を入れても本番側では一切参照されず、常に既定値
    # DEFAULT_MIN_REST_AFTER_NIGHT=480分が使われていた。260(T2終了)+480=740分は
    # T3のstart候補[400,460]を完全に超えるため、T2とT3を同一スタッフに割り当てる
    # ことは（480分ルールの下では）常にinfeasibleだった——これはCP-SATモデルの
    # ロジックの誤りではなく、本パイロットのシナリオ構築（config配置ミス）の
    # 誤りだった。ここでstaff側のwork_limitsに正しく注入し、本パイロットが
    # 意図した値（160分）を本番モデルにも実際に反映させる。
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Backend/ をパスに追加
    from solvers.nurse_shift_weekly_cap_solver import NurseShiftWeeklyCapSolver

    min_rest = config.get("min_rest_after_night", 480)
    solver_input = {
        "meta": {"instance_name": "cpmpy_pilot_compare"},
        "staff": [
            {
                **s,
                "availability": {"start": -1_000_000, "end": 1_000_000},
                "work_limits": {"min_rest_after_night_shift": min_rest},
            }
            for s in staff
        ],
        "tasks": tasks,
        "config": {**config, "time_limit": 10, "preference_penalty_weight": 0},
        "issue_statuses": {},
    }
    docplex_result = NurseShiftWeeklyCapSolver(solver_input).solve()
    docplex_cost = None
    docplex_assignment = None
    if docplex_result["solutions"]:
        sol = docplex_result["solutions"][0]
        docplex_cost = sol["metrics"]["total_cost"]
        # 2026-08-13追加: 数値だけでなく実際の割当も出す（どのタスクが誰に
        # 割り当てられたか）。objective不一致の原因切り分け用の診断情報。
        docplex_assignment = [
            {"task_id": t["task_id"], "staff_id": t["staff_id"],
             "start": t["start"], "end": t["end"], "cost": t["cost"]}
            for t in sol.get("tasks", [])
        ]

    candidates = build_candidates(staff, tasks)
    cpmpy_result = solve_with_cpmpy(candidates, config)

    print(f"docplex.cp total_cost = {docplex_cost}")
    print(f"docplex.cp assignment = {docplex_assignment}")
    print(f"docplex.cp understaffed_count = {docplex_result['solutions'][0]['metrics'].get('understaffed_count') if docplex_result['solutions'] else None}")
    print(f"docplex.cp issues = {[i.get('id') for i in docplex_result.get('issues', [])]}")
    print(f"CPMpy(CP-SAT) objective (項1のみ) = {cpmpy_result['objective']}")
    print(f"CPMpy(CP-SAT) assignment = {cpmpy_result['assignment']}")
    print(f"MATCH: {docplex_cost is not None and abs(docplex_cost - cpmpy_result['objective']) < 1e-6}")
    print("注意: docplex側はUNDERSTAFFING_PENALTY等の項も含むtotal_costなので、")
    print("      required_countが全て満たされるシナリオ（本シナリオは該当）でのみ")
    print("      項1単体としての単純比較が成立する。")
    return docplex_cost, cpmpy_result["objective"]


if __name__ == "__main__":
    staff, tasks, config = _scenario_flexible_conflict()
    candidates = build_candidates(staff, tasks)

    print("=== flexible scheduling + conflict + night-rest (真のフレキシブルスケジューリング) ===")
    cpmpy_result = solve_with_cpmpy(candidates, config)
    bf_cost, bf_assign = brute_force_optimal(candidates, config)

    print(f"CPMpy(CP-SAT) objective = {cpmpy_result['objective']}")
    print(f"CPMpy(CP-SAT) assignment (task_id -> [(staff_id, start, end)]) = {cpmpy_result['assignment']}")
    print(f"Brute force  optimal    = {bf_cost}")
    print(f"Brute force  example assignment (task_id -> staff_id) = {bf_assign}")

    match = abs(cpmpy_result["objective"] - bf_cost) < 1e-6
    print(f"MATCH: {match}")
    assert match, "CPMpy objective does not match brute-force optimum!"

    # 構造チェック: T1とT4が同一スタッフに割り当てられていないことを確認
    # （NoOverlapOptionalが「startをどう選んでも重なる」構造的排他を正しく
    #   検出できていることの直接的な証拠）
    assign = cpmpy_result["assignment"]
    staff_t1 = assign["T1"][0][0]
    staff_t4 = assign["T4"][0][0]
    print(f"\n構造チェック: T1担当={staff_t1}, T4担当={staff_t4} (異なるはず) -> "
          f"{'OK' if staff_t1 != staff_t4 else 'NG'}")
    assert staff_t1 != staff_t4, "NoOverlapOptionalが構造的排他を見逃した可能性がある"

    # 構造チェック: T2/T3が同一スタッフなら、T3のstartが420以上であることを確認
    staff_t2 = assign["T2"][0][0]
    staff_t3, start_t3, _ = assign["T3"][0]
    if staff_t2 == staff_t3:
        print(f"構造チェック: T2/T3が同一スタッフ({staff_t2})。T3 start={start_t3} (>=420のはず) -> "
              f"{'OK' if start_t3 >= 420 else 'NG'}")
        assert start_t3 >= 420, "夜勤後休憩制約(end_before_start+delay)が効いていない可能性がある"
    else:
        print(f"構造チェック: T2({staff_t2})とT3({staff_t3})は別スタッフ担当のため休憩制約は非対象")

    print("\nCPMpy(CP-SAT)の最適値がブルートフォースと一致し、")
    print("NoOverlapOptional・end_before_start(delay)相当の含意制約が")
    print("真にフレキシブルなstart変数のもとでも正しく機能することを確認しました。")
    print("\n(参考) CPLEXがローカルにある環境では以下でdocplex.cpとも比較できます:")
    print("  from pilot_cpmpy_nurse_shift_weekly_cap import compare_with_docplex, _scenario_flexible_conflict")
    print("  compare_with_docplex(*_scenario_flexible_conflict())")
