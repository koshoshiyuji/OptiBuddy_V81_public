# -*- coding: utf-8 -*-
"""
OptiBuddy Backend API サンプルクライアント（APIキー認証）
================================================================

Backend/app_core.py に実装されている簡易APIキー認証（OPTIBUDDY_API_KEY /
OPTIBUDDY_API_KEYS）の使い方を実演するサンプルです。認証機構そのものの
実装詳細（fail-closedの設計方針、hmac.compare_digestを使う理由など）は
app_core.py 内のコメント（2026-08-21追記、2026-08-21(2)追記）を参照して
ください。ここではクライアント側からの呼び出し方のみを示します。

実演内容
--------
  1. GET /health                       … 認証対象外のエンドポイント（常に通る）
  2. GET /dsl_repository/scenarios     … 認証対象のエンドポイントに対して
     (a) キーなしでアクセス
         → サーバーが OPTIBUDDY_API_KEY / OPTIBUDDY_API_KEYS を設定して
           いれば401、未設定（従来通り認証なし）なら200になる
     (b) X-API-Key ヘッダーでアクセス
     (c) Authorization: Bearer ヘッダーでアクセス

前提
----
- OptiBuddy Backend（Flask）が起動していること
  （`Backend/app.py` 実行、デフォルト http://localhost:5000）
- 認証を有効化して試す場合は、サーバー起動前に以下のいずれかを設定:
    export OPTIBUDDY_API_KEY=xxxx
    # または複数キー（呼び出し元ごとに名前を付けられる。ただし値が一致した
    # キーの名前がサーバー側ログに記録されるだけで、キーごとの権限差は
    # 一切ない — 全キーが同じ権限を持つ点に注意）:
    export OPTIBUDDY_API_KEYS='{"frontend": "xxxx", "batch": "yyyy"}'
- クライアント側（本サンプル実行時）は以下を設定:
    export OPTIBUDDY_BASE_URL=http://localhost:5000   # 省略時のデフォルトと同じ
    export OPTIBUDDY_API_KEY=xxxx                       # サーバー側と同じ値

実行方法・テスト手順
--------------------
    pip install requests

  (1) 認証なし（後方互換）の確認:
        OPTIBUDDY_API_KEY / OPTIBUDDY_API_KEYS を設定せずに
        `Backend/app.py` を起動してから、別ターミナルで:

            python api_key_auth_sample.py

        /health も /dsl_repository/scenarios（キーなし）も200になれば
        従来通り認証なしで動作している。

  (2) 単一キー認証の確認:
        export OPTIBUDDY_API_KEY=xxxx
        をセットしてから Backend/app.py を起動する。
        ※ 環境変数は app_core.py のモジュール読み込み時に一度だけ読まれる
        ため、起動中のプロセスに後から設定しても効かない。必ずサーバーを
        再起動すること。
        クライアント側も同じ値を OPTIBUDDY_API_KEY にセットして実行:

            export OPTIBUDDY_API_KEY=xxxx
            python api_key_auth_sample.py

        (a) キーなし → 401、(b) X-API-Key → 200、(c) Bearer → 200 になる
        ことを確認する。

  (3) 複数キー（OPTIBUDDY_API_KEYS）の確認:
        export OPTIBUDDY_API_KEYS='{"frontend": "xxxx", "batch": "yyyy"}'
        をセットしてサーバーを再起動する。クライアント側で
        OPTIBUDDY_API_KEY=xxxx を指定して実行し200になることに加え、
        Flaskサーバー側のログに `key_name=frontend` と出ているか確認する
        （yyyy側で実行した場合は `key_name=batch` になるはず）。これで
        「どの名前のキーで認証されたか」がログから追えることを確認できる。

  (4) 誤ったキーの確認:
        クライアント側の OPTIBUDDY_API_KEY をわざと間違った値にして実行し、
        401になることを確認する。

このサンプルが実演しない範囲（現在の実装にそもそも存在しない機能）
--------------------------------------------------------------
  - キーのローテーション・失効の仕組み（.env の値を変更してサーバーを
    再起動する以外の方法はない）
  - キーごとのエンドポイント権限分け・スコープ（前述の通り全キー同一権限）
  - レート制限
  これらが必要な場合は、リバースプロキシ等の前段で別途実装する必要が
  あります（OptiBuddy_Development_Guide.md 7-3節にも同様の注記あり）。

注意
----
本サンプルはクラウドサンドボックス環境では動作確認していません（Flask
サーバーを実際に起動して確認する必要があるため）。エンドポイント・
レスポンス形状はBackendのソースコード（app_core.py /
routes_dsl_repository.py）を確認した上で作成していますが、実機での
動作確認をお願いします。
"""
from __future__ import annotations

import os
import sys

import requests

BASE_URL = os.environ.get("OPTIBUDDY_BASE_URL", "http://localhost:5000").rstrip("/")
API_KEY = os.environ.get("OPTIBUDDY_API_KEY", "")

_SCENARIOS_URL = f"{BASE_URL}/dsl_repository/scenarios"


def check_health() -> None:
    """GET /health … 認証対象外のエンドポイント。キーの有無に関係なく通る。"""
    resp = requests.get(f"{BASE_URL}/health", timeout=10)
    print(f"  GET /health -> {resp.status_code}")


def try_without_key() -> None:
    """キーなしでアクセス。サーバーが認証を有効化していれば401が返る。"""
    resp = requests.get(_SCENARIOS_URL, timeout=10)
    print(f"  キーなし -> {resp.status_code}")
    if resp.status_code == 401:
        print(f"    body: {resp.json()}")


def try_with_x_api_key_header() -> None:
    """X-API-Key ヘッダーでアクセス。"""
    resp = requests.get(_SCENARIOS_URL, headers={"X-API-Key": API_KEY}, timeout=10)
    print(f"  X-API-Key ヘッダー -> {resp.status_code}")


def try_with_bearer_header() -> None:
    """Authorization: Bearer ヘッダーでアクセス。"""
    resp = requests.get(
        _SCENARIOS_URL, headers={"Authorization": f"Bearer {API_KEY}"}, timeout=10
    )
    print(f"  Authorization: Bearer ヘッダー -> {resp.status_code}")


def main() -> None:
    print("[1] GET /health（認証対象外）...")
    try:
        check_health()
    except requests.exceptions.ConnectionError:
        print(f"  接続できませんでした: {BASE_URL}")
        print("  OptiBuddy Backend (Backend/app.py) が起動しているか確認してください。")
        sys.exit(1)

    print("\n[2] GET /dsl_repository/scenarios（認証対象）...")
    print("  (a) キーなし:")
    try_without_key()

    if not API_KEY:
        print(
            "\n  OPTIBUDDY_API_KEY が未設定のため (b)(c) のキー付きアクセスは"
            "スキップします。\n"
            "  サーバー側で OPTIBUDDY_API_KEY / OPTIBUDDY_API_KEYS を設定して"
            "いる場合は、\n"
            "  同じ値を環境変数 OPTIBUDDY_API_KEY にセットして再実行してください。"
        )
        return

    print("  (b) X-API-Key ヘッダー:")
    try_with_x_api_key_header()

    print("  (c) Authorization: Bearer ヘッダー:")
    try_with_bearer_header()


if __name__ == "__main__":
    main()
