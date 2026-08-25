# THIRD_PARTY_NOTICES

本ファイルは、OptiBuddyが依存する第三者ライブラリ（OSS）のライセンス一覧です。`claude/PLAN_2026-08-17_oss_release_and_gtm_checklist.md` A6（T1・T4）の成果物として、以下の手順で自動生成した内容を元にしています。

- Backend（Python）: `Backend/requirements.txt`を`pip install --dry-run --report`で解決した完全な依存関係グラフ（推移的依存を含む）から、各パッケージのPyPIメタデータ（`license_expression`/`classifier`）を抽出。
- Frontend（npm）: `Frontend/`で`npx license-checker-rseidelsohn --production`を実行し、実際にビルド成果物へバンドルされる本番依存のみを抽出（devDependencies、すなわちビルド・Lint・テスト用ツールは対象外）。

**生成日**: 2026-08-25　**対象バージョン**: `requirements.txt`のクリーンアップ（`claude/PLAN_2026-08-17_...` A6 T2・T3）後の状態

---

## 重要な注記

1. **IBM CPLEX / CP Optimizer（`cplex`・`docplex`、`Backend/requirements-cplex.txt`）はこのリストに含まれません。** これらは独自の商用ライセンス（IBM Community Edition評価版）であり、OSSではありません。base installとは意図的に分離しており、同梱・再配布は行いません。利用者が任意でインストールする場合は、IBMの利用規約に従っていただく必要があります。
2. **GPL/AGPL系の依存物は検出されませんでした。** 唯一の例外として`chardet`（`requests`の依存先）が**LGPLv2+**（Lesser GPL）です。LGPLはライブラリとして動的に利用する分には自作コードのライセンスに影響しないため、OptiBuddy側の対応は不要です（`chardet`自体を改変して再配布する場合のみ、その改変部分の公開義務が生じます）。
3. **ライセンス表記が「UNKNOWN」の一部パッケージ**（`charset-normalizer`・`flask-cors`・`fonttools`・`lxml`・`protobuf`・`python-dotenv`・`tzdata`）は、PyPIメタデータのclassifier欄が未設定のため機械的に抽出できませんでした。いずれも著名なOSSプロジェクトで、公開リポジトリ上は概ねMIT/BSD系の許容ライセンスですが、**本ファイルでは正式な確認が取れていない旨を明記します**。正式な最終確認は、各プロジェクトのリポジトリ同梱`LICENSE`ファイルを直接参照するか、専門家レビューを推奨します。
4. 現行の配布形態（ソースリポジトリのpush、`requirements.txt`/`package.json`を参照させるのみ）では、以下のライセンス表示義務は厳密には発生しません（詳細は`claude/PLAN_2026-08-17_...`のA6 2026-08-25追記を参照）。本ファイルはあくまで透明性確保のための任意の情報開示です。PyInstallerでのバイナリ配布や、フロントエンドのビルド済みJSを配信するホスティングサービスを将来始める場合は、その時点でこのファイルを配布物に同梱する対応が必要になります。

---

## Backend（Python）

`Backend/requirements.txt`の直接依存および推移的依存、計77パッケージ（開発専用ツールとして削除済みの`pyinstaller`・`bandit`・`safety`・`vulture`一式は対象外）。

