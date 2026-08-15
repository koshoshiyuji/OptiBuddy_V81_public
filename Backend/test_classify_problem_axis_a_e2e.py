"""
test_classify_problem_axis_a_e2e.py
=====================================

DESIGN_2026-07-30_domain_registration_classification_taxonomy.md 3-1節「軸(a)適合
チェック」を実LLMで検証するスクリプト。sandbox環境（APIキー・docplex無し）では
実行できない。Koshoshiの実環境（実LLM APIキー設定済み）で実行することを想定している。
（test_classify_problem_csplib_e2e.py / test_classify_problem_solver_technology.py
と同じ位置づけ・同じ実行前提）

検証する2つの観点（Koshoshi依頼、2026-07-30）:

  A) 精度（正しいヒアリング文をどれだけ正確に分類できるか）
     現行7ドメインのうち4件について、実際にそのドメイン登録に使われた/使う想定の
     本物のヒアリングシート（docs/test_hearings/ 配下）をそのまま入力する。
     期待結果: match_type=existing_domain（またはbase_problem）、base_domainが
     該当ドメインと一致、かつ軸(a)の食い違いフラグ（existing_domainの場合のみ、
     check_axis_a_fit()のmismatch）が**出ないこと**（＝正しい組み合わせなので
     誤検知しないことの確認）。

  B) 意図的に技術的前提が食い違うヒアリング文を渡した場合の挙動（2026-07-30拡充）
     TRAP_CASESに3パターンを用意した。うち2パターンは実際に技術的前提が
     食い違うケース（expect_mismatch=True）、1パターンは推奨解法が「両方」で
     軸(a)が沈黙してよい過検知チェック用（expect_mismatch=False）。各ケース
     N_TRAP_RUNS=10回ずつ実行し、見逃し率をより確からしく推定する
     （2026-07-30、当初N=3で3回中1回見逃しという結果が出たが、Koshoshi指摘の
     通りこの回数では信頼区間が広すぎるため引き上げた）。

     【重要な前提】いずれのケースも、ヒアリング内容がStage1aで
     match_type=existing_domainに分類されるとは限らない（LLMの判断次第で
     new_domainに倒れることもある。test_classify_problem_csplib_e2e.pyの
     ケースAが2026-07-25の実機検証でnew_domainに分類された前例と同じ限界）。
     軸(a)チェックはexisting_domain分岐にのみ組み込んでいるため、new_domainに
     倒れた回はそもそも軸(a)チェックの対象外であり、その回はpass/fail判定の
     対象から外す（run_trap_case()参照）。

  【2026-07-31改訂: 軸(a)チェックのアーキテクチャ変更】
  2026-07-30時点では、軸(a)チェックはclassify_problem()の分類プロンプトに
  同梱されており、missing_infoの文字列をキーワードで走査する
  _has_axis_a_mismatch_flag() というヘルパーで検知の有無を判定していた。
  この方式は当初の実機検証（本ファイルの旧版）で以下の問題を露呈した:
    - trap_bus_driver_as_shift: 8/10検知
    - trap_winner_determination_as_meeting_room: 2/10検知（多くの実行でLLMが
      CSPLibの類似問題自体に気づかず、missing_infoが空のまま）
    - キーワードマッチによる検知ロジック自体にも一度、早期continueによる
      見逃しバグがあった（run9/10で「一致確認」と「本当の食い違い」が同一文字列
      内に混在するケースを見逃していた）。
  この結果を受け、Koshoshiが「専用LLM呼び出しへの分離」を承認（2026-07-31）。
  domain_generator.pyに独立した check_axis_a_fit()（Stage1a.4）を新設し、
  classify_problem()からは軸(a)関連の指示・事実ブロックを完全に除去した。
  これに伴い、本ファイルもcheck_axis_a_fit()を別途呼び出す2段階の呼び出しに
  改め、_has_axis_a_mismatch_flag()によるキーワード走査は廃止した
  （check_axis_a_fit()が構造化された"mismatch": bool を直接返すため、
  文字列ヒューリスティックが原理的に不要になった）。

  本改訂の目的は「分離によって検知率がどう変わるか」をKoshoshiの実環境で
  再測定することであり、テストケース自体（ACCURACY_CASES/TRAP_CASES）は
  2026-07-30版から変更していない（比較可能性を保つため）。

元データの出所:
  - 精度検証（A）: docs/test_hearings/ 配下の実在ヒアリングシート
    （CarSequencing_hearing.md、store_site_hearing_sheet_j.md、
    nurse_shift_weekly_cap_plain_hearing_j.md、meeting_room_hearing.md）をそのまま
    使用。これらは実際のドメイン登録に使われた本物のヒアリング文であり、新規に
    作文していない。
  - 食い違い検証（B）: docs/REFERENCE_csplib_cp_mip_table.md（CSPLib由来の
    外部参照）に記載の3問題を題材に作文した文（各TRAP_CASESのnote参照）。
    prob022 Bus Driver Scheduling（MIP）→NurseShiftWeeklyCap(CP)、
    prob063 Winner Determination（MIP）→MeetingRoom(CP)、
    prob058 Discrete Lot Sizing（両方、過検知確認用）→LineChangeoverScheduler(CP)。

【重要な設計上の注意（test_classify_problem_csplib_e2e.pyと同じ注意）】
classify_problem()は、DB登録済みの動的候補ドメインをルール0（最優先）で
プロンプトに含める。実行前に `SELECT problem_class FROM dsl_definitions` で
現在の登録済みドメイン一覧を確認し、期待値とズレていないか確認すること。

実行方法:
  cd Backend && python3 test_classify_problem_axis_a_e2e.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import domain_generator as dg  # noqa: E402

N_RUNS = 3        # 精度検証(A)用。既に誤検知0/3が確認できているため据え置き。
N_TRAP_RUNS = 10  # 食い違い検証(B)用。2026-07-30、N=3では1/3ミスの信頼区間が
                  # 広すぎる（10%〜60%超まで解釈しうる）とのKoshoshi指摘を受け
                  # 実際の見逃し率をより確からしく推定するため引き上げた。
_TEST_HEARINGS_DIR = Path(__file__).parent.parent / "docs" / "test_hearings"


def _load(filename: str) -> str:
    return (_TEST_HEARINGS_DIR / filename).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# A. 精度検証: 実在ヒアリングシート4件（正しく分類され、かつ軸(a)の
#    誤検知が起きないこと）
# ─────────────────────────────────────────────────────────────

ACCURACY_CASES = [
    # (case_id, ヒアリングファイル名, 期待base_domain)
    ("acc_car_sequencing",        "CarSequencing_hearing.md",                 "CarSequencing"),
    ("acc_store_site",            "store_site_hearing_sheet_j.md",            "StoreSite"),
    ("acc_nurse_shift",           "nurse_shift_weekly_cap_plain_hearing_j.md", "NurseShiftWeeklyCap"),
    ("acc_meeting_room",          "meeting_room_hearing.md",                  "MeetingRoom"),
]


# ─────────────────────────────────────────────────────────────
# B. 食い違い検証: 3パターン（2026-07-30拡充）
#    (case_id, ヒアリング文, expect_mismatch, 出典・狙い)
#    expect_mismatch=True  : 軸(a)の食い違いを検知してほしいケース
#    expect_mismatch=False : 推奨解法が「両方」等で、軸(a)が沈黙してよい
#                            （＝過検知しないことを確認する）ケース
# ─────────────────────────────────────────────────────────────

TRAP_CASES = [
    (
        "trap_bus_driver_as_shift",
        """
