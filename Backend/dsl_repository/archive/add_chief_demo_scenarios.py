"""
add_chief_demo_scenarios.py

デモ用: NurseShiftWeeklyCap に「責任者(CHIEF)体制OK版」「責任者不足(解なし)版」の
2シナリオを追加登録する。既存の標準/タイト/解なし(id 135/136/137)は一切変更しない。

背景（今回わかったバグ）:
  既存の3シナリオ（標準・タイト・解なし）は、staff全員のgradeがSENIOR/STANDARD/JUNIOR
  のいずれかで、grade="CHIEF"の人が1人もいない。ソルバー
  (solvers/nurse_shift_weekly_cap_solver.py) は各タスクの min_chiefs 制約を
  「grade=="CHIEF"の候補が1人もいなければ制約自体を追加しない」というガード
  (`if chief_vars:`) を持っているため、既存3シナリオでは責任者要件が
  全タスクで静かに無効化されている（Feasibleと出るのに責任者0名、というバグ）。

このスクリプトは、コード（solver.py）は一切変更せず、シナリオデータのみを修正した
新規シナリオを2件追加することで、上記バグを回避しつつ「責任者要件が正しく効く」
デモを可能にする。

  1. 責任者体制OK版: 田中主任(N01)・鈴木看護師(N02)・佐藤看護師(N03)をgrade=CHIEFに
     昇格。責任者要件は深夜勤のみ(min_chiefs=1、日勤・準夜勤は0)というルールに変更。
     CHIEF3名・週次夜勤上限3回/人（既存baselineのSENIOR設定を流用）に対し、
     深夜勤5枠しか無いため余裕を持ってFeasibleになる設計。

  2. 責任者不足(解なし)版: 同じ深夜勤のみルールだが、CHIEFは田中主任(N01)1名のみ
     （鈴木・佐藤はSENIORのまま）。深夜勤5枠に対し1名では個人の週次夜勤上限
     (3回/7日間ローリングウィンドウ)までしか埋められず、残り最低2枠は責任者を
     配置できないため、min_chiefs制約によりモデル全体がInfeasibleになる。

実行方法（Koshoshiさんの実機・実DBに対して直接実行してください）:
  cd /Users/arche/work/OptiBuddy_V81/Backend
  python3 dsl_repository/add_chief_demo_scenarios.py

このスクリプトは既存名と同名のシナリオが既にあれば挿入をスキップする
（Cowork側のサンドボックスから同名レコードを試験的に投入した形跡があるため、
重複防止のガードを入れてある）。
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from repository import DslRepository  # noqa: E402

SCEN_DIR = Path(__file__).parent / "scenarios"

NAME_A = "看護師シフト最適化（週単位夜勤回数上限拡張） — 責任者体制OK版"
NAME_B = "看護師シフト最適化（週単位夜勤回数上限拡張） — 責任者不足（解なし）"


def make_night_only_chief_rule(dsl):
    d = copy.deepcopy(dsl)
    for t in d["tasks"]:
        t["min_chiefs"] = 1 if t.get("is_night_shift") else 0
    return d


def build_scenario_a(baseline):
    d = make_night_only_chief_rule(baseline)
    for s in d["staff"]:
        if s["id"] in ("N01", "N02", "N03"):
            s["grade"] = "CHIEF"
    d["id"] = "nurse_shift_weekly_cap_chief_ok_demo"
    d["label"] = "NurseShiftWeeklyCap — 責任者体制OK版"
    d["description"] = (
        "責任者(CHIEF)配置ルールを修正したデモ用シナリオ。責任者は深夜勤のみ最低1名必須"
        "（日勤・準夜勤は不要という設計に変更）。田中主任・鈴木看護師・佐藤看護師の3名を"
        "grade=CHIEFに昇格（元のbaseline/tight/infeasibleシナリオはCHIEF該当者0名という"
        "データ不備があり、責任者充足チェックが常時無効化されていた）。深夜勤5枠に対し"
        "CHIEF3名・週次夜勤上限3回/人＝最大9回分の供給があり、余裕を持ってFeasibleになる設計。"
    )
    return d


def build_scenario_b(baseline):
    d = make_night_only_chief_rule(baseline)
    for s in d["staff"]:
        if s["id"] == "N01":
            s["grade"] = "CHIEF"
        elif s["id"] in ("N02", "N03"):
            s["grade"] = "SENIOR"
    d["id"] = "nurse_shift_weekly_cap_chief_shortage_demo"
    d["label"] = "NurseShiftWeeklyCap — 責任者不足（解なし）"
    d["description"] = (
        "責任者(CHIEF)不足で解なしになるデモ用シナリオ。責任者は深夜勤のみ最低1名必須"
        "（責任者体制OK版と同一ルール）。CHIEF grade該当者は田中主任1名のみ。深夜勤は"
        "5日分＝5枠あるが、田中主任個人の週次夜勤上限は3回/7日間ローリングウィンドウ。"
        "1名では最大3枠しか埋められず、残り最低2枠は責任者不在のまま required_countは"
        "満たせても min_chiefs>=1 を満たせないため、モデル全体がInfeasibleになる。"
        "責任者体制OK版（CHIEF3名）と比較することで「責任者要件を追加要員でどう解消するか」"
        "を実演できる。"
    )
    return d


def main():
    baseline = json.loads(
        (SCEN_DIR / "nurse_shift_weekly_cap_baseline.json").read_text(encoding="utf-8")
    )
    repo = DslRepository()

    existing = {
        r["name"] for r in repo.list_scenarios(domain="nurse_shift_weekly_cap")
    }

    scenario_a = build_scenario_a(baseline)
    scenario_b = build_scenario_b(baseline)

    if NAME_A in existing:
        print(f"[スキップ] 既に同名シナリオがあります: {NAME_A}")
    else:
        id_a = repo.create_scenario(
            name=NAME_A,
            dsl_json=scenario_a,
            description=scenario_a["description"],
            tag="NURSES",
            tag_color="#ec4899",
            domain="nurse_shift_weekly_cap",
        )
        print(f"[登録完了] {NAME_A} (id={id_a})")

    if NAME_B in existing:
        print(f"[スキップ] 既に同名シナリオがあります: {NAME_B}")
    else:
        id_b = repo.create_scenario(
            name=NAME_B,
            dsl_json=scenario_b,
            description=scenario_b["description"],
            tag="NURSES",
            tag_color="#ec4899",
            domain="nurse_shift_weekly_cap",
        )
        print(f"[登録完了] {NAME_B} (id={id_b})")

    print("\n--- nurse_shift_weekly_cap シナリオ一覧 ---")
    for r in repo.list_scenarios(domain="nurse_shift_weekly_cap"):
        print(f"  id={r['id']}: {r['name']}")


if __name__ == "__main__":
    main()