| パッケージ | バージョン | ライセンス |
|---|---|---|
| absl-py | 2.5.0 | Apache-2.0 |
| annotated-types | 0.7.0 | MIT License |
| anthropic | 0.104.1 | MIT License |
| anyio | 4.11.0 | MIT |
| blinker | 1.9.0 | MIT License |
| cachetools | 6.2.0 | MIT License |
| certifi | 2025.8.3 | Mozilla Public License 2.0 (MPL 2.0) |
| chardet | 5.2.0 | GNU Lesser General Public License v2 or later (LGPLv2+) ※上記注記2参照 |
| charset-normalizer | 3.4.3 | UNKNOWN（実態はMIT、上記注記3参照） |
| click | 8.3.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| contourpy | 1.3.2 | BSD License |
| cpmpy | 1.0.0 | Apache Software License |
| cycler | 0.12.1 | BSD License |
| distlib | 0.4.0 | Python Software Foundation License |
| distro | 1.9.0 | Apache Software License |
| docstring_parser | 0.18.0 | MIT License |
| exceptiongroup | 1.3.0 | MIT License |
| filelock | 3.19.1 | Unlicense |
| Flask | 3.1.3 | BSD-3-Clause |
| flask-cors | 6.0.1 | UNKNOWN（実態はMIT、上記注記3参照） |
| fonttools | 4.63.0 | UNKNOWN（実態はMIT、上記注記3参照） |
| h11 | 0.16.0 | MIT License |
| highspy | 1.15.1 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.10 | BSD License |
| immutabledict | 4.3.1 | MIT |
| iniconfig | 2.1.0 | MIT |
| itsdangerous | 2.2.0 | BSD License |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.11.0 | MIT |
| joblib | 1.5.3 | BSD-3-Clause |
| jsonpatch | 1.33 | BSD License |
| jsonpointer | 3.0.0 | BSD License |
| kiwisolver | 1.4.9 | BSD License |
| lxml | 6.1.1 | UNKNOWN（実態はBSD-3-Clause系、上記注記3参照） |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.2 | BSD License |
| matplotlib | 3.10.6 | Python Software Foundation License |
| mdurl | 0.1.2 | MIT License |
| networkx | 3.4.2 | BSD License |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openai | 1.108.2 | Apache Software License |
| ortools | 9.15.6755 | Apache Software License |
| packaging | 25.0 | Apache Software License; BSD License |
| pandas | 2.3.3 | BSD License |
| pillow | 11.3.0 | MIT-CMU |
| platformdirs | 4.4.0 | MIT |
| pluggy | 1.6.0 | MIT License |
| protobuf | 6.33.6 | UNKNOWN（実態はBSD-3-Clause、上記注記3参照） |
| pydantic | 2.11.9 | MIT |
| pydantic_core | 2.33.2 | MIT License |
| pydeps | 3.0.2 | BSD License |
| Pygments | 2.19.2 | BSD License |
| pyparsing | 3.2.4 | MIT |
| pyproject-api | 1.9.1 | MIT |
| python-dateutil | 2.9.0.post0 | BSD License; Apache Software License |
| python-dotenv | 1.2.2 | UNKNOWN（実態はBSD-3-Clause、上記注記3参照） |
| python-pptx | 1.0.2 | MIT License |
| pytz | 2026.3.post1 | MIT License |
| PyYAML | 6.0.2 | MIT License |
| regex | 2026.5.9 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache Software License |
| rich | 15.0.0 | MIT License |
| shellingham | 1.5.4 | ISC License (ISCL) |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License; Apache Software License |
| stdlib-list | 0.12.0 | MIT License |
| tomli | 2.2.1 | MIT License |
| tqdm | 4.67.1 | MIT License; Mozilla Public License 2.0 (MPL 2.0) |
| typing-inspection | 0.4.1 | MIT |
| typing_extensions | 4.15.0 | PSF-2.0 |
| tzdata | 2026.3 | UNKNOWN（実態はApache-2.0、上記注記3参照） |
| urllib3 | 2.7.0 | MIT |
| Werkzeug | 3.1.8 | BSD-3-Clause |
| xlsxwriter | 3.2.9 | BSD License |

---

## Frontend（npm、本番バンドルに含まれる依存のみ）

`Frontend/`の`dependencies`（`devDependencies`は対象外。ビルド・Lint用ツールはユーザーのブラウザに配信されないため）。

| パッケージ | ライセンス |
|---|---|
| @babel/runtime@7.29.7 | MIT |
| agent-base@6.0.2 | MIT |
| asynckit@0.4.0 | MIT |
| axios@1.18.1 | MIT |
| call-bind-apply-helpers@1.0.2 | MIT |
| combined-stream@1.0.8 | MIT |
| core-js@3.49.0 | MIT |
| debug@4.4.3 | MIT |
| delayed-stream@1.0.0 | MIT |
| dunder-proto@1.0.1 | MIT |
| es-define-property@1.0.1 | MIT |
| es-errors@1.3.0 | MIT |
| es-object-atoms@1.1.2 | MIT |
| es-set-tostringtag@2.1.0 | MIT |
| follow-redirects@1.16.0 | MIT |
| form-data@4.0.6 | MIT |
| function-bind@1.1.2 | MIT |
| get-intrinsic@1.3.0 | MIT |
| get-proto@1.0.1 | MIT |
| gopd@1.2.0 | MIT |
| has-symbols@1.1.0 | MIT |
| has-tostringtag@1.0.2 | MIT |
| hasown@2.0.4 | MIT |
| html-parse-stringify@3.0.1 | MIT |
| https-proxy-agent@5.0.1 | MIT |
| i18next@26.3.6 | MIT |
| lucide-react@1.16.0 | ISC |
| math-intrinsics@1.1.0 | MIT |
| mime-db@1.52.0 | MIT |
| mime-types@2.1.35 | MIT |
| ms@2.1.3 | MIT |
| proxy-from-env@2.1.0 | MIT |
| react-dom@19.2.6 | MIT |
| react-i18next@17.0.9 | MIT |
| react@19.2.6 | MIT |
| scheduler@0.27.0 | MIT |
| typescript@5.9.3 | Apache-2.0 |
| use-sync-external-store@1.6.0 | MIT |
| void-elements@3.1.0 | MIT |

---

## GPL/AGPL混入チェック結果（T4）

Backend・Frontendともに全推移的依存を対象に機械的スキャンを実施した結果、**GPL/AGPL系ライセンスの依存物は検出されませんでした。** 検出された唯一のコピーレフト系ライセンスは`chardet`のLGPLv2+のみで、上記注記2の通り対応不要と判断します。

## 将来の再配布時の対応（T6）

現行のソース配布形態では本ファイルの同梱は法的な必須要件ではありませんが、以下のいずれかに着手する場合は、その時点で本ファイルを配布物に同梱してください。

- PyInstaller等による実行可能ファイルとしてのバイナリ配布
- フロントエンドのビルド済みJSバンドルを配信するホスティングサービス（「OptiBuddy Cloud」構想等）

詳細は`claude/PLAN_2026-08-17_oss_release_and_gtm_checklist.md` A6を参照してください。