バス営業所の勤務管理を最適化したい。

複数の乗務員（運転士）を、日々発生する「仕業」（乗務員1人が担当する、朝から夜までの
一連の乗務のまとまり。ちょうど看護師の日勤・夜勤のようなシフト枠に相当する）に
割り当てるシフト表を自動作成したい。

各仕業には決まった時間帯・担当区間があり、必ず誰か1名の乗務員を割り当てなければ
ならない。1人の乗務員が同じ日に担当する仕業同士は時間が重なってはならない。

最終的に使用する乗務員の人数（＝雇用しているシフト要員の総数）をできるだけ
少なく抑えたシフトの組み合わせを決定したい。特に使用したい技術やライブラリの
指定はない。
""",
        True,
        "prob022 Bus Driver Scheduling(MIP) を NurseShiftWeeklyCap(実際はCP) の"
        "シフト割当の言い回しに寄せた罠文。",
    ),
    (
        "trap_winner_determination_as_meeting_room",
        """
社内の会議室予約を最適化したい。

繁忙期は複数の部署から、人気の高い会議室・時間帯の組み合わせに希望が殺到します。
各部署には「第1候補〜第3候補の会議室×時間帯の組み合わせ」を1セットとして
提出してもらい、部署ごとに設定された優先度スコアの合計が最大になるように、
時間・会議室が重複しないセットの組み合わせを選定したい。1つの部署が提出した
複数セットのうち、採用されるのはどれか1つだけで構いません。特に使用したい
技術やライブラリの指定はありません。
""",
        True,
        "prob063 Winner Determination(組合せオークション落札者決定、MIP) を "
        "MeetingRoom(実際はCP) の会議室予約の言い回しに寄せた罠文。「重複しない"
        "候補セットの組み合わせを選び総スコアを最大化する」という構造が"
        "組合せオークションの落札者決定そのものであることを見抜けるかを見る。",
    ),
    (
        "trap_lot_sizing_should_stay_silent",
        """
