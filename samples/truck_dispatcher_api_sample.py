# -*- coding: utf-8 -*-
"""
OptiBuddy Backend API サンプルクライアント（TruckDispatcher）
================================================================

OptiBuddyをバックエンドのAPIエンジンとして利用し、フロントエンドを
自社で独自開発する構成のサンプルです（docs/OptiBuddy_User_Manual.md
セクション10「カスタムUIを作りたい場合」を参照）。

TruckDispatcher（トラック配送最適化 / CVRP-TW）を題材に、以下の流れを
実演します。

  1. GET  /dsl_repository/scenarios  … 登録済みシナリオの一覧取得（参考表示のみ）
  2. POST /baseline                  … 本サンプル内蔵のダミー業務DSLを送って最適化を実行
  3. describe_interface()            … 返ってきたJSONの構造（キー名・型・
                                         配列要素数）を再帰的に表示する。
                                         ui_dsl のフィールド単位のスキーマは
                                         マニュアルに書かれていないため、
                                         実際のレスポンスから確認する。

重要: 業務DSLはダミーデータです
--------------------------------
最適化に実際に投入する業務DSL（SAMPLE_DSL）は、本サンプル用に作成した
架空の顧客名・架空の事業所名・概算座標のみで構成しています。登録済み
シナリオの実データ（顧客名・取引先名等）は一切含みません。
GET /dsl_repository/scenarios で一覧取得したシナリオはあくまで「どんな
シナリオが登録されているか」を見せるための参考表示であり、その中身
（dsl_json）はこのサンプルの最適化実行には使用していません。

前提
----
- OptiBuddy Backend（Flask）が起動していること
  （`Backend/app.py` 実行、デフォルト http://localhost:5000）
- 認証を有効化している場合（app_core.py の OPTIBUDDY_API_KEY /
  OPTIBUDDY_API_KEYS）は環境変数 OPTIBUDDY_API_KEY にキーを設定すること

実行方法
--------
    pip install requests
    export OPTIBUDDY_BASE_URL=http://localhost:5000   # 省略時のデフォルトと同じ
    export OPTIBUDDY_API_KEY=xxxx                       # 認証を有効化している場合のみ
    python truck_dispatcher_api_sample.py

注意
----
本サンプルはクラウドサンドボックス環境では動作確認していません
（CP Optimizer本体のライセンスが無い環境のため、/baseline の実際の
solve()結果までは検証できていません）。エンドポイント・ペイロード形状
・describe_interface() のロジックはBackendのソースコード
（routes_dsl_repository.py / routes_solve.py）を確認した上で作成して
いますが、実機（CPLEXライセンス環境）での動作確認をお願いします。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests

# --- 設定 ----------------------------------------------------------------
BASE_URL = os.environ.get("OPTIBUDDY_BASE_URL", "http://localhost:5000").rstrip("/")
API_KEY = os.environ.get("OPTIBUDDY_API_KEY", "")
DOMAIN = "truck_dispatcher"  # dsl_repository.scenarios.domain の値（snake_case）


# --- ダミー業務DSL（TruckDispatcher） -------------------------------------
# 架空の顧客・車両・事業所のみで構成。実際の登録済みシナリオとは無関係。
SAMPLE_DSL: dict = {
    "problem_class": "TruckDispatcher",
    "domain": "truck_dispatcher",
    "config": {
        "avg_speed_kmh": 40,
        "highway_threshold_km": 50,
        "objective_priority": ["minimize_vehicles", "minimize_distance", "balance_duty"],
        "soft_tw_penalty_per_min": 100,
        "solver_time_limit_sec": 60,
    },
    "meta": {
        "date": "2026-01-01",
        "instance_name": "サンプル配送計画（ダミーデータ）",
        "note": "本サンプルプログラム用の架空データ。実在の顧客・事業者とは関係ありません。",
    },
    "depot": {
        "id": "depot",
        "name": "サンプル運輸 デモ営業所",
        "lat": 35.70,
        "lon": 140.10,
        "open_time": "08:00",
        "close_time": "20:00",
    },
    "vehicles": [
        {
            "id": "v01",
            "name": "2tトラック(サンプル)",
            "type": "2t",
            "capacity_kg": 2000,
            "max_continuous_drive_min": 240,
            "max_duty_min": 600,
            "preferred_customer_ids": [],
        },
        {
            "id": "v02",
            "name": "4tトラック(サンプル)",
            "type": "4t",
            "capacity_kg": 4000,
            "max_continuous_drive_min": 240,
            "max_duty_min": 600,
            "preferred_customer_ids": [],
        },
    ],
    "customers": [
        {
            "id": "c01",
            "name": "サンプル商事 A倉庫",
            "lat": 35.72,
            "lon": 140.05,
            "demand_kg": 800,
            "service_time_min": 30,
            "tw_open": "09:00",
            "tw_close": "12:00",
            "restricted_vehicle_types": [],
        },
        {
            "id": "c02",
            "name": "ダミー物流センターB",
            "lat": 35.68,
            "lon": 140.15,
            "demand_kg": 1500,
            "service_time_min": 45,
            "tw_open": "09:00",
            "tw_close": "13:00",
            "restricted_vehicle_types": [],
        },
        {
            "id": "c03",
            "name": "テスト工業 C工場",
            "lat": 35.75,
            "lon": 140.20,
            "demand_kg": 600,
            "service_time_min": 30,
            "tw_open": "10:00",
            "tw_close": "15:00",
            "restricted_vehicle_types": [],
        },
        {
            "id": "c04",
            "name": "架空フーズ D配送センター",
            "lat": 35.66,
            "lon": 140.08,
            "demand_kg": 400,
            "service_time_min": 20,
            "tw_open": "08:30",
            "tw_close": "11:00",
            "restricted_vehicle_types": [],
        },
    ],
}


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        # app_core.py の _require_api_key() は X-API-Key と
        # Authorization: Bearer <key> の両方に対応しているが、ここでは
        # X-API-Key を使う
        headers["X-API-Key"] = API_KEY
    return headers


def fetch_scenarios(domain: str = DOMAIN) -> list[dict]:
    """GET /dsl_repository/scenarios を呼び、指定ドメインのシナリオだけを返す。

    ここで取得する内容（シナリオ名等）は接続先サーバーに登録されている
    ものがそのまま返るため、社内の実データが含まれている場合がある。
    本サンプルでは一覧の存在を示すだけに使い、中身（dsl_json）は
    /baseline の実行には使用しない（実行には SAMPLE_DSL を使う）。
    """
    url = f"{BASE_URL}/dsl_repository/scenarios"
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"シナリオ一覧の取得に失敗しました: {body}")
    return [s for s in body["scenarios"] if s.get("domain") == domain]


def run_baseline(dsl: dict) -> dict:
    """POST /baseline を呼び、業務DSLに対する最適化結果を返す。"""
    url = f"{BASE_URL}/baseline"
    payload = {"dsl": dsl, "issueActions": {}}
    resp = requests.post(url, headers=_headers(), json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def describe_interface(value: Any, path: str = "(root)", max_items: int = 1, _depth: int = 0) -> None:
    """レスポンスJSONを再帰的に辿り、キー名・型・配列要素数を一覧表示する。

    マニュアル（セクション10）は ui_dsl が「KPI・ルート・割り当て結果・
    イシュー情報を構造化されたJSONとして提供する」とだけ説明しており、
    フィールド単位のスキーマまでは書かれていない。自社フロントエンドを
    組む際にどのキーを参照すればよいかを、実際のレスポンスから
    このユーティリティで確認する。

    Args:
        value: 表示対象（dict / list / それ以外のJSON値）
        path: 現在地を表すドット/添字表記のパス文字列
        max_items: list の場合に何件目まで中身を展開するか（デフォルト1件目のみ）
        _depth: 再帰の深さ（インデント用、外から指定しない）
    """
    indent = "  " * _depth
    if isinstance(value, dict):
        print(f"{indent}{path}: dict (keys: {', '.join(value.keys())})")
        for key, child in value.items():
            describe_interface(child, f"{path}.{key}", max_items, _depth + 1)
    elif isinstance(value, list):
        note = " (empty)" if not value else ""
        print(f"{indent}{path}: list[{len(value)}]{note}")
        for item in value[:max_items]:
            describe_interface(item, f"{path}[0]", max_items, _depth + 1)
    else:
        preview = repr(value)
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"{indent}{path}: {type(value).__name__} = {preview}")


def main() -> None:
    print(f"[1] （参考）登録済みシナリオ一覧 (GET /dsl_repository/scenarios, domain={DOMAIN}) ...")
    try:
        scenarios = fetch_scenarios()
        if scenarios:
            for s in scenarios:
                print(f"  - id={s['id']}  name={s['name']}")
        else:
            print(f"  ドメイン '{DOMAIN}' の登録済みシナリオはありません（本サンプルの実行には影響しません）。")
    except requests.exceptions.ConnectionError:
        print(f"  接続できませんでした: {BASE_URL}")
        print("  OptiBuddy Backend (Backend/app.py) が起動しているか確認してください。")
        sys.exit(1)

    print("\n[2] 最適化実行 (POST /baseline, 本サンプル内蔵のダミーDSLを使用) ...")
    try:
        result = run_baseline(SAMPLE_DSL)
    except requests.exceptions.ConnectionError:
        print(f"  接続できませんでした: {BASE_URL}")
        sys.exit(1)
    print(f"  status = {result.get('status')!r}")

    print("\n[3] レスポンスの構造を確認 (describe_interface) ...")
    describe_interface(result)

    print("\n[4] サマリ")
    issues = result.get("issues", [])
    print(f"  issues件数: {len(issues)}")
    ui_dsl = result.get("ui_dsl") or {}
    if isinstance(ui_dsl, dict) and "kpi" in ui_dsl:
        print("  kpi:")
        print(json.dumps(ui_dsl["kpi"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
