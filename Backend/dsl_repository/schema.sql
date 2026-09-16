-- OptiBuddy DSL Repository Schema (V7)
-- SQLite3 + JSON1 Extension

-- DSL定義テーブル
CREATE TABLE IF NOT EXISTS dsl_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_class TEXT NOT NULL,
    version TEXT NOT NULL,
    extensions JSON NOT NULL,
    schema_json JSON NOT NULL,
    description TEXT,
    ask_system_prompt TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Extensionテーブル
-- 2026-07-27追記: category は「種類ラベル」（constraint/resource/ui等）専用とし、
-- どのドメインに適用可能かは applicable_domains（JSON配列、ドメイン名のリスト）で
-- 判定する。以前はcategoryにドメイン名を入れる例（NurseShiftWeeklyCap専用extension）と
-- 種類ラベルを入れる例（physical_space等）が混在し、category=base_domainで検索する
-- detect_extension_gaps()側が種類ラベル式の行を一切拾えないという不整合があった
-- （Koshoshiとの会話で発覚）。新設のapplicable_domainsを正とする。
CREATE TABLE IF NOT EXISTS extensions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    applicable_domains JSON,
    description TEXT,
    schema_fragment JSON,
    solver_mapping JSON,
    ui_mapping JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- DSL進化履歴テーブル (V7再設計)
-- 業務DSLの変化とビジネス文脈の対応を記録する
CREATE TABLE IF NOT EXISTS dsl_evolution_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 何が変わったか
    dsl_id          INTEGER REFERENCES dsl_definitions(id),
    extension_id    INTEGER REFERENCES extensions(id),  -- Extension変更の場合
    change_type     TEXT NOT NULL,
    -- "dsl_patch_applied"  : DSLパッチ適用
    -- "extension_added"    : Extension追加
    -- "extension_derived"  : 横展開でExtensionを流用
    -- "ask_to_extension"   : 質問からExtension生成
    -- "issue_resolved"     : Issue対処の結果DSL変更
    -- "initialization"     : 初期登録

    -- なぜ変わったか（ビジネス文脈）
    business_context TEXT,  -- 「船社からクレームがあった」「コスト削減のため」等
    trigger_type    TEXT,
    -- "user_question"  : /askからの質問
    -- "issue_fix"      : IssueのFIX/ACCEPT
    -- "ai_suggestion"  : AI Suggestionsからの適用
    -- "manual"         : 手動操作
    -- "initialization" : 初期登録

    -- 何の結果になったか
    before_summary  TEXT,   -- 変更前の状態（自然言語）
    after_summary   TEXT,   -- 変更後の状態（自然言語）
    effect_summary  TEXT,   -- 効果「makespanが20%改善」等
    dsl_patch       JSON,   -- 実際のJSONパッチ差分

    -- 横展開の記録
    source_domain   TEXT,   -- 流用元ドメイン「yard_planning」
    target_domain   TEXT,   -- 流用先ドメイン「event_staff」

    -- LLM文脈（再利用のため）
    llm_question    TEXT,   -- ユーザーの質問原文
    llm_answer      TEXT,   -- LLMの回答原文

    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by      TEXT DEFAULT 'system'  -- "user" / "ai" / "system"
);

-- 問題インスタンステーブル
CREATE TABLE IF NOT EXISTS problem_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dsl_id INTEGER REFERENCES dsl_definitions(id),
    instance_name TEXT,
    instance_data JSON NOT NULL,
    metadata JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- DSLマイグレーションルールテーブル
CREATE TABLE IF NOT EXISTS migration_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_version TEXT NOT NULL,
    to_version TEXT NOT NULL,
    problem_class TEXT NOT NULL,
    transformation_rules JSON NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(problem_class, from_version, to_version)
);

-- インデックス
CREATE INDEX IF NOT EXISTS idx_dsl_problem_class ON dsl_definitions(problem_class);
CREATE INDEX IF NOT EXISTS idx_dsl_version ON dsl_definitions(version);
CREATE INDEX IF NOT EXISTS idx_extension_name ON extensions(name);
CREATE INDEX IF NOT EXISTS idx_extension_category ON extensions(category);
CREATE INDEX IF NOT EXISTS idx_evolution_dsl_id ON dsl_evolution_log(dsl_id);
CREATE INDEX IF NOT EXISTS idx_evolution_trigger ON dsl_evolution_log(trigger_type);
CREATE INDEX IF NOT EXISTS idx_evolution_change ON dsl_evolution_log(change_type);
CREATE INDEX IF NOT EXISTS idx_instance_dsl_id ON problem_instances(dsl_id);
CREATE INDEX IF NOT EXISTS idx_migration_problem_class ON migration_rules(problem_class);

-- シナリオテーブル（HomeScreen用プリセット管理）
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    tag TEXT DEFAULT 'CUSTOM',
    tag_color TEXT DEFAULT '#00e5ff',
    domain TEXT,  -- 'yard' or 'staffing'
    dsl_json JSON NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scenarios_domain ON scenarios(domain);
CREATE INDEX IF NOT EXISTS idx_scenarios_active ON scenarios(is_active);

-- 業務登録ジョブテーブル（V8.7: app.pyの _DOMAIN_JOBS メモリdictの永続化置き換え）
-- job本体はJSONのままdataカラムに丸ごと保存する（stage/questions/pending/result等、
-- 呼び出し側のフィールド構成が変わってもスキーマ変更不要にするため）。
-- バックエンド再起動やタブを閉じてもジョブの状態（needs_confirmation待ちのpending含む）が
-- 失われないようにするのが目的。
CREATE TABLE IF NOT EXISTS domain_jobs (
    job_id TEXT PRIMARY KEY,
    data JSON NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_domain_jobs_updated_at ON domain_jobs(updated_at);

-- ドメイン登録処理の工程別所要時間ログ（2026-08-03追加、Koshoshi依頼:
-- 登録処理の時間短縮を検討する前提として、まず実測の基礎データが必要）。
-- domain_jobsはTTL経過後にGCされる運用（_DOMAIN_JOB_TTL_SEC=1時間）だが、
-- 基礎データは長期に蓄積して集計したいため、job本体とは別のこのテーブルに
-- done/error到達時のstage_timelineスナップショットを恒久保存する。
-- GC対象外（job_gcはdomain_jobsのみ削除し、このテーブルには触れない）。
CREATE TABLE IF NOT EXISTS domain_job_timing_log (
    job_id       TEXT PRIMARY KEY,
    domain_name  TEXT,
    match_type   TEXT,
    final_stage  TEXT NOT NULL,       -- 'done' | 'error'
    created_at   REAL,
    finished_at  REAL NOT NULL,
    stage_timeline JSON NOT NULL,     -- [{"stage": ..., "ts": ...}, ...]
    logged_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_domain_job_timing_log_logged_at
    ON domain_job_timing_log(logged_at);

-- ビュー: DSL定義とextension数の概要
CREATE VIEW IF NOT EXISTS v_dsl_overview AS
SELECT
    d.id,
    d.problem_class,
    d.version,
    json_array_length(d.extensions) as extension_count,
    d.extensions,
    d.description,
    d.created_at
FROM dsl_definitions d;