単一の生産ライン上で複数の品目を生産する計画を立てたい。

品目を切り替える際には段取り替えのコストが発生し、生産していない期間の在庫には
保管コストがかかる。各期間ごとに決まっている品目別の需要を満たしつつ、段取り替え
コストと在庫コストの合計を最小化する生産計画（どの期間にどの品目をどれだけ
生産するか）を立てたい。特に使用したい技術やライブラリの指定はありません。
""",
        False,
        "prob058 Discrete Lot Sizing(推奨解法「両方」) を LineChangeoverScheduler"
        "(実際はCP) の生産ライン切替の言い回しに寄せた文。推奨解法が「両方」の"
        "場合は軸(a)チェックが無理に疑いを作らないはず、という「過検知しない"
        "こと」の確認用（罠ではなく、沈黙してよいケース）。",
    ),
]


def _check_llm_available() -> bool:
    try:
        dg.classify_problem("疎通確認", ["これは疎通確認用のダミーヒアリング文です。"])
        return True
    except Exception as e:
        print(f"[SKIP] 実LLM呼び出しに失敗したため、このテストはスキップします: {e}")
        print("       (sandbox環境ではAPIキー未設定のため失敗するのが正常。"
              "Koshoshiの実環境で実行してください)")
        return False


def run_accuracy_cases():
    print(f"\n{'#'*70}\nA. 精度検証（実在ヒアリングシート4件）\n{'#'*70}")
    results = []
    for case_id, filename, expect_domain in ACCURACY_CASES:
        hearing = _load(filename)
        print(f"\n{'='*70}\n[{case_id}] ({filename}) {N_RUNS}回実行\n{'='*70}")
        run_results = []
        for i in range(N_RUNS):
            classification = dg.classify_problem(case_id, [hearing])
            match_type  = classification.get("match_type")
            base_domain = classification.get("base_domain")
            print(f"  run{i+1}: match_type={match_type!r}, base_domain={base_domain!r}")

            axis_a_mismatch = False
            if match_type == "existing_domain" and base_domain:
                axis_a = dg.check_axis_a_fit(case_id, [hearing], base_domain)
                axis_a_mismatch = axis_a["mismatch"]
                if axis_a_mismatch:
                    print(f"          [Stage1a.4] mismatch=True concern={axis_a['concern']!r}")
            run_results.append((match_type, base_domain, axis_a_mismatch))

        match_types  = {r[0] for r in run_results}
        base_domains = {r[1] for r in run_results}
        axis_a_flags = [r[2] for r in run_results]

        ok_match  = match_types.issubset({"existing_domain", "base_problem"})
        ok_domain = base_domains == {expect_domain}
        ok_no_false_positive = not any(axis_a_flags)

        passed = ok_match and ok_domain and ok_no_false_positive
        print(f"  → match_type: {ok_match} (実際={match_types})")
        print(f"  → base_domain一致: {ok_domain} (期待={expect_domain}, 実際={base_domains})")
        print(f"  → 軸(a)誤検知なし: {ok_no_false_positive} "
              f"(誤検知が出た回数={sum(axis_a_flags)}/{len(axis_a_flags)})")
        print(f"  結果: {'PASS' if passed else 'FAIL'}")
        results.append((case_id, passed))
    return results


def run_trap_case(case_id: str, hearing: str, expect_mismatch: bool, note: str):
    print(f"\n{'#'*70}\nB. 食い違い検証: [{case_id}]\n{note}\n{'#'*70}")
    run_results = []
    n_applicable = 0  # existing_domain経路を実際に通った回数（軸(a)チェック対象）
    for i in range(N_TRAP_RUNS):
        classification = dg.classify_problem(case_id, [hearing])
        match_type  = classification.get("match_type")
        base_domain = classification.get("base_domain")
        tech_dir    = classification.get("technical_directives", "")
        print(f"\n  run{i+1}: match_type={match_type!r}, base_domain={base_domain!r}")
        if tech_dir:
            print(f"          technical_directives={tech_dir!r}")

        if match_type == "existing_domain" and base_domain:
            n_applicable += 1
            axis_a = dg.check_axis_a_fit(case_id, [hearing], base_domain)
            mismatch_flagged = axis_a["mismatch"]
            print(f"          [Stage1a.4] mismatch={mismatch_flagged!r}"
                  + (f" concern={axis_a['concern']!r}" if mismatch_flagged else ""))

            if expect_mismatch:
                # 軸(a)チェック本来の対象。食い違いを検出できたかがそのままpass/fail。
                ok = mismatch_flagged
                path_note = f"existing_domain(base_domain={base_domain})経路（軸(a)チェックの対象）"
            else:
                # 過検知しないことの確認。誤って食い違いフラグを出したらFAIL。
                ok = not mismatch_flagged
                path_note = f"existing_domain(base_domain={base_domain})経路（過検知しないことの確認）"
        elif match_type == "new_domain":
            if expect_mismatch:
                # 軸(a)(existing_domain専用)の対象外。既存のnew_domain向け
                # technical_directives推定が独立にMIPを検出できているかは
                # 参考記録として見るが、本テストのpass/fail判定には使わない
                # （対象外の経路を通っただけであり、軸(a)自体の成否ではないため）。
                ok = True
                path_note = "new_domain経路（軸(a)の対象外。参考記録のみ）"
            else:
                ok = True
                path_note = "new_domain経路（軸(a)の対象外。過検知の心配なし）"
        else:
            ok = False
            path_note = f"想定外のmatch_type: {match_type}"

        print(f"          経路: {path_note}")
        print(f"          判定: {'PASS' if ok else 'FAIL'}")
        run_results.append(ok)

    passed = all(run_results)
    print(f"\n  結果: {'PASS' if passed else 'FAIL'} "
          f"({sum(run_results)}/{len(run_results)}回成功、"
          f"うちexisting_domain経路{n_applicable}/{len(run_results)}回)")
    return [(case_id, passed)]


def run_all_trap_cases():
    results = []
    for case_id, hearing, expect_mismatch, note in TRAP_CASES:
        results += run_trap_case(case_id, hearing, expect_mismatch, note)
    return results


def run():
    if not _check_llm_available():
        return

    results = run_accuracy_cases() + run_all_trap_cases()

    print(f"\n{'='*70}\nサマリ\n{'='*70}")
    for case_id, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {case_id}")
    n_pass = sum(1 for _, p in results if p)
    print(f"\n{n_pass}/{len(results)} ケース PASS")


if __name__ == "__main__":
    run()
