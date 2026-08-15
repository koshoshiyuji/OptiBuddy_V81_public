"""
OptiBuddy DSL Repository
SQLite3 + JSON1拡張を使用したDSL永続化・バージョン管理・マイグレーション
"""

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class DslRepository:
    """DSL定義・Extension・進化履歴を管理するリポジトリ"""

    def __init__(self, db_path: str = None):
        """
        Args:
            db_path: SQLite3データベースファイルパス（Noneの場合はデフォルト）
        """
        if db_path is None:
            db_path = Path(__file__).parent / "optibuddy.db"
        
        self.db_path = str(db_path)
        self._init_database()

    def _init_database(self):
        """データベース初期化（スキーマ適用）"""
        schema_path = Path(__file__).parent / "schema.sql"

        with sqlite3.connect(self.db_path) as conn:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            conn.executescript(schema_sql)
            conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        """DB接続取得（JSON1拡張有効化）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 辞書形式で結果取得
        return conn

    # ========================================
    # DSL定義 CRUD
    # ========================================

    def create_dsl_definition(
        self,
        problem_class: str,
        version: str,
        extensions: List[str],
        schema_json: Dict[str, Any],
        description: str = None,
    ) -> int:
        """
        DSL定義を作成
        
        Returns:
            作成されたDSL定義のID
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO dsl_definitions 
                (problem_class, version, extensions, schema_json, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    problem_class,
                    version,
                    json.dumps(extensions),
                    json.dumps(schema_json),
                    description,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_dsl_definition(
        self, problem_class: str, version: str
    ) -> Optional[Dict[str, Any]]:
        """DSL定義を取得"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, problem_class, version, extensions, schema_json, 
                       description, created_at, updated_at
                FROM dsl_definitions
                WHERE problem_class = ? AND version = ?
                """,
                (problem_class, version),
            )
            row = cursor.fetchone()
            
            if row is None:
                return None
            
            return {
                "id": row["id"],
                "problem_class": row["problem_class"],
                "version": row["version"],
                "extensions": json.loads(row["extensions"]),
                "schema_json": json.loads(row["schema_json"]),
                "description": row["description"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_dsl_definitions(
        self, problem_class: str = None
    ) -> List[Dict[str, Any]]:
        """DSL定義一覧を取得（schema_jsonを含む）"""
        with self._get_connection() as conn:
            if problem_class:
                cursor = conn.execute(
                    """
                    SELECT id, problem_class, version, extensions, schema_json, description,
                           created_at, updated_at
                    FROM dsl_definitions
                    WHERE problem_class = ?
                    ORDER BY version DESC
                    """,
                    (problem_class,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, problem_class, version, extensions, schema_json, description,
                           created_at, updated_at
                    FROM dsl_definitions
                    ORDER BY problem_class, version DESC
                    """
                )
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "problem_class": row["problem_class"],
                    "version": row["version"],
                    "extensions": json.loads(row["extensions"]),
                    "schema_json": json.loads(row["schema_json"]),
                    "description": row["description"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            
            return results

    def get_latest_dsl_version(self, problem_class: str) -> Optional[str]:
        """指定されたproblem_classの最新バージョンを取得"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT version FROM dsl_definitions
                WHERE problem_class = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (problem_class,),
            )
            row = cursor.fetchone()
            return row["version"] if row else None

    def update_dsl_definition_schema(
        self, problem_class: str, version: str, schema_json: Dict[str, Any]
    ) -> bool:
        """
        既存のDSL定義のschema_jsonだけを更新する。

        create_dsl_definition()は新規作成専用でupdateパスが存在せず、
        何らかの理由（sample_dsl_json未渡し等）でschema_jsonが空（{}）のまま
        作成された行が、以後永久に更新されないという不具合が
        HospitalShiftPlannerで実際に発生したことを受け追加した。

        Returns:
            更新できた場合True、該当行が無い場合False
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE dsl_definitions
                SET schema_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE problem_class = ? AND version = ?
                """,
                (json.dumps(schema_json), problem_class, version),
            )
            conn.commit()
            return cursor.rowcount > 0

    # ========================================
    # Extension CRUD
    # ========================================

    def create_extension(
        self,
        name: str,
        category: str,
        schema_fragment: Dict[str, Any],
        solver_mapping: Dict[str, Any] = None,
        ui_mapping: Dict[str, Any] = None,
        description: str = None,
        applicable_domains: List[str] = None,
    ) -> int:
        """
        Extension定義を作成

        Args:
            category: 種類ラベル（例: "constraint"/"resource"/"ui"）。
                2026-07-27以降、ドメイン名を入れないこと（適用対象ドメインは
                applicable_domainsで指定する。旧設計ではcategoryにドメイン名を
                入れる例があったが、detect_extension_gaps()の検索と噛み合わない
                不整合の原因だったため廃止した）。
            applicable_domains: このextensionを適用可能なドメイン名
                （problem_class、例: ["YardPlanning"]）のリスト。
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO extensions
                (name, category, applicable_domains, schema_fragment, solver_mapping, ui_mapping, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    category,
                    json.dumps(applicable_domains) if applicable_domains is not None else json.dumps([]),
                    json.dumps(schema_fragment),
                    json.dumps(solver_mapping) if solver_mapping else None,
                    json.dumps(ui_mapping) if ui_mapping else None,
                    description,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_extension(self, name: str) -> Optional[Dict[str, Any]]:
        """Extension定義を取得"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, name, category, applicable_domains, schema_fragment, solver_mapping,
                       ui_mapping, description, created_at, updated_at
                FROM extensions
                WHERE name = ?
                """,
                (name,),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            return {
                "id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "applicable_domains": json.loads(row["applicable_domains"]) if row["applicable_domains"] else [],
                "schema_fragment": json.loads(row["schema_fragment"]),
                "solver_mapping": json.loads(row["solver_mapping"]) if row["solver_mapping"] else None,
                "ui_mapping": json.loads(row["ui_mapping"]) if row["ui_mapping"] else None,
                "description": row["description"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_extensions(self, category: str = None, applicable_domain: str = None) -> List[Dict[str, Any]]:
        """
        Extension一覧を取得

        Args:
            category: 種類ラベル（"constraint"等）での絞り込み（省略可）。
            applicable_domain: このドメイン名（problem_class）が
                applicable_domainsに含まれるextensionのみに絞り込む（省略可）。
                2026-07-27追加。detect_extension_gaps()はこちらを使うこと
                （旧: category=base_domainで代用していたが、categoryは
                種類ラベル専用のため意味的に誤りだった）。
        """
        with self._get_connection() as conn:
            if category:
                cursor = conn.execute(
                    """
                    SELECT id, name, category, applicable_domains, description, created_at, updated_at
                    FROM extensions
                    WHERE category = ?
                    ORDER BY name
                    """,
                    (category,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, name, category, applicable_domains, description, created_at, updated_at
                    FROM extensions
                    ORDER BY category, name
                    """
                )

            results = []
            for row in cursor.fetchall():
                applicable = json.loads(row["applicable_domains"]) if row["applicable_domains"] else []
                if applicable_domain is not None and applicable_domain not in applicable:
                    continue
                results.append({
                    "id": row["id"],
                    "name": row["name"],
                    "category": row["category"],
                    "applicable_domains": applicable,
                    "description": row["description"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })

            return results

    # ========================================
    # 業務登録ジョブ（app.pyの _DOMAIN_JOBS 永続化置き換え）
    # ========================================
    #
    # 以前は app.py が dict（_DOMAIN_JOBS）にジョブ状態を持っていたため、
    # バックエンド再起動やタブを閉じると needs_confirmation 待ちの pending が
    # 消えてしまい、「入力中〜確認待ちの間が1プロセスの一部のように振る舞う」
    # 問題があった。呼び出し側インターフェース（job_set(job_id, **fields) /
    # job_get(job_id) -> dict|None）は _DOMAIN_JOBS 時代と完全互換にしてあり、
    # app.py 側のロジック変更は不要（保存先の器を差し替えただけ）。

    def job_set(self, job_id: str, **fields: Any) -> None:
        """ジョブ状態にfieldsをマージ保存する（dict.update相当）。

        2026-08-03追加（ドメイン登録処理の短縮に向けた基礎データ収集、Koshoshi依頼）:
        stageフィールドが変化するたびに job["stage_timeline"] に
        {"stage": <新stage>, "ts": <epoch秒>} を自動で積む。呼び出し側
        （app.py の _run_hearing_pipeline/_advance_after_agent_round等）は
        既存のstage文字列（interpreting/stage1a_classifying/
        stage1a4_axis_a_fit_check/stage1a5_extension_gap_check/
        stage1b_scenario_gen/needs_confirmation/generating/verifying/
        applying/done/error/waiting_for_agent_question等）をそのまま
        job_set(job_id, stage=...) で渡しているだけなので、app.py・
        domain_generator.py側の変更は一切不要。
        隣接する2エントリの ts の差分が、その工程（前者のstage）に
        実際にかかった壁時計時間になる。新規ジョブの初回job_setで
        created_atも記録し、created_at→最初のtimelineエントリの間の
        遅延（スレッド起動〜最初のstage報告まで）も遡って追えるようにする。
        過去（この変更より前）に作成されたジョブにはtimelineが無いため、
        遡及計測はできない（TTL 1時間でGCされる運用上、実害は小さい）。
        """
        now = time.time()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT data FROM domain_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            job = json.loads(row["data"]) if row else {}
            prev_stage = job.get("stage")
            job.update(fields)
            if row is None:
                job["created_at"] = now
            new_stage = fields.get("stage")
            if new_stage is not None and new_stage != prev_stage:
                job.setdefault("stage_timeline", []).append({"stage": new_stage, "ts": now})
            job["updated_at"] = now
            conn.execute(
                """
                INSERT INTO domain_jobs (job_id, data, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                """,
                (job_id, json.dumps(job, ensure_ascii=False), now),
            )
            # 2026-08-03追加: done/errorに到達したら、domain_jobsがGCされても
            # 基礎データが失われないよう domain_job_timing_log に恒久保存する。
            # 同じjobが複数回done/errorへ遷移することは想定していないが、念のため
            # job_id主キーでUPSERTし、最終到達時点のtimelineで上書きする。
            if new_stage in ("done", "error") and job.get("stage_timeline"):
                conn.execute(
                    """
                    INSERT INTO domain_job_timing_log
                        (job_id, domain_name, match_type, final_stage, created_at,
                         finished_at, stage_timeline, logged_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        domain_name    = excluded.domain_name,
                        match_type     = excluded.match_type,
                        final_stage    = excluded.final_stage,
                        finished_at    = excluded.finished_at,
                        stage_timeline = excluded.stage_timeline,
                        logged_at      = excluded.logged_at
                    """,
                    (
                        job_id,
                        job.get("domain_name"),
                        job.get("match_type"),
                        new_stage,
                        job.get("created_at"),
                        now,
                        json.dumps(job["stage_timeline"], ensure_ascii=False),
                        now,
                    ),
                )
            conn.commit()

    def job_get(self, job_id: str) -> Optional[Dict[str, Any]]:
        """ジョブ状態を取得する。存在しなければNone。"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT data FROM domain_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return json.loads(row["data"]) if row else None

    def job_gc(self, ttl_sec: float) -> int:
        """updated_atがttl_secより古いジョブを削除する。削除件数を返す。"""
        cutoff = time.time() - ttl_sec
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM domain_jobs WHERE updated_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount

    # ========================================
    # DSL進化ログ
    # ========================================

    def log_dsl_evolution(
        self,
        dsl_id: int,
        change_type: str,
        # V7新フィールド
        trigger_type: str = "manual",
        business_context: str = None,
        before_summary: str = None,
        after_summary: str = None,
        effect_summary: str = None,
        dsl_patch: Dict[str, Any] = None,
        source_domain: str = None,
        target_domain: str = None,
        llm_question: str = None,
        llm_answer: str = None,
        extension_id: int = None,
        created_by: str = "system",
        # 後方互換（旧引数、内部で無視）
        change_description: str = None,
        diff_json: Dict[str, Any] = None,
        llm_context: str = None,
    ) -> int:
        """
        業務DSLの変化とビジネス文脈を記録する。

        change_type:
            "dsl_patch_applied"  : DSLパッチ適用
            "extension_added"    : Extension追加
            "extension_derived"  : 横展開でExtensionを流用
            "ask_to_extension"   : 質問からExtension生成
            "issue_resolved"     : Issue対処の結果DSL変更
            "initialization"     : 初期登録

        trigger_type:
            "user_question"  : /askからの質問
            "issue_fix"      : IssueのFIX/ACCEPT
            "ai_suggestion"  : AI Suggestionsからの適用
            "manual"         : 手動操作
            "initialization" : 初期登録
        """
        # 2026-07-20: dsl_evolution_logはQ&A回答内容や選択したプランなど、
        # ユーザーのアクティビティを記録しうる。開発環境では従来通り有効のままにし、
        # 顧客配布パッケージでは.env側で EVOLUTION_LOG_ENABLED=0 を設定することで
        # 無効化できるようにする（Koshoshiとの相談: 権利・セキュリティ上、顧客環境の
        # 活動履歴を自動収集しない方針。デバッグ時は都度ユーザーから提供いただく）。
        # 呼び出し元6箇所（app.py/domain_generator.py/decomposer.py/init_repository.py）は
        # すべてこのメソッド経由なので、ここ1箇所の変更で全体に効く。
        import os
        if os.environ.get("EVOLUTION_LOG_ENABLED", "1").strip() == "0":
            return -1

        # 後方互換: change_descriptionをbefore_summaryに流用
        if before_summary is None and change_description:
            before_summary = change_description
        # 後方互換: diff_jsonをdsl_patchに流用
        if dsl_patch is None and diff_json:
            dsl_patch = diff_json
        # 後方互換: llm_contextをllm_answerに流用
        if llm_answer is None and llm_context:
            llm_answer = llm_context

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO dsl_evolution_log (
                    dsl_id, extension_id, change_type, trigger_type,
                    business_context, before_summary, after_summary, effect_summary,
                    dsl_patch, source_domain, target_domain,
                    llm_question, llm_answer, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dsl_id,
                    extension_id,
                    change_type,
                    trigger_type,
                    business_context,
                    before_summary,
                    after_summary,
                    effect_summary,
                    json.dumps(dsl_patch) if dsl_patch else None,
                    source_domain,
                    target_domain,
                    llm_question,
                    llm_answer,
                    created_by,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_evolution_history(
        self, dsl_id: int = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        DSL進化履歴を取得。dsl_id=Noneで全件取得。
        """
        with self._get_connection() as conn:
            if dsl_id is not None:
                cursor = conn.execute(
                    """
                    SELECT
                        e.id, e.dsl_id, e.extension_id, e.change_type, e.trigger_type,
                        e.business_context, e.before_summary, e.after_summary, e.effect_summary,
                        e.dsl_patch, e.source_domain, e.target_domain,
                        e.llm_question, e.llm_answer, e.created_by, e.created_at,
                        d.problem_class, d.version,
                        ex.name as extension_name
                    FROM dsl_evolution_log e
                    LEFT JOIN dsl_definitions d ON e.dsl_id = d.id
                    LEFT JOIN extensions ex ON e.extension_id = ex.id
                    WHERE e.dsl_id = ?
                    ORDER BY e.created_at DESC
                    LIMIT ?
                    """,
                    (dsl_id, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT
                        e.id, e.dsl_id, e.extension_id, e.change_type, e.trigger_type,
                        e.business_context, e.before_summary, e.after_summary, e.effect_summary,
                        e.dsl_patch, e.source_domain, e.target_domain,
                        e.llm_question, e.llm_answer, e.created_by, e.created_at,
                        d.problem_class, d.version,
                        ex.name as extension_name
                    FROM dsl_evolution_log e
                    LEFT JOIN dsl_definitions d ON e.dsl_id = d.id
                    LEFT JOIN extensions ex ON e.extension_id = ex.id
                    ORDER BY e.created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

            results = []
            for row in cursor.fetchall():
                # dsl_name: "YardPlanning v1.0" 形式
                dsl_name = (
                    f"{row['problem_class']} v{row['version']}"
                    if row["problem_class"]
                    else f"DSL#{row['dsl_id']}"
                )
                results.append({
                    "id":               row["id"],
                    "dsl_id":           row["dsl_id"],
                    "dsl_name":         dsl_name,
                    "extension_name":   row["extension_name"],
                    "change_type":      row["change_type"],
                    "trigger_type":     row["trigger_type"],
                    "business_context": row["business_context"],
                    "before_summary":   row["before_summary"],
                    "after_summary":    row["after_summary"],
                    "effect_summary":   row["effect_summary"],
                    "dsl_patch":        json.loads(row["dsl_patch"]) if row["dsl_patch"] else None,
                    "source_domain":    row["source_domain"],
                    "target_domain":    row["target_domain"],
                    "llm_question":     row["llm_question"],
                    "llm_answer":       row["llm_answer"],
                    "created_by":       row["created_by"],
                    "created_at":       row["created_at"],
                })
            return results

    # ========================================
    # マイグレーション
    # ========================================

    def create_migration_rule(
        self,
        problem_class: str,
        from_version: str,
        to_version: str,
        transformation_rules: Dict[str, Any],
        description: str = None,
    ) -> int:
        """マイグレーションルールを作成"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO migration_rules 
                (problem_class, from_version, to_version, transformation_rules, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    problem_class,
                    from_version,
                    to_version,
                    json.dumps(transformation_rules),
                    description,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_migration_rule(
        self, problem_class: str, from_version: str, to_version: str
    ) -> Optional[Dict[str, Any]]:
        """マイグレーションルールを取得"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, transformation_rules, description, created_at
                FROM migration_rules
                WHERE problem_class = ? AND from_version = ? AND to_version = ?
                """,
                (problem_class, from_version, to_version),
            )
            row = cursor.fetchone()
            
            if row is None:
                return None
            
            return {
                "id": row["id"],
                "transformation_rules": json.loads(row["transformation_rules"]),
                "description": row["description"],
                "created_at": row["created_at"],
            }

    def migrate_dsl(
        self, instance_data: Dict[str, Any], from_version: str, to_version: str
    ) -> Dict[str, Any]:
        """
        DSLインスタンスをマイグレーション
        
        Args:
            instance_data: マイグレーション対象のDSLインスタンス
            from_version: 現在のバージョン
            to_version: 移行先バージョン
            
        Returns:
            マイグレーション後のDSLインスタンス
        """
        problem_class = instance_data.get("problem_class")
        if not problem_class:
            raise ValueError("instance_data に problem_class が必要です")
        
        migration_rule = self.get_migration_rule(problem_class, from_version, to_version)
        if not migration_rule:
            raise ValueError(
                f"マイグレーションルールが見つかりません: {problem_class} {from_version} -> {to_version}"
            )
        
        # 変換ルール適用（簡易実装）
        migrated_data = instance_data.copy()
        migrated_data["version"] = to_version
        
        transformation_rules = migration_rule["transformation_rules"]
        
        # フィールド名変更
        if "rename_fields" in transformation_rules:
            for old_name, new_name in transformation_rules["rename_fields"].items():
                if old_name in migrated_data:
                    migrated_data[new_name] = migrated_data.pop(old_name)
        
        # デフォルト値追加
        if "add_defaults" in transformation_rules:
            for field, default_value in transformation_rules["add_defaults"].items():
                if field not in migrated_data:
                    migrated_data[field] = default_value
        
        # フィールド削除
        if "remove_fields" in transformation_rules:
            for field in transformation_rules["remove_fields"]:
                migrated_data.pop(field, None)
        
        return migrated_data

    # ========================================
    # LLM統合用ヘルパー
    # ========================================

    def find_similar_dsl_patterns(
        self, problem_class: str, extensions: List[str], limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        類似のDSLパターンを検索（LLMコンテキスト生成用）
        
        Args:
            problem_class: 問題クラス
            extensions: 検索対象のextensions
            limit: 最大取得件数
            
        Returns:
            類似DSL定義のリスト
        """
        with self._get_connection() as conn:
            # JSON配列の要素マッチングを使用
            placeholders = " OR ".join(
                ["json_extract(extensions, '$') LIKE ?"] * len(extensions)
            )
            params = [f"%{ext}%" for ext in extensions] + [problem_class, limit]
            
            cursor = conn.execute(
                f"""
                SELECT id, problem_class, version, extensions, schema_json, description
                FROM dsl_definitions
                WHERE ({placeholders}) AND problem_class = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            )
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "problem_class": row["problem_class"],
                    "version": row["version"],
                    "extensions": json.loads(row["extensions"]),
                    "schema_json": json.loads(row["schema_json"]),
                    "description": row["description"],
                })
            
            return results

    def get_llm_context_for_dsl(
        self, problem_class: str, version: str
    ) -> str:
        """
        LLMに渡すコンテキストを生成
        
        Returns:
            DSL定義とextension情報を含むテキスト
        """
        dsl_def = self.get_dsl_definition(problem_class, version)
        if not dsl_def:
            return ""
        
        context_parts = [
            f"# DSL定義: {problem_class} v{version}",
            f"説明: {dsl_def.get('description', 'なし')}",
            f"\nExtensions: {', '.join(dsl_def['extensions'])}",
            "\n## スキーマ:",
            json.dumps(dsl_def["schema_json"], indent=2, ensure_ascii=False),
        ]
        
        # Extension詳細を追加
        if dsl_def["extensions"]:
            context_parts.append("\n## Extension詳細:")
            for ext_name in dsl_def["extensions"]:
                ext = self.get_extension(ext_name)
                if ext:
                    context_parts.append(f"\n### {ext_name}")
                    context_parts.append(f"カテゴリ: {ext['category']}")
                    context_parts.append(f"説明: {ext.get('description', 'なし')}")
                    context_parts.append("スキーマ追加:")
                    context_parts.append(
                        json.dumps(ext["schema_fragment"], indent=2, ensure_ascii=False)
                    )
        
        return "\n".join(context_parts)

    # ========================================
    # ユーティリティ
    # ========================================

    def get_overview(self) -> Dict[str, Any]:
        """リポジトリ全体の概要を取得"""
        with self._get_connection() as conn:
            # DSL定義数
            cursor = conn.execute("SELECT COUNT(*) as count FROM dsl_definitions")
            dsl_count = cursor.fetchone()["count"]
            
            # Extension数
            cursor = conn.execute("SELECT COUNT(*) as count FROM extensions")
            ext_count = cursor.fetchone()["count"]
            
            # 進化ログ数
            cursor = conn.execute("SELECT COUNT(*) as count FROM dsl_evolution_log")
            log_count = cursor.fetchone()["count"]
            
            # Problem class一覧
            cursor = conn.execute(
                "SELECT DISTINCT problem_class FROM dsl_definitions ORDER BY problem_class"
            )
            problem_classes = [row["problem_class"] for row in cursor.fetchall()]
            
            return {
                "dsl_definitions_count": dsl_count,
                "extensions_count": ext_count,
                "evolution_logs_count": log_count,
                "problem_classes": problem_classes,
            }

    # ========================================
    # シナリオ管理（HomeScreen用）
    # ========================================

    def create_scenario(
        self,
        name: str,
        dsl_json: Dict[str, Any],
        description: str = None,
        tag: str = "CUSTOM",
        tag_color: str = "#00e5ff",
        domain: str = None,
    ) -> int:
        """
        シナリオを作成

        Args:
            name: シナリオ名
            dsl_json: DSL JSON
            description: 説明
            tag: タグ（BASIC, COMPLEX, STAFFING等）
            tag_color: タグ色
            domain: ドメイン（'yard' or 'staffing'）

        Returns:
            作成されたシナリオのID
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scenarios
                (name, description, tag, tag_color, domain, dsl_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    description,
                    tag,
                    tag_color,
                    domain,
                    json.dumps(dsl_json),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_scenario(self, scenario_id: int) -> Optional[Dict[str, Any]]:
        """シナリオを取得"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, name, description, tag, tag_color, domain,
                       dsl_json, is_active, created_at, updated_at
                FROM scenarios
                WHERE id = ?
                """,
                (scenario_id,),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            return {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "tag": row["tag"],
                "tag_color": row["tag_color"],
                "domain": row["domain"],
                "dsl_json": json.loads(row["dsl_json"]),
                "is_active": row["is_active"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_scenarios(
        self, domain: str = None, is_active: bool = True
    ) -> List[Dict[str, Any]]:
        """
        シナリオ一覧を取得
        
        Args:
            domain: ドメインでフィルタ（Noneで全件）
            is_active: アクティブなシナリオのみ取得
        """
        with self._get_connection() as conn:
            query = """
                SELECT id, name, description, tag, tag_color, domain,
                       dsl_json, is_active, created_at, updated_at
                FROM scenarios
                WHERE 1=1
            """
            params = []

            if is_active:
                query += " AND is_active = 1"

            if domain:
                query += " AND domain = ?"
                params.append(domain)

            query += " ORDER BY created_at DESC"

            cursor = conn.execute(query, params)

            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"],
                    "tag": row["tag"],
                    "tag_color": row["tag_color"],
                    "domain": row["domain"],
                    "dsl_json": json.loads(row["dsl_json"]),
                    "is_active": row["is_active"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                })
            
            return results

    def update_scenario(
        self,
        scenario_id: int,
        name: str = None,
        description: str = None,
        tag: str = None,
        tag_color: str = None,
        domain: str = None,
        dsl_json: Dict[str, Any] = None,
        is_active: bool = None,
    ) -> bool:
        """シナリオを更新"""
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if tag is not None:
            updates.append("tag = ?")
            params.append(tag)
        if tag_color is not None:
            updates.append("tag_color = ?")
            params.append(tag_color)
        if domain is not None:
            updates.append("domain = ?")
            params.append(domain)
        if dsl_json is not None:
            updates.append("dsl_json = ?")
            params.append(json.dumps(dsl_json))
        if is_active is not None:
            updates.append("is_active = ?")
            params.append(1 if is_active else 0)

        if not updates:
            return False
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(scenario_id)
        
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE scenarios SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
            return True

    def delete_scenario(self, scenario_id: int) -> bool:
        """シナリオを削除（論理削除）"""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE scenarios SET is_active = 0 WHERE id = ?",
                (scenario_id,),
            )
            conn.commit()
            return True
