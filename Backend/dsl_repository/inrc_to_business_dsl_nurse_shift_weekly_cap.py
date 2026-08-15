"""
inrc_to_business_dsl_nurse_shift_weekly_cap.py

INRC-II（Second International Nurse Rostering Competition）のテキスト形式
Scenario / Week data ファイルを、OptiBuddy NurseShiftWeeklyCap 用の
Business DSL（JSON）に変換する。Gate 2（DESIGN_2026-07-09の3-4節）で
「△部分一致」に分類された領域 ―― 必要人数充足(H2)・スキル要件(H4)・
夜勤インターバルまわり ―― の回帰テストに使うための、ドメインごとに
一度書けばよい変換アダプタ。

--------------------------------------------------------------------------
なぜテキスト形式を対象にしたか
--------------------------------------------------------------------------
公式サイト（http://mobiz.vives.be/inrc2/）はJSON/XML/テキストの3形式で
インスタンスを配布しているが、この実行環境からは同サイトのZIPファイルへ
直接アクセスできなかった（ネットワークallowlistでブロック）。一方、
仕様論文（Ceschia et al., "Second International Nurse Rostering
Competition (INRC-II) --- Problem Description and Rules", arXiv:1501.04177,
Appendix A）にテキスト形式の文法が完全な形で記載されている。JSON/XMLは
「同じ構造をより自己説明的にしただけ」と論文内に明記されているため、
実サンプルを見ずに正確に実装できるテキスト形式を対象にした。
Koshoshiさんの実機でZIPを取得できた場合は、まずJSON/XML内の対応する
値をこのスクリプトの入力テキスト形式に整形するか、本スクリプトの
parse_scenario/parse_week_data をJSON対応に拡張すること。

--------------------------------------------------------------------------
対応範囲（Gate 2「△部分一致」方針に沿った意図的な線引き）
--------------------------------------------------------------------------
このアダプタは INRC の全制約を移植しない。DESIGN_2026-07-09の3-4節の
方針通り、「核となる構造」だけを検証対象にし、それ以外は変換せず
レポートに出す。

  変換する（core skeleton）:
    - NURSES → staff（id, name, skills）
    - REQUIREMENTS の (shift_type, skill, day) 行で min>0 のもの
      → tasks（1行=1タスク。必要人数=min。スキル一致条件=requirement）
      ※ optimal値(S1)は変換しない。任意最適化であり必須の充足ではないため。
    - SHIFT_TYPES 名に "night"/"深夜" 等が含まれる場合、その
      (min,max)consecutive を staff.work_limits.max_consecutive_night_shifts
      の既定値として使う（全staff共通。個別contractのnight上限はINRCに
      無いため）。

  変換しない（レポートに出すだけ。理由付き）:
    - CONTRACTS の総勤務日数上限/下限、連続休日数、週末勤務上限、
      complete-weekend制約 → NurseShiftWeeklyCapのwork_limits/config に
      対応フィールドが無い。
    - FORBIDDEN_SHIFT_TYPES_SUCCESSIONS（夜勤以外の一般的な勤務帯連続禁止）
      → NurseShiftWeeklyCapはmin_rest_after_night_shiftという夜勤専用の
      インターバル制約しか持たず、汎用の勤務帯遷移禁止テーブルは無い。
    - SHIFT_OFF_REQUESTS（希望休/特定シフト回避のソフト制約=S4）
      → 2026-07-13のスキーマ変更でforbidden_tasks等の「回避したい」系
      フィールドはsolverが参照しないdead fieldとして削除済み。
      staff.approved_days_off はハード制約（必ず休ませる）用のフィールド
      であり、INRCのソフトな「回避したい」要求をここに変換すると
      ハード化してしまい実態と異なる。そのため意図的に変換しない。
    - S1（optimal coverage）, S6（総勤務数）, S7（総週末勤務数）等の
      ソフト制約 → NurseShiftWeeklyCap側に対応する目的関数項が無い。

  上記の「変換しない」項目は、変換結果と一緒に出力するレポート
  （report辞書 / --report-out で保存可能）に一覧化される。これは
  「ベンチマークで検証できるのはモデルの骨格部分まで」という
  DESIGN_2026-07-09 3-4節の注記をコード上でも可視化するための仕組み。

--------------------------------------------------------------------------
シフト時刻の割り当てについて（要確認・要調整）
--------------------------------------------------------------------------
INRC-IIのシナリオ形式は勤務帯（Early/Late/Night等）を抽象的な名前としてのみ
定義し、具体的な開始・終了時刻を持たない（論文Appendix A参照）。
NurseShiftWeeklyCapのtasksは絶対分数のstart_window/end_windowを必要とする
ため、DEFAULT_SHIFT_CLOCK_TIMES で「勤務帯名 → (開始HH:MM, 終了HH:MM)」の
既定マッピングを与えている。実際のインスタンスの勤務帯名・想定時刻が
異なる場合は、変換前にこの辞書を調整すること。

--------------------------------------------------------------------------
入力ファイル形式（INRC-II テキスト形式。論文 Appendix A 準拠）
--------------------------------------------------------------------------
Scenario ファイル例:
    SCENARIO = n005w4
    WEEKS = 4
    SKILLS = 2
    HeadNurse
    Nurse
    SHIFT_TYPES = 3
    Early (2,5)
    Late (2,3)
    Night (4,5)
    FORBIDDEN_SHIFT_TYPES_SUCCESSIONS
    Early 0
    Late 1 Early
    Night 2 Early Late
    CONTRACTS = 2
    FullTime (15,22) (3,5) (2,3) 2 1
    PartTime (7,11) (3,5) (3,5) 2 1
    NURSES = 5
    Patrick FullTime 2 HeadNurse Nurse
    Andrea FullTime 2 HeadNurse Nurse
    Stefaan PartTime 2 HeadNurse Nurse
    Sara PartTime 1 Nurse
    Nguyen FullTime 1 Nurse

Week data ファイル例:
    WEEK_DATA
    n005w4
    REQUIREMENTS
    Early HeadNurse (1,1) (0,0) (0,0) (0,0) (0,0) (1,1) (0,0)
    Early Nurse (1,2) (1,1) (1,1) (0,1) (1,1) (1,1) (0,1)
    ...
    SHIFT_OFF_REQUESTS = 3
    Sara Any Thu
    Sara Night Sat
    Stefaan Late Sat

--------------------------------------------------------------------------
使い方
--------------------------------------------------------------------------
python3 inrc_to_business_dsl_nurse_shift_weekly_cap.py \\
    <scenario.txt> <week_data.txt> <出力先.json> \\
    [--instance-name "名前"] [--report-out <report.json>]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("inrc_to_business_dsl")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# 勤務帯名 → (開始HH:MM, 終了HH:MM) の既定マッピング（要確認・要調整）。
# INRC-IIのシナリオファイルには具体的な時刻情報が無いため、慣例的な値を仮置きする。
DEFAULT_SHIFT_CLOCK_TIMES = {
    "early":   ("07:00", "15:00"),
    "day":     ("07:00", "15:00"),
    "late":    ("15:00", "23:00"),
    "evening": ("15:00", "23:00"),
    "night":   ("23:00", "07:00"),  # 翌日にまたがる
}

DEFAULT_WORK_LIMITS = {
    "max_consecutive_night_shifts": 2,
    "max_daily_hours": 8,
    "max_night_shifts_per_rolling_7days": 3,  # INRCに対応概念なし。NurseShiftWeeklyCap独自拡張のため既定値のまま
    "min_rest_after_night_shift": 480,
}


def _hhmm_to_min(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


def _is_night_shift_type(shift_type_name: str) -> bool:
    return "night" in shift_type_name.lower() or "深夜" in shift_type_name


def _clock_times_for_shift_type(shift_type_name: str) -> Tuple[str, str]:
    key = shift_type_name.strip().lower()
    if key in DEFAULT_SHIFT_CLOCK_TIMES:
        return DEFAULT_SHIFT_CLOCK_TIMES[key]
    logger.warning(
        f"勤務帯名 '{shift_type_name}' に既定の時刻マッピングが無いため "
        f"day勤務相当（07:00-15:00）を仮置きします。DEFAULT_SHIFT_CLOCK_TIMES に追記してください。"
    )
    return ("07:00", "15:00")


# ---------------------------------------------------------------------------
# Scenario パーサ
# ---------------------------------------------------------------------------

def parse_scenario(text: str) -> Dict[str, Any]:
    """INRC-II テキスト形式のScenarioファイルをパースする。"""
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip() != ""]

    scenario: Dict[str, Any] = {
        "name": None,
        "weeks": None,
        "skills": [],
        "shift_types": [],       # [{"name":.., "min_consec":.., "max_consec":..}]
        "forbidden_successions": [],  # [{"shift_type":.., "forbidden_after":[..]}]
        "contracts": [],         # [{"name":.., "total":(min,max), "consec_work":(min,max),
                                   #   "consec_off":(min,max), "max_weekends":.., "complete_weekend":bool}]
        "nurses": [],            # [{"name":.., "contract":.., "skills":[..]}]
    }

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()

        if line.startswith("SCENARIO"):
            scenario["name"] = line.split("=", 1)[1].strip()
            i += 1
            continue

        if line.startswith("WEEKS"):
            scenario["weeks"] = int(line.split("=", 1)[1].strip())
            i += 1
            continue

        if line.startswith("SKILLS"):
            count = int(line.split("=", 1)[1].strip())
            i += 1
            for _ in range(count):
                scenario["skills"].append(lines[i].strip())
                i += 1
            continue

        if line.startswith("SHIFT_TYPES"):
            count = int(line.split("=", 1)[1].strip())
            i += 1
            for _ in range(count):
                m = re.match(r"^(\S+)\s+\((\d+),(\d+)\)$", lines[i].strip())
                if not m:
                    raise ValueError(f"SHIFT_TYPES行のパースに失敗: {lines[i]!r}")
                scenario["shift_types"].append({
                    "name": m.group(1),
                    "min_consec": int(m.group(2)),
                    "max_consec": int(m.group(3)),
                })
                i += 1
            continue

        if line.startswith("FORBIDDEN_SHIFT_TYPES_SUCCESSIONS"):
            i += 1
            # shift_types件数だけ行が続く（各shift_typeにつき1行、0件でも行自体は存在）
            for _ in scenario["shift_types"]:
                parts = lines[i].strip().split()
                shift_type = parts[0]
                num_forbidden = int(parts[1])
                forbidden_after = parts[2:2 + num_forbidden]
                scenario["forbidden_successions"].append({
                    "shift_type": shift_type,
                    "forbidden_after": forbidden_after,
                })
                i += 1
            continue

        if line.startswith("CONTRACTS"):
            count = int(line.split("=", 1)[1].strip())
            i += 1
            for _ in range(count):
                parts = lines[i].strip()
                m = re.match(
                    r"^(\S+)\s+\((\d+),(\d+)\)\s+\((\d+),(\d+)\)\s+\((\d+),(\d+)\)\s+(\d+)\s+([01])$",
                    parts,
                )
                if not m:
                    raise ValueError(f"CONTRACTS行のパースに失敗: {lines[i]!r}")
                scenario["contracts"].append({
                    "name": m.group(1),
                    "total": (int(m.group(2)), int(m.group(3))),
                    "consec_work": (int(m.group(4)), int(m.group(5))),
                    "consec_off": (int(m.group(6)), int(m.group(7))),
                    "max_weekends": int(m.group(8)),
                    "complete_weekend": m.group(9) == "1",
                })
                i += 1
            continue

        if line.startswith("NURSES"):
            count = int(line.split("=", 1)[1].strip())
            i += 1
            for _ in range(count):
                parts = lines[i].strip().split()
                name, contract, num_skills = parts[0], parts[1], int(parts[2])
                skills = parts[3:3 + num_skills]
                scenario["nurses"].append({
                    "name": name,
                    "contract": contract,
                    "skills": skills,
                })
                i += 1
            continue

        i += 1

    return scenario


# ---------------------------------------------------------------------------
# Week data パーサ
# ---------------------------------------------------------------------------

def parse_week_data(text: str) -> Dict[str, Any]:
    """INRC-II テキスト形式のWeek dataファイルをパースする。"""
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip() != ""]

    week: Dict[str, Any] = {
        "scenario_name": None,
        "requirements": [],   # [{"shift_type":.., "skill":.., "per_day":[(min,opt) x7]}]
        "shift_off_requests": [],  # [{"nurse":.., "shift_type":.., "day":..}]
    }

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()

        if line == "WEEK_DATA":
            week["scenario_name"] = lines[i + 1].strip()
            i += 2
            continue

        if line == "REQUIREMENTS":
            i += 1
            while i < n and not lines[i].strip().startswith("SHIFT_OFF_REQUESTS"):
                parts = lines[i].strip().split()
                shift_type, skill = parts[0], parts[1]
                pairs = []
                for tok in parts[2:9]:
                    m = re.match(r"^\((\d+),(\d+)\)$", tok)
                    if not m:
                        raise ValueError(f"REQUIREMENTS行の(min,opt)パースに失敗: {tok!r} in {lines[i]!r}")
                    pairs.append((int(m.group(1)), int(m.group(2))))
                if len(pairs) != 7:
                    raise ValueError(f"REQUIREMENTS行は7日分必要（Mon-Sun）: {lines[i]!r}")
                week["requirements"].append({
                    "shift_type": shift_type,
                    "skill": skill,
                    "per_day": pairs,
                })
                i += 1
            continue

        if line.startswith("SHIFT_OFF_REQUESTS"):
            count = int(line.split("=", 1)[1].strip())
            i += 1
            for _ in range(count):
                parts = lines[i].strip().split()
                week["shift_off_requests"].append({
                    "nurse": parts[0], "shift_type": parts[1], "day": parts[2],
                })
                i += 1
            continue

        i += 1

    return week


# ---------------------------------------------------------------------------
# Business DSL 組み立て
# ---------------------------------------------------------------------------

def build_business_dsl(
    scenario: Dict[str, Any],
    week: Dict[str, Any],
    instance_name: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    INRC-II の scenario + week data（1週間分）から NurseShiftWeeklyCap の
    Business DSL を組み立てる。同時に「変換しなかった項目」のレポートを返す。
    """
    report: Dict[str, Any] = {
        "converted": [],
        "not_converted": [],
        "warnings": [],
    }

    night_shift_type_names = {
        st["name"] for st in scenario["shift_types"] if _is_night_shift_type(st["name"])
    }

    # --- work_limits の既定値をSHIFT_TYPESから補強 ---
    work_limits = dict(DEFAULT_WORK_LIMITS)
    for st in scenario["shift_types"]:
        if st["name"] in night_shift_type_names:
            work_limits["max_consecutive_night_shifts"] = st["max_consec"]
            report["converted"].append(
                f"SHIFT_TYPES '{st['name']}' (max_consec={st['max_consec']}) → "
                f"work_limits.max_consecutive_night_shifts の既定値として全staffに適用"
            )

    # --- staff ---
    staff_list = []
    for nurse in scenario["nurses"]:
        skills_lower = [s.lower() for s in nurse["skills"]]
        staff_list.append({
            "id": nurse["name"],
            "name": nurse["name"],
            "grade": "STANDARD",  # INRCにgrade概念なし。HeadNurseはskillとして別途requirementに現れる
            "skills": skills_lower,
            "certifications": [],
            "hourly_rate": 2000,
            "availability": {"start": 0, "end": 7 * 24 * 60},
            "preferences": {"desired_tasks": []},
            "work_limits": dict(work_limits),
            "qualification_type": "RN",
            "experience_years": 0.0,
            "has_leader_cert": "headnurse" in skills_lower,
            "employment_type": "full_time" if nurse["contract"].lower().startswith("full") else "part_time",
            "approved_days_off": [],
            "paid_leave_days": [],
        })

    for c in scenario["contracts"]:
        report["not_converted"].append(
            f"CONTRACTS '{c['name']}': 総勤務数{c['total']}, 連続勤務{c['consec_work']}, "
            f"連続休日{c['consec_off']}, 週末上限{c['max_weekends']}, "
            f"complete_weekend={c['complete_weekend']} → NurseShiftWeeklyCapに対応フィールド無し"
        )
    for fs in scenario["forbidden_successions"]:
        if fs["forbidden_after"]:
            report["not_converted"].append(
                f"FORBIDDEN_SHIFT_TYPES_SUCCESSIONS: {fs['shift_type']} の前日に "
                f"{fs['forbidden_after']} を割り当て禁止 → 夜勤以外の汎用遷移禁止は未対応"
            )

    # --- tasks（REQUIREMENTSの各(shift_type, skill, day)行でmin>0のもの） ---
    tasks_list = []
    task_seq = 0
    for req in week["requirements"]:
        shift_type = req["shift_type"]
        skill = req["skill"]
        clock_start, clock_end = _clock_times_for_shift_type(shift_type)
        is_night = shift_type in night_shift_type_names
        wraps_midnight = is_night and _hhmm_to_min(clock_end) <= _hhmm_to_min(clock_start)

        for day_idx, (min_cov, opt_cov) in enumerate(req["per_day"]):
            if min_cov <= 0:
                continue
            day_num = day_idx + 1  # 1-indexed (Mon=1..Sun=7)
            start_abs = (day_idx * 24 * 60) + _hhmm_to_min(clock_start)
            if wraps_midnight:
                end_abs = ((day_idx + 1) * 24 * 60) + _hhmm_to_min(clock_end)
            else:
                end_abs = (day_idx * 24 * 60) + _hhmm_to_min(clock_end)

            task_seq += 1
            task_id = f"T_{shift_type}_{skill}_{DAY_NAMES[day_idx]}".replace(" ", "")
            tasks_list.append({
                "id": task_id,
                "name": f"{shift_type}/{skill} ({DAY_NAMES[day_idx]})",
                "day": day_num,
                "start_window": start_abs,
                "end_window": end_abs,
                "duration": end_abs - start_abs,
                "shift_type": shift_type.lower(),
                "is_night_shift": is_night,
                "requirement": {"attr": "skills", "op": "atom", "value": skill.lower()},
                "required_count": min_cov,
                "min_chiefs": 0,
                "novice_requires_senior": False,
            })
            if opt_cov > min_cov:
                report["not_converted"].append(
                    f"REQUIREMENTS {shift_type}/{skill}/{DAY_NAMES[day_idx]}: "
                    f"optimal={opt_cov} > min={min_cov} (S1目標充足) → "
                    f"required_count={min_cov}のみ変換。optimal超過分の目的関数項は無し"
                )

    report["converted"].append(f"tasks: {len(tasks_list)} 件生成（REQUIREMENTSのmin>0行から）")
    report["converted"].append(f"staff: {len(staff_list)} 件生成（NURSESから）")

    for req_off in week["shift_off_requests"]:
        report["not_converted"].append(
            f"SHIFT_OFF_REQUESTS {req_off['nurse']} {req_off['shift_type']} {req_off['day']}: "
            f"ソフトな回避希望(S4) → NurseShiftWeeklyCapのapproved_days_offはハード制約用のため、"
            f"意図的に変換しない（誤ってハード化するリスクを避けるため）"
        )

    if len(week["requirements"]) and week["requirements"][0]["per_day"] and False:
        pass  # (no-op; placeholder to keep structure explicit for future multi-week extension)

    business_dsl = {
        "problem_class": "NurseShiftWeeklyCap",
        "domain": "nurse_shift_weekly_cap",
        "id": instance_name,
        "label": f"NurseShiftWeeklyCap — INRC-II変換 ({scenario.get('name', '?')})",
        "description": (
            f"INRC-II '{scenario.get('name', '?')}' シナリオ + 1週間分のweek dataから "
            "inrc_to_business_dsl_nurse_shift_weekly_cap.py により自動生成。"
            "変換対象外の制約はreportを参照。"
        ),
        "version": "1.0",
        "tag": "HOSPITAL",
        "tagColor": "#8b5cf6",
        "meta": {
            "days": 7,
            "staffCount": len(staff_list),
            "taskCount": len(tasks_list),
            "source": "INRC-II",
            "source_scenario": scenario.get("name"),
        },
        "extensions": [
            "weekly_night_shift_cap",
            "rolling_window_constraint",
            "certification_requirement",
            "fatigue_constraint",
            "multi_day_horizon",
        ],
        "staff": staff_list,
        "tasks": tasks_list,
        "config": {
            "time_limit": 60,
            "preference_penalty_weight": 50,
            "min_preference_satisfaction_rate": 0.6,
            "operation_start": 0,
            "rolling_window_days": 7,
            "relax_incomplete_window_at_period_end": True,
            "shifts": [],
            "breaks": [],
        },
        "resources": {"ship_cranes": [], "yard_cranes": []},
        "containers": [],
    }

    return business_dsl, report


def build_business_dsl_multi_week(
    scenario: Dict[str, Any],
    weeks: List[Dict[str, Any]],
    instance_name: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    複数週分のweek dataを、日番号をオフセットしながら1本の長い horizon の
    Business DSL に連結する。

    2026-07-14追記（重要な設計判断）: INRC-II自身のhistory（境界データ・
    カウンタ）を実装してINRC方式の「週ごとに解いてhistoryを引き継ぐ」を
    再現するのではなく、この関数のように**複数週分のタスクを最初から1回の
    solveに全部渡す**方式を採る。理由は、NurseShiftWeeklyCapの
    max_consecutive_night_shifts制約（solvers/nurse_shift_weekly_cap_solver.py
    275行目付近）も、7日間ローリング夜勤上限
    （solvers/base/rolling_window.py の add_rolling_window_cap_constraints）も、
    どちらも「与えられたtasksの全日範囲」に対して汎用的にスライディング
    ウィンドウを構築する実装であり、"1週間だけ"という前提を一切ハードコードして
    いない（rolling_window.pyのrolling_window_ranges()はall_daysに対して
    window_daysを連続スライドさせるだけで、7日区切りのカレンダー週という
    概念に依存しない）。したがって、INRC側の複雑なhistory受け渡し
    （境界データ・カウンタ）を実装しなくても、単に複数週分のタスクを
    日番号をずらして1つのtasks[]にまとめて1回で解かせるだけで、
    週の境界をまたぐ連続夜勤・ローリングウィンドウの検証ができる。
    """
    report: Dict[str, Any] = {"converted": [], "not_converted": [], "warnings": []}

    night_shift_type_names = {
        st["name"] for st in scenario["shift_types"] if _is_night_shift_type(st["name"])
    }
    work_limits = dict(DEFAULT_WORK_LIMITS)
    for st in scenario["shift_types"]:
        if st["name"] in night_shift_type_names:
            work_limits["max_consecutive_night_shifts"] = st["max_consec"]

    staff_list = []
    for nurse in scenario["nurses"]:
        skills_lower = [s.lower() for s in nurse["skills"]]
        staff_list.append({
            "id": nurse["name"],
            "name": nurse["name"],
            "grade": "STANDARD",
            "skills": skills_lower,
            "certifications": [],
            "hourly_rate": 2000,
            "availability": {"start": 0, "end": len(weeks) * 7 * 24 * 60},
            "preferences": {"desired_tasks": []},
            "work_limits": dict(work_limits),
            "qualification_type": "RN",
            "experience_years": 0.0,
            "has_leader_cert": "headnurse" in skills_lower,
            "employment_type": "full_time" if nurse["contract"].lower().startswith("full") else "part_time",
            "approved_days_off": [],
            "paid_leave_days": [],
        })

    tasks_list = []
    for week_idx, week in enumerate(weeks):
        day_offset_days = week_idx * 7
        for req in week["requirements"]:
            shift_type = req["shift_type"]
            skill = req["skill"]
            clock_start, clock_end = _clock_times_for_shift_type(shift_type)
            is_night = shift_type in night_shift_type_names
            wraps_midnight = is_night and _hhmm_to_min(clock_end) <= _hhmm_to_min(clock_start)

            for day_idx, (min_cov, opt_cov) in enumerate(req["per_day"]):
                if min_cov <= 0:
                    continue
                abs_day_idx = day_offset_days + day_idx  # 0-indexed通し日数
                day_num = abs_day_idx + 1
                start_abs = (abs_day_idx * 24 * 60) + _hhmm_to_min(clock_start)
                if wraps_midnight:
                    end_abs = ((abs_day_idx + 1) * 24 * 60) + _hhmm_to_min(clock_end)
                else:
                    end_abs = (abs_day_idx * 24 * 60) + _hhmm_to_min(clock_end)

                task_id = f"T_{shift_type}_{skill}_W{week_idx}_{DAY_NAMES[day_idx]}".replace(" ", "")
                tasks_list.append({
                    "id": task_id,
                    "name": f"{shift_type}/{skill} (週{week_idx+1} {DAY_NAMES[day_idx]})",
                    "day": day_num,
                    "start_window": start_abs,
                    "end_window": end_abs,
                    "duration": end_abs - start_abs,
                    "shift_type": shift_type.lower(),
                    "is_night_shift": is_night,
                    "requirement": {"attr": "skills", "op": "atom", "value": skill.lower()},
                    "required_count": min_cov,
                    "min_chiefs": 0,
                    "novice_requires_senior": False,
                })

    report["converted"].append(
        f"{len(weeks)}週分を連結し、通し日数1〜{len(weeks)*7}のtasks {len(tasks_list)}件を生成"
    )
    report["converted"].append(f"staff: {len(staff_list)} 件生成（NURSESから、全週共通）")
    report["not_converted"].append(
        "INRC-IIのhistory（境界データ・カウンタ）は実装していない。"
        "その代わり複数週のtasksを1本のhorizonとして1回のsolveに渡すことで、"
        "週境界をまたぐ連続夜勤・ローリングウィンドウの制約自体は検証できる"
        "（詳細はbuild_business_dsl_multi_weekのdocstring参照）"
    )

    business_dsl = {
        "problem_class": "NurseShiftWeeklyCap",
        "domain": "nurse_shift_weekly_cap",
        "id": instance_name,
        "label": f"NurseShiftWeeklyCap — INRC-II変換 ({scenario.get('name', '?')}, {len(weeks)}週連結)",
        "description": (
            f"INRC-II '{scenario.get('name', '?')}' シナリオ + {len(weeks)}週分のweek dataを"
            "日番号オフセットで連結し、1本のhorizonとして"
            "inrc_to_business_dsl_nurse_shift_weekly_cap.py により自動生成。"
        ),
        "version": "1.0",
        "tag": "HOSPITAL",
        "tagColor": "#8b5cf6",
        "meta": {
            "days": len(weeks) * 7,
            "staffCount": len(staff_list),
            "taskCount": len(tasks_list),
            "source": "INRC-II",
            "source_scenario": scenario.get("name"),
            "weeks_concatenated": len(weeks),
        },
        "extensions": [
            "weekly_night_shift_cap",
            "rolling_window_constraint",
            "certification_requirement",
            "fatigue_constraint",
            "multi_day_horizon",
        ],
        "staff": staff_list,
        "tasks": tasks_list,
        "config": {
            "time_limit": 120,
            "preference_penalty_weight": 50,
            "min_preference_satisfaction_rate": 0.6,
            "operation_start": 0,
            "rolling_window_days": 7,
            "relax_incomplete_window_at_period_end": True,
            "shifts": [],
            "breaks": [],
        },
        "resources": {"ship_cranes": [], "yard_cranes": []},
        "containers": [],
    }

    return business_dsl, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario_file")
    parser.add_argument("week_data_file", nargs="+", help="1つなら単一週変換、2つ以上なら日番号オフセットで連結")
    parser.add_argument("output_json")
    parser.add_argument("--instance-name", default="nurse_shift_weekly_cap_inrc")
    parser.add_argument("--report-out", default=None, help="変換レポートを保存するJSONファイルパス")
    args = parser.parse_args()

    scenario_text = Path(args.scenario_file).read_text(encoding="utf-8")
    scenario = parse_scenario(scenario_text)

    if len(args.week_data_file) > 1:
        weeks = [parse_week_data(Path(p).read_text(encoding="utf-8")) for p in args.week_data_file]
        business_dsl, report = build_business_dsl_multi_week(scenario, weeks, args.instance_name)
        Path(args.output_json).write_text(
            json.dumps(business_dsl, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("=== 変換結果サマリー（複数週連結） ===")
        logger.info(f"staff: {len(business_dsl['staff'])} 件 / tasks: {len(business_dsl['tasks'])} 件 "
                    f"/ 通し日数: {business_dsl['meta']['days']}")
        logger.info(f"出力先: {args.output_json}")
        for msg in report["not_converted"]:
            logger.warning(f"[未変換] {msg}")
        if args.report_out:
            Path(args.report_out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return

    week_text = Path(args.week_data_file[0]).read_text(encoding="utf-8")
    week = parse_week_data(week_text)

    if week["scenario_name"] and scenario["name"] and week["scenario_name"] != scenario["name"]:
        logger.warning(
            f"week dataのシナリオ名 '{week['scenario_name']}' が "
            f"scenarioファイルの名前 '{scenario['name']}' と一致しません。組み合わせを確認してください。"
        )

    business_dsl, report = build_business_dsl(scenario, week, args.instance_name)

    Path(args.output_json).write_text(
        json.dumps(business_dsl, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info("=== 変換結果サマリー ===")
    logger.info(f"staff: {len(business_dsl['staff'])} 件 / tasks: {len(business_dsl['tasks'])} 件")
    logger.info(f"出力先: {args.output_json}")
    logger.info(f"変換した項目: {len(report['converted'])} 件 / 変換しなかった項目: {len(report['not_converted'])} 件")
    for msg in report["not_converted"]:
        logger.warning(f"[未変換] {msg}")

    if args.report_out:
        Path(args.report_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"変換レポート出力先: {args.report_out}")


if __name__ == "__main__":
    sys.exit(main())
