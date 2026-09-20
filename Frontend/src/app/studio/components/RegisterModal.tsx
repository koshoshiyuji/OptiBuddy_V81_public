/**
 * RegisterModal.tsx  (V5.0)
 * - /api/domain/run が非同期ジョブ（job_id返却）になったのに合わせ、
 *   ポーリングで実際のステージ進捗（queued/stage1a/stage1b/generating/applying/done）を表示。
 *   以前の setTimeout による偽進捗表示を廃止。
 * - status="needs_confirmation" を受けて Q&A確認画面（confirmingフェーズ）を追加。
 * - job_id を sessionStorage に保存し、ポーリング中の fetch 失敗（接続断等）では
 *   エラーにせずリトライし続ける。モーダルを閉じて開き直しても、未完了の
 *   job_id があれば自動でポーリングを再開する（サーバーは接続に依存せず完走するため）。
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

const API_BASE = 'http://localhost:5000';
const POLL_INTERVAL_MS = 1400;
const JOB_STORAGE_KEY = 'optibuddy_domain_run_job';

type Phase = 'input' | 'running' | 'confirming' | 'agent_question' | 'done' | 'error' | 'cancelled';

// バックエンドの job.stage と 1:1 対応する進捗ステージ
// queued → stage1a_classifying → (stage1a4_axis_a_fit_check) → (stage1a5_extension_gap_check)
//        → (stage1b_scenario_gen | generating) → applying → done
//                                               └→ needs_confirmation
// stage1a4_axis_a_fit_check（2026-07-31追加）: match_type="existing_domain"の場合のみ、
// 選ばれたbase_domainの技術的前提（採用エンジン）がヒアリング内容に合っているかを
// 確認する専用LLM呼び出し（Backend/domain_generator.py check_axis_a_fit()）。
type JobStage =
  | 'queued' | 'interpreting' | 'stage1a_classifying' | 'stage1a4_axis_a_fit_check'
  | 'stage1a5_extension_gap_check'
  | 'stage1b_scenario_gen'
  | 'generating' | 'applying' | 'needs_confirmation' | 'done' | 'error'
  // /api/domain/confirm も非同期ジョブ化（V5.1）。confirm押下後はここから始まる。
  | 'queued_confirm' | 'generating_extensions' | 'registering_scenarios' | 'applying_files'
  // Gate2動的検証（V8.7）: 拡張適用パスにも静的・動的チェックを接続。
  | 'verifying'
  // 2026-07-16追加: デバッグエージェント（debug_agent）がdraftファイルを
  // 直接修正している間の段階。中断ボタンを表示する対象。
  | 'fixing'
  // 2026-07-17追加: エージェントがask_humanでその場に質問し、人間の回答待ちの段階。
  // Gate2の再検証はまだ行われていない＝1ラウンドの途中（新しいラウンドではない）。
  | 'waiting_for_agent_question'
  // 2026-09-19追加: 自動操縦（auto_resolve）が、解決できない指摘が残ったまま
  // 上限に達した場合等に自律的に登録を取りやめた最終状態。
  // Backend/routes_domain_registration.py _run_auto_pilot() のcancel分岐が設定する。
  | 'cancelled';

interface JobState {
  stage: JobStage;
  domain_name?: string;
  match_type?: string;
  base_domain?: string;
  classification?: any;
  questions?: string[];
  pending?: any;
  result?: any;
  error?: string;
  conflicts?: string[];
  // 2026-07-17追加: stage==='waiting_for_agent_question'の時、エージェントの質問文。
  pending_question?: string;
  // 2026-07-17追加: stage==='needs_confirmation'の時、trueなら1ラウンド
  // （ask_humanでの対話含む）+Gate2再検証を終えた後の最終ゲート。
  // 「続行」（新しいエージェントラウンド）は出さず、取りやめ／このまま登録する
  // の二択のみにする。
  loop_exhausted?: boolean;
  // 2026-08-10追加: force_apply=trueがGate2動的検証由来の指摘（下記
  // DYNAMIC_STRUCTURAL_PREFIXES参照）を理由にサーバー側で拒否された場合にtrue。
  // 通常はクライアント側でボタン自体を無効化するため到達しないが、念のための
  // サーバー側ガードの結果を表示するためのフィールド（防御的多層防御）。
  force_apply_blocked?: boolean;
  // 2026-09-19追加: 自動操縦モードのジョブかどうか（Backend側run()で保存）。
  auto_resolve?: boolean;
  // 2026-09-19追加: stage==='cancelled'の場合、自動操縦が見送りを判断した理由。
  auto_pilot_final_reasoning?: string;
}

// 2026-08-10追加（Koshoshi合意）: Gate2動的検証（実ソルブ）由来の指摘の接頭辞。
// Backend/app.py の _DYNAMIC_STRUCTURAL_PREFIXES と同じ内容を保つこと。
// memory.mdの「Gate2動的検証は非交渉」という原則により、この接頭辞を持つ指摘が
// 残っている間は「指摘を残したまま登録する」を無効化する（静的field-check由来の
// 指摘＝Big-M・absent値誤用・要件カバレッジ等は引き続きforce_apply可能）。
// 2026-08-28追記（Koshoshi合意）: 接頭辞の文言をプレーンな日本語に変更。
// Backend/app.py の _DYNAMIC_STRUCTURAL_PREFIXES と必ず同じ文字列に保つこと。
const DYNAMIC_STRUCTURAL_PREFIXES = [
  '（プログラムのエラーで停止・要修正）',
  '（実際に解いてみた結果が想定と違いました）',
];

function hasBlockingDynamicIssue(job: JobState | null | undefined): boolean {
  if (!job) return false;
  if (job.force_apply_blocked) return true;
  return (job.questions ?? []).some(
    q => typeof q === 'string' && DYNAMIC_STRUCTURAL_PREFIXES.some(p => q.startsWith(p))
  );
}

// 2026-08-28追記（Koshoshi合意）: 確認画面冒頭の説明文が「取りやめ／続行ボタンの
// 動き方」という定型文だけで、今回どのカテゴリの指摘が含まれているか・何を
// 確認すればよいかの手がかりが無かった（実機フィードバック）。
// Backend/domain_generator.py の blocking_questions 組み立てで前置している
// プレーンな日本語ラベルを接頭辞として使い、今回の指摘に含まれるカテゴリを
// 判定して、カテゴリ別の確認ポイント（i18n: categoryGuidance_*）を画面に出す。
// 接頭辞の文字列は Backend/domain_generator.py の該当箇所と同じに保つこと
// （DYNAMIC_STRUCTURAL_PREFIXESと違い、こちらは表示用の判定のみでforce_apply
// の可否には影響しない）。
const CATEGORY_GUIDANCE_PREFIXES: [string, string][] = [
  ['（プログラムのエラーで停止・要修正）', 'categoryGuidance_dynamicException'],
  ['（実際に解いてみた結果が想定と違いました）', 'categoryGuidance_dynamicMismatch'],
  ['（ヒアリング内容が未反映）', 'categoryGuidance_requiredGap'],
  ['（設定項目の反映漏れの疑い）', 'categoryGuidance_missingInDslForSolver'],
  ['（入力項目の反映漏れの疑い）', 'categoryGuidance_unusedInSolver'],
  ['（数値のざっくり近似に関する指摘）', 'categoryGuidance_bigM'],
  ['（特殊な条件の扱いに矛盾の疑い）', 'categoryGuidance_absentValue'],
  // 2026-09-01追加（Koshoshi合意）: MIPドメインでis_valid_solution()による
  // 自己検証が未実装の場合のTier1指摘（domain_generator.py側の
  // _check_mip_self_verification()と対になる）。
  ['（解の自己検証が未実装の疑い）', 'categoryGuidance_mipSelfCheck'],
];

function getPresentCategoryGuidanceKeys(job: JobState | null | undefined): string[] {
  if (!job) return [];
  const questions = job.questions ?? [];
  const keys: string[] = [];
  for (const [prefix, key] of CATEGORY_GUIDANCE_PREFIXES) {
    if (questions.some(q => typeof q === 'string' && q.startsWith(prefix)) && !keys.includes(key)) {
      keys.push(key);
    }
  }
  return keys;
}

interface RegisterModalProps {
  onClose: () => void;
}

// stage が切り替わるたびに1行追加するラベル。
// match_type が判明した時点で、その後の表示を実際の経路に合わせて切り替える
// （既存ドメインルートでは"Stage 2"が実際には走らないので、それを表示しない）。
function stageLabel(stage: JobStage, matchType: string | undefined, t: TFunction): string {
  switch (stage) {
    case 'queued':               return `🕒 ${t('registerModal.stageQueued')}`;
    case 'interpreting':
    case 'stage1a_classifying':  return `🔍 ${t('registerModal.stageClassifying')}`;
    case 'stage1a4_axis_a_fit_check': return `⚖️ ${t('registerModal.stageAxisAFitCheck')}`;
    case 'stage1a5_extension_gap_check': return `🔎 ${t('registerModal.stageGapCheck')}`;
    case 'stage1b_scenario_gen': return `📐 ${t('registerModal.stageScenarioGen')}`;
    case 'generating':           return `⚙️ ${t('registerModal.stageGenerating')}`;
    case 'applying':
      return matchType === 'new_domain'
        ? `💾 ${t('registerModal.stageWritingFiles')}`
        : `💾 ${t('registerModal.stageRegisteringScenarios')}`;
    case 'needs_confirmation':   return `❓ ${t('registerModal.stageNeedsConfirmation')}`;
    case 'queued_confirm':       return `🕒 ${t('registerModal.stageQueuedConfirm')}`;
    case 'generating_extensions': return `⚙️ ${t('registerModal.stageGeneratingExtensions')}`;
    case 'registering_scenarios': return `💾 ${t('registerModal.stageRegisteringScenarios')}`;
    case 'applying_files':       return `💾 ${t('registerModal.stageWritingFiles')}`;
    case 'verifying':            return `🧪 ${t('registerModal.stageVerifying')}`;
    case 'fixing':               return `🛠 ${t('registerModal.stageFixing')}`;
    case 'waiting_for_agent_question': return `💬 ${t('registerModal.stageAgentQuestion')}`;
    case 'done':                 return `✅ ${t('registerModal.stageDone')}`;
    case 'error':                return `❌ ${t('registerModal.stageError')}`;
    case 'cancelled':            return `🛑 ${t('registerModal.stageCancelled')}`;
    default:                     return String(stage);
  }
}

// 2026-07-16追加: runningフェーズの大見出しは、以前は常に「AIが業務ドメインを
// 生成中...」固定だったため、続行後のfixing/verifying段階（AIが再生成ではなく
// 指摘事項の修正・再検証をしている段階）でも同じ文言が出て混乱の原因になった
// （「待ってたらこんな画面になった？何？」）。stageに応じて見出しを変える。
function runningHeader(stage: JobStage | undefined, t: TFunction): string {
  switch (stage) {
    case 'fixing':    return t('registerModal.headerFixing');
    case 'verifying': return t('registerModal.headerVerifying');
    case 'queued_confirm':
    case 'generating_extensions':
    case 'registering_scenarios':
    case 'applying_files':
    case 'applying':  return t('registerModal.headerApplying');
    default:          return t('registerModal.generatingDomain');
  }
}

export function RegisterModal({ onClose }: RegisterModalProps) {
  const { t } = useTranslation();
  const [domainName, setDomainName]         = useState('');
  const [hearingText, setHearingText]       = useState('');
  const [attachedFiles, setAttachedFiles]   = useState<{ name: string; content: string }[]>([]);
  const [phase, setPhase]                   = useState<Phase>('input');
  const [statusLog, setStatusLog]           = useState<string[]>([]);
  const [job, setJob]                       = useState<JobState | null>(null);
  const [jobId, setJobId]                   = useState<string>('');
  const [result, setResult]                 = useState<any>(null);
  const [errorMsg, setErrorMsg]             = useState('');
  const [existsConflict, setExistsConflict] = useState<string[]>([]);
  const [promptNameWarning, setPromptNameWarning] = useState<string | null>(null);
  const [checkingName, setCheckingName]     = useState(false);
  const [reconnecting, setReconnecting]     = useState(false);
  const [confirming, setConfirming]         = useState(false);
  // 2026-09-07追加（Koshoshi合意）: 動的検証（実ソルブ）由来の指摘について、
  // 人間が実際に確認した上で「実装バグではない」と明示判断した場合に、
  // force_applyとは別枠で正式に登録を通すためのチェックボックス状態。
  // Backend/app.pyのdynamic_override＋理由必須（answers）に対応する。
  const [dynamicOverrideChecked, setDynamicOverrideChecked] = useState(false);
  const [interrupting, setInterrupting]     = useState(false);
  const [answerText, setAnswerText]         = useState('');
  const [copiedIdx, setCopiedIdx]             = useState<number | null>(null);
  const [expandedIdx, setExpandedIdx]         = useState<Set<number>>(new Set());
  const [agentAnswerText, setAgentAnswerText] = useState('');
  const [forceNewDomain, setForceNewDomain] = useState(false);
  const [autoResolve, setAutoResolve]       = useState(false);
  // 2026-07-18f追加: 登録完了後の「今すぐ直す」ジョブが実行中かどうか。
  const [startingFix, setStartingFix]       = useState(false);
  const fileInputRef                        = useRef<HTMLInputElement>(null);
  const checkTimerRef                       = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimerRef                        = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 2026-09-19追加（Koshoshi合意）: autopilot(auto_resolve)継続用のポーリング
  // タイマーが、それをセットした直後のphase変化（running→confirming等）に
  // よって誤ってclearTimeoutされてしまうバグの修正。詳細はポーリングuseEffect
  // 内のコメント参照。
  const suppressNextPollCleanupRef          = useRef(false);
  const lastStageRef                        = useRef<JobStage | null>(null);
  // 2026-07-18f追加: ポーリング中のjobが「登録完了後の任意修正」ラウンドかどうかを
  // 判定するフラグ。trueの場合、stage==='done'到達時にj.resultで既存resultを
  // まるごと置き換えず、gate2_warnings等だけをマージする（written/scenarios_registered
  // /match_typeを失わないため）。
  const isFixRoundRef                       = useRef(false);

  const checkDomainName = useCallback(async (name: string) => {
    if (!name.trim()) { setExistsConflict([]); setPromptNameWarning(null); return; }
    setCheckingName(true);
    try {
      const res  = await fetch(`${API_BASE}/api/domain/check/${encodeURIComponent(name)}`);
      const data = await res.json();
      setExistsConflict(data.exists ? data.conflicts : []);
      // 2026-09-19追加: ドメイン名が分類プロンプト内の過去事例名と一致する場合の
      // 注意喚起（Backend/domain_generator/stage1a.py check_domain_exists()参照）。
      // conflictsと違い登録はブロックしない。
      setPromptNameWarning(data.prompt_name_warning ?? null);
    } catch { setExistsConflict([]); setPromptNameWarning(null); }
    finally { setCheckingName(false); }
  }, []);

  useEffect(() => {
    if (checkTimerRef.current) clearTimeout(checkTimerRef.current);
    checkTimerRef.current = setTimeout(() => checkDomainName(domainName), 500);
    return () => { if (checkTimerRef.current) clearTimeout(checkTimerRef.current); };
  }, [domainName, checkDomainName]);

  // 前回のセッションで未完了の job があれば自動復帰（モーダルを閉じて開き直してもポーリングを継続）
  useEffect(() => {
    const saved = sessionStorage.getItem(JOB_STORAGE_KEY);
    if (saved) {
      try {
        const { job_id, domain_name } = JSON.parse(saved);
        if (job_id) {
          setJobId(job_id);
          setDomainName(domain_name ?? '');
          setPhase('running');
          setStatusLog([t('registerModal.resumedFromPreviousSession')]);
        }
      } catch { /* ignore */ }
    }
    return () => { if (pollTimerRef.current) clearTimeout(pollTimerRef.current); };
  }, []);

  // job_id が確定したらポーリング開始
  useEffect(() => {
    if (!jobId || (phase !== 'running')) return;

    let cancelled = false;

    const poll = async () => {
      if (cancelled) return;
      try {
        const res  = await fetch(`${API_BASE}/api/domain/run/status/${jobId}`);
        if (res.status === 404) {
          setErrorMsg(t('registerModal.jobNotFound'));
          setPhase('error');
          sessionStorage.removeItem(JOB_STORAGE_KEY);
          return;
        }
        const data = await res.json();
        if (data.status !== 'ok') throw new Error(data.message ?? t('registerModal.statusFetchFailed'));
        setReconnecting(false);

        const j: JobState = data.job;
        setJob(j);

        if (j.stage !== lastStageRef.current) {
          lastStageRef.current = j.stage;
          setStatusLog(prev => [...prev, stageLabel(j.stage, j.match_type, t)]);
        }

        if (j.stage === 'needs_confirmation') {
          // 2026-07-16修正: 以前はここでsessionStorageを消していたため、
          // 確認画面表示中にモーダルが閉じてしまう（背景クリック等）と
          // 二度と再開できなくなるバグがあった（実機で発生）。doneかerrorに
          // 到達するまでは保持し続け、再度開いた時にこの画面へ復帰できるようにする。
          setPhase('confirming');
          // 2026-09-19追加: 自動操縦（auto_resolve）ジョブは、この画面で人間の
          // ボタン操作を待たず裏で進行し続けるため、通常モード（人間の操作待ち＝
          // ポーリング停止）と異なりポーリングを止めずに継続する。
          // 2026-09-19再修正（Koshoshi合意）: 上記の「継続する」意図は実際には
          // 機能していなかった。setPhase('confirming')によりphaseが変わり、この
          // useEffectが[jobId, phase]依存で再実行される際、Reactが直前の
          // cleanup（下のreturn関数）を呼ぶ。そのcleanupが「今まさにセットした
          // ばかりのこのタイマー」までclearTimeoutで消してしまい、以降ポーリングが
          // 完全に停止する（実機で確認: needs_confirmation到達後、バックエンドが
          // 後で正常にdone/cancelledへ到達しても画面が最初の確認画面のまま固まる）。
          // suppressNextPollCleanupRefを立てて、次に一度だけ走るcleanupに
          // 「このタイマーは消さないでほしい」と伝える。
          if (j.auto_resolve) {
            suppressNextPollCleanupRef.current = true;
            pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
          }
          return;
        }
        if (j.stage === 'waiting_for_agent_question') {
          // 2026-07-17追加: エージェントがその場で質問してきた状態。
          // needs_confirmationと同様、sessionStorageは保持したまま（再開可能に）。
          setPhase('agent_question');
          // 2026-09-19再修正（Koshoshi合意）: needs_confirmation分岐と同じ理由で
          // suppressNextPollCleanupRefが必要（詳細は上のコメント参照）。
          if (j.auto_resolve) {
            suppressNextPollCleanupRef.current = true;
            pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
          }
          return;
        }
        if (j.stage === 'cancelled') {
          // 2026-09-19追加: 自動操縦が解決できない指摘を理由に登録を見送った
          // 最終状態。done/errorと同様に終了状態として扱い、sessionStorageを解放する。
          sessionStorage.removeItem(JOB_STORAGE_KEY);
          setPhase('cancelled');
          return;
        }
        if (j.stage === 'done') {
          sessionStorage.removeItem(JOB_STORAGE_KEY);
          if (isFixRoundRef.current) {
            // 2026-07-18f追加: 「今すぐ直す」ジョブの完了。written/scenarios_registered/
            // match_type等、登録本体の結果は失わずgate2_warningsだけを差し替える。
            isFixRoundRef.current = false;
            setResult((prev: any) => ({
              ...prev,
              gate2_warnings: j.result?.gate2_warnings ?? [],
              post_fix_summary: j.result?.fixed_summary ?? '',
              post_fix_stopped_reason: j.result?.stopped_reason ?? '',
            }));
            setPhase('done');
            return;
          }
          setResult({ ...j.result, match_type: j.match_type, base_domain: j.base_domain, classification: j.classification });
          setPhase('done');
          return;
        }
        if (j.stage === 'error') {
          sessionStorage.removeItem(JOB_STORAGE_KEY);
          setErrorMsg(j.error ?? t('registerModal.unknownError'));
          setPhase('error');
          return;
        }

        pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
      } catch {
        // 接続断・ネットワークエラー: サーバー側のジョブは動き続けているので、
        // エラー画面には落とさずリトライを続ける（バックオフ気味に少し間隔を伸ばす）
        setReconnecting(true);
        pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS * 2);
      }
    };

    poll();
    return () => {
      // 2026-09-19追加（Koshoshi合意）: このタイマーがautopilot継続用として
      // 直前にセットされたばかりの場合（suppressNextPollCleanupRef）は、
      // ここでのclearTimeoutをスキップする。このcleanupは「phaseが
      // running以外に変わった」ことで走るが、autopilot継続の場合はまさに
      // そのphase変化（running→confirming/agent_question）自身が原因で
      // 呼ばれており、消してよいのは通常の（手動操作待ちで本当に止めるべき）
      // タイマーだけ。フラグは一度だけ使ったらリセットする。
      if (suppressNextPollCleanupRef.current) {
        suppressNextPollCleanupRef.current = false;
        return;
      }
      cancelled = true;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, [jobId, phase]);

  const handleFileAttach = (e: React.ChangeEvent<HTMLInputElement>) => {
    Array.from(e.target.files ?? []).forEach(file => {
      const reader = new FileReader();
      reader.onload = ev => {
        setAttachedFiles(prev => [...prev, { name: file.name, content: ev.target?.result as string }]);
      };
      reader.readAsText(file, 'utf-8');
    });
    e.target.value = '';
  };

  const handleRun = async () => {
    if (!domainName.trim() || existsConflict.length > 0) return;

    const hearingTexts: string[] = [];
    if (hearingText.trim()) hearingTexts.push(hearingText.trim());
    attachedFiles.forEach(f => hearingTexts.push(`## ${f.name}\n\n${f.content}`));
    if (hearingTexts.length === 0) {
      setErrorMsg(t('registerModal.needHearingTextOrFile'));
      setPhase('error');
      return;
    }

    lastStageRef.current = null;
    setStatusLog([]);
    setPhase('running');

    try {
      const res = await fetch(`${API_BASE}/api/domain/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain_name: domainName.trim(),
          hearing_texts: hearingTexts,
          force_new_domain: forceNewDomain,
          auto_resolve: autoResolve,
        }),
      });

      if (res.status === 409) {
        const data = await res.json();
        setErrorMsg(data.message ?? t('registerModal.domainNameConflict'));
        setPhase('error');
        return;
      }

      const data = await res.json();
      if (data.status !== 'started' || !data.job_id) {
        setErrorMsg(data.message ?? t('registerModal.unknownError'));
        setPhase('error');
        return;
      }

      sessionStorage.setItem(JOB_STORAGE_KEY, JSON.stringify({ job_id: data.job_id, domain_name: domainName.trim() }));
      setJobId(data.job_id);
    } catch (e: any) {
      setErrorMsg(e.message ?? t('registerModal.unknownError'));
      setPhase('error');
    }
  };

  // 2026-07-16: 以前はここに「📝 回答を反映してヒアリングをやり直す」という、
  // クライアント側だけで入力画面に戻す別ルート（handleReviseWithAnswer）もあった。
  // /api/domain/confirm を経由する下記 handleConfirm と役割が重複し、
  // どちらを押すべきか分かりにくかったため1本化した。
  //
  // 2026-07-16 再設計: 「続行」を押しても、hearing文に回答を追記してAIの
  // 生成をまるごとやり直すわけではない。指摘事項はAIエージェントが実際の生成物
  // （サーバー側にdraftとして残っているファイル）を直接直しにいく。「続行」は
  // その結果をGate2だけで再検証し、まだ指摘が残っていれば新しい指摘付きで
  // この画面に戻ってくる（同じjob_idのままラウンドが進む）。指摘が0件になった
  // 時点で正式に登録が完了する。
  //
  // 2026-07-17追加: エージェントが「業務上の判断が必要」と自己申告して止まる
  // 指摘（例:優先度の解釈、null値の扱い等）は、エージェント自身では決められない。
  // 回答欄に書いた内容は次のラウンドでエージェントへの追加指示として渡される。
  //
  // 2026-07-17追加（force_apply）: 静的チェック（converter⇔solverのキー突き合わせ等）
  // は機械的な判定のため、コードが変わらない限り同じ「誤検知」を毎回出し続け、
  // AIエージェントが正しく「問題ない」と説明しても収束しないケースがあった
  // （実機で発生：「結局ずっとこの画面に戻ってしまい終わらない」）。バックエンドには
  // 元々force_apply（再検証せず今の内容のまま登録する、人間が誤検知と判断した場合の
  // 脱出口）が用意されていたが、UIに出していなかったため使えなかった。
  const handleConfirm = async (forceApply: boolean = false, dynamicOverride: boolean = false) => {
    if (!job) return;
    setConfirming(true);
    try {
      const res = await fetch(`${API_BASE}/api/domain/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain_name: job.domain_name ?? domainName.trim(),
          pending: job.pending,
          questions: job.questions,
          answers: answerText,
          job_id: jobId,
          force_apply: forceApply,
          dynamic_override: dynamicOverride,
        }),
      });
      const data = await res.json();
      if (data.status !== 'started' || !data.job_id) {
        setErrorMsg(data.message ?? t('registerModal.confirmStartFailed'));
        setPhase('error');
        return;
      }
      setAnswerText('');
      // confirmもrunと同じ非同期ジョブ（job_id発行→ポーリング）に統一。
      // 既存のポーリングuseEffect（[jobId, phase]依存）をそのまま使い回す。
      lastStageRef.current = null;
      setStatusLog(prev => [...prev, `--- ${t('registerModal.resumingAfterConfirm')} ---`]);
      sessionStorage.setItem(JOB_STORAGE_KEY, JSON.stringify({ job_id: data.job_id, domain_name: job.domain_name ?? domainName.trim() }));
      setJobId(data.job_id);
      setPhase('running');
    } catch (e: any) {
      setErrorMsg(e.message ?? t('registerModal.confirmStartFailed'));
      setPhase('error');
    } finally {
      setConfirming(false);
    }
  };

  // 2026-07-17追加: エージェントがask_humanでその場に質問してきた時の回答送信。
  // /api/domain/confirm（新しいラウンドの開始）とは別エンドポイント
  // （/api/domain/agent_answer）を叩く。同じjob_idのまま、一時停止していた
  // エージェントのセッションの続きが実行される（新しいラウンドではない）。
  const handleAgentAnswer = async () => {
    if (!jobId) return;
    setConfirming(true);
    try {
      const res = await fetch(`${API_BASE}/api/domain/agent_answer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId, answer: agentAnswerText }),
      });
      const data = await res.json();
      if (data.status !== 'started') {
        setErrorMsg(data.message ?? t('registerModal.confirmStartFailed'));
        setPhase('error');
        return;
      }
      setAgentAnswerText('');
      lastStageRef.current = null;
      setStatusLog(prev => [...prev, `--- ${t('registerModal.resumingAfterAgentAnswer')} ---`]);
      setPhase('running');
    } catch (e: any) {
      setErrorMsg(e.message ?? t('registerModal.confirmStartFailed'));
      setPhase('error');
    } finally {
      setConfirming(false);
    }
  };

  // 2026-07-18f追加: 登録完了後、result.gate2_warningsがまだ残っている場合に
  // 「今すぐ直す」を選べるようにする任意の入口。「残りは誤検知と判断し、この
  // まま登録する」を選んだ後でも、後から実害がありそうだと気づいた場合の
  // 逃げ道として用意した（ユーザー要望:「登録後に残りバグの修正に希望なら
  // 入れるようにしなくていい？」）。/api/domain/confirmとは独立した別ジョブで、
  // pending/draftの概念が無く既に登録済みのライブファイルを直接直す。
  const handleFixRemaining = async () => {
    if (!result) return;
    setStartingFix(true);
    try {
      const res = await fetch(`${API_BASE}/api/domain/post_register_fix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          domain_name: result.domain_name,
          warnings: result.gate2_warnings ?? [],
        }),
      });
      const data = await res.json();
      if (data.status !== 'started' || !data.job_id) {
        setErrorMsg(data.message ?? t('registerModal.unknownError'));
        setPhase('error');
        return;
      }
      isFixRoundRef.current = true;
      lastStageRef.current = null;
      setStatusLog(prev => [...prev, `--- ${t('registerModal.resumingAfterFixRemaining')} ---`]);
      setJobId(data.job_id);
      setPhase('running');
    } catch (e: any) {
      setErrorMsg(e.message ?? t('registerModal.unknownError'));
      setPhase('error');
    } finally {
      setStartingFix(false);
    }
  };

  // 2026-07-16追加: 「取りやめ」時、サーバー側にGate2再検証用のdraftファイル
  // （solver.py/converter.py/シナリオJSON）が残ったままだと、DB未登録・
  // 未適用のコードだけが残る「幽霊状態」になる（実機で実際に起きた事故と同種）。
  // 閉じる前にバックエンドへ後片付けを依頼する。
  const handleCancel = async () => {
    if (job?.pending?.written_paths?.length) {
      try {
        await fetch(`${API_BASE}/api/domain/cancel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pending: job.pending }),
        });
      } catch {
        // 片付け失敗はここでは無視する（閉じる操作自体は妨げない）
      }
    }
    // needs_confirmation中もsessionStorageを保持するように変更したため
    // （閉じても再開できるように）、取りやめ時は明示的に消す。消さないと
    // 次に開いた時、draftファイルが片付け済みで実体のないjobに再接続してしまう。
    sessionStorage.removeItem(JOB_STORAGE_KEY);
    onClose();
  };

  // 2026-07-16追加: デバッグエージェント（stage='fixing'）実行中に、人間が
  // 「今の状態でいったん止めたい」と判断した場合の中断ボタン。次のターンに
  // 入る前にバックエンド側で打ち切られる（進行中のLLM呼び出し自体は止まらない）。
  const handleInterrupt = async () => {
    if (!jobId) return;
    setInterrupting(true);
    try {
      await fetch(`${API_BASE}/api/domain/interrupt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId }),
      });
    } catch {
      // 送信失敗はここでは無視する（ポーリングは継続する）
    } finally {
      setInterrupting(false);
    }
  };

  const card: React.CSSProperties = {
    background: '#16161e', border: '0.5px solid #2a2a30',
    borderRadius: '12px', padding: '16px 20px', marginBottom: '12px',
  };
  const inp: React.CSSProperties = {
    width: '100%', fontSize: '13px', padding: '7px 10px',
    border: '0.5px solid #2a2a30', borderRadius: '8px',
    background: '#0a0a0c', color: '#eee', boxSizing: 'border-box',
  };
  const btnPrimary: React.CSSProperties = {
    padding: '9px 22px', borderRadius: '8px', fontSize: '13px', fontWeight: 700,
    cursor: 'pointer', border: '0.5px solid #0F6E56',
    background: '#1D9E75', color: '#E1F5EE',
  };
  const btnGhost: React.CSSProperties = {
    padding: '7px 14px', borderRadius: '8px', fontSize: '12px',
    cursor: 'pointer', border: '0.5px solid #2a2a30',
    background: 'transparent', color: '#888',
  };
  const lbl: React.CSSProperties = { fontSize: '12px', color: '#888', display: 'block', marginBottom: '5px' };

  const canRun = !!(domainName.trim() && (hearingText.trim() || attachedFiles.length > 0) &&
                    existsConflict.length === 0 && !checkingName);

  const matchTypeLabel = (mt: string) => ({
    existing_domain: t('registerModal.matchTypeExistingDomain'),
    base_problem:    t('registerModal.matchTypeBaseProblem'),
    new_domain:      t('registerModal.matchTypeNewDomain'),
  }[mt] ?? mt);

  const matchTypeColor = (mt: string) =>
    mt === 'new_domain' ? '#f97316' : '#5DCAA5';

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  zIndex: 1000, padding: '20px' }} onClick={onClose}>
      <div style={{ background: '#0a0a0c', borderRadius: '14px', maxWidth: '720px', width: '100%',
                    maxHeight: '92vh', overflowY: 'auto', padding: '28px' }}
           onClick={e => e.stopPropagation()}>

        {/* ヘッダー */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '22px' }}>
          <div>
            <h2 style={{ fontSize: '18px', fontWeight: 700, color: '#eee', margin: 0 }}>✨ {t('registerModal.title')}</h2>
            <p style={{ fontSize: '11px', color: '#555', margin: '4px 0 0' }}>
              {t('registerModal.subtitle')}
            </p>
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#555', fontSize: '22px', cursor: 'pointer' }}>×</button>
        </div>

        {/* ══ input ══ */}
        {phase === 'input' && (
          <>
            <div style={card}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <div style={{ fontSize: '13px', fontWeight: 600, color: '#eee' }}>📋 {t('registerModal.hearingInfo')}</div>
                <button onClick={() => window.open(`${API_BASE}/api/domain/template`, '_blank')} style={btnGhost}>
                  📥 {t('registerModal.downloadHearingSheet')}
                </button>
              </div>

              <div style={{ marginBottom: '14px' }}>
                <label style={lbl}>{t('registerModal.domainNameLabel')}<span style={{ color: '#ff5555' }}> *</span>
                  <span style={{ color: '#555', marginLeft: '6px' }}>{t('registerModal.domainNameExample')}</span>
                </label>
                <input type="text" value={domainName} onChange={e => setDomainName(e.target.value)}
                  placeholder="e.g. TruckDispatch"
                  style={{ ...inp, borderColor: existsConflict.length > 0 ? '#ff5555' : '#2a2a30' }} />
                {checkingName && <div style={{ fontSize: '10px', color: '#555', marginTop: '3px' }}>{t('registerModal.checking')}</div>}
                {existsConflict.length > 0 && (
                  <div style={{ marginTop: '6px', padding: '8px 12px', borderRadius: '6px',
                                background: '#2a1000', border: '0.5px solid #ff5555' }}>
                    <div style={{ fontSize: '11px', color: '#ff5555', fontWeight: 600 }}>⚠ {t('registerModal.nameAlreadyRegistered')}</div>
                    <div style={{ fontSize: '10px', color: '#888', marginTop: '3px' }}>{t('registerModal.useAnotherName')}</div>
                  </div>
                )}
                {existsConflict.length === 0 && promptNameWarning && (
                  <div style={{ marginTop: '6px', padding: '8px 12px', borderRadius: '6px',
                                background: '#3a2a00', border: '0.5px solid #ffaa00' }}>
                    <div style={{ fontSize: '11px', color: '#ffaa00', fontWeight: 600 }}>⚠ {t('registerModal.promptNameWarningTitle')}</div>
                    <div style={{ fontSize: '10px', color: '#aaa', marginTop: '3px' }}>{promptNameWarning}</div>
                  </div>
                )}
              </div>

              <div style={{ marginBottom: '12px' }}>
                <label style={lbl}>{t('registerModal.hearingContentLabel')}
                  <span style={{ color: '#555', marginLeft: '6px' }}>{t('registerModal.hearingContentHint')}</span>
                </label>
                <textarea value={hearingText} onChange={e => setHearingText(e.target.value)}
                  placeholder={t('registerModal.hearingContentPlaceholder')}
                  style={{ ...inp, resize: 'vertical', minHeight: '160px', lineHeight: 1.7 }} />
              </div>

              <div>
                <label style={lbl}>{t('registerModal.attachHearingSheet')}</label>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
                  <button onClick={() => fileInputRef.current?.click()} style={btnGhost}>📎 {t('registerModal.addFile')}</button>
                  {attachedFiles.map(f => (
                    <div key={f.name} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px',
                                               padding: '4px 10px', borderRadius: '6px',
                                               background: '#0d2f25', border: '0.5px solid #5DCAA544' }}>
                      <span style={{ fontSize: '11px', color: '#5DCAA5' }}>📄 {f.name}</span>
                      <button onClick={() => setAttachedFiles(prev => prev.filter(x => x.name !== f.name))}
                        style={{ background: 'transparent', border: 'none', color: '#555',
                                 cursor: 'pointer', fontSize: '13px', padding: 0 }}>×</button>
                    </div>
                  ))}
                </div>
                <input ref={fileInputRef} type="file" accept=".md,.txt" multiple
                  style={{ display: 'none' }} onChange={handleFileAttach} />
              </div>

              <div style={{ marginTop: '14px', paddingTop: '14px', borderTop: '0.5px solid #2a2a30' }}>
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', cursor: 'pointer' }}>
                  <input type="checkbox" checked={forceNewDomain}
                    onChange={e => setForceNewDomain(e.target.checked)}
                    style={{ marginTop: '2px', cursor: 'pointer' }} />
                  <span>
                    <span style={{ fontSize: '12px', color: '#eee' }}>{t('registerModal.forceNewDomain')}</span>
                    <div style={{ fontSize: '10px', color: '#666', marginTop: '2px' }}>
                      {t('registerModal.forceNewDomainHint')}
                    </div>
                  </span>
                </label>
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', cursor: 'pointer', marginTop: '10px' }}>
                  <input type="checkbox" checked={autoResolve}
                    onChange={e => setAutoResolve(e.target.checked)}
                    style={{ marginTop: '2px', cursor: 'pointer' }} />
                  <span>
                    <span style={{ fontSize: '12px', color: '#eee' }}>{t('registerModal.autoResolve')}</span>
                    <div style={{ fontSize: '10px', color: '#666', marginTop: '2px' }}>
                      {t('registerModal.autoResolveHint')}
                    </div>
                  </span>
                </label>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
              <button onClick={onClose} style={btnGhost}>{t('registerModal.cancel')}</button>
              <button onClick={handleRun} disabled={!canRun}
                style={{ ...btnPrimary, opacity: canRun ? 1 : 0.35 }}>
                🚀 {t('registerModal.startGeneration')}
              </button>
            </div>
          </>
        )}

        {/* ══ running ══ */}
        {phase === 'running' && (
          <div style={{ textAlign: 'center', padding: '50px 0' }}>
            <div style={{ fontSize: '36px', marginBottom: '20px' }}>{job?.stage === 'fixing' ? '🛠' : '⚙️'}</div>
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#5DCAA5', marginBottom: '10px' }}>
              {runningHeader(job?.stage, t)}
            </div>
            {reconnecting && (
              <div style={{ fontSize: '11px', color: '#f97316', marginBottom: '10px' }}>
                ⚠ {t('registerModal.connectionUnstable')}
              </div>
            )}
            <div style={{ textAlign: 'left', maxWidth: '440px', margin: '0 auto' }}>
              {statusLog.map((log, i) => (
                <div key={i} style={{ fontSize: '12px', color: i === statusLog.length - 1 ? '#eee' : '#555',
                                      padding: '4px 0', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '50%',
                                 background: i === statusLog.length - 1 ? '#5DCAA5' : 'transparent',
                                 flexShrink: 0 }} />
                  {log}
                </div>
              ))}
            </div>
            {job?.stage === 'fixing' && (
              <div style={{ marginTop: '18px' }}>
                <button onClick={handleInterrupt} disabled={interrupting} style={btnGhost}>
                  {interrupting ? t('registerModal.interrupting') : `🛑 ${t('registerModal.interruptAgent')}`}
                </button>
              </div>
            )}
            <div style={{ fontSize: '11px', color: '#444', marginTop: '20px' }}>
              {t('registerModal.runningFooterNote')}
            </div>
          </div>
        )}

        {/* ══ agent_question（エージェントがその場で質問） ══ */}
        {phase === 'agent_question' && job && (
          <div>
            <div style={{ ...card, borderColor: '#5DCAA5' }}>
              <div style={{ fontSize: '15px', fontWeight: 700, color: '#5DCAA5', marginBottom: '6px' }}>
                💬 {t('registerModal.agentQuestionTitle')}
              </div>
              <div style={{ fontSize: '11px', color: '#888', marginBottom: '10px' }}>
                {t('registerModal.agentQuestionHint')}
              </div>
              <div style={{ fontSize: '13px', color: '#eee', padding: '10px 12px', borderRadius: '6px',
                            background: '#0d1f19', border: '0.5px solid #5DCAA544', lineHeight: 1.6 }}>
                {job.pending_question}
              </div>
            </div>

            {/* 2026-09-19追加（Koshoshi合意）: 自動操縦（auto_resolve）ジョブでは、
                裏でバックエンドが自律的に回答を組み立てて進行する。この画面は
                以前は通常モードと全く同じ「人間が回答を書いて送信する」UIを
                そのまま出していたため、自動操縦が実際には動いているのに画面上は
                人間の入力待ちにしか見えず、操作不要であることが伝わらなかった
                （実機フィードバック、2026-09-19）。auto_resolve時は入力欄・
                送信ボタンを出さず、自動対応中であることを明示するだけにする。 */}
            {job.auto_resolve ? (
              <div style={{ ...card, borderColor: '#8b5cf6' }}>
                <div style={{ fontSize: '12px', color: '#c4b5fd', lineHeight: 1.6 }}>
                  {t('registerModal.autopilotAnsweringHint')}
                </div>
              </div>
            ) : (
              <>
                <div style={card}>
                  <textarea value={agentAnswerText} onChange={e => setAgentAnswerText(e.target.value)}
                    placeholder={t('registerModal.agentAnswerPlaceholder')}
                    style={{ ...inp, resize: 'vertical', minHeight: '70px' }} autoFocus />
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', flexWrap: 'wrap' }}>
                  <button onClick={handleCancel} style={btnGhost}>{t('registerModal.dealLater')}</button>
                  <button onClick={handleAgentAnswer} disabled={confirming || !agentAnswerText.trim()}
                    style={{ ...btnPrimary, opacity: (confirming || !agentAnswerText.trim()) ? 0.5 : 1 }}>
                    {confirming ? t('registerModal.applying') : `💬 ${t('registerModal.submitAnswer')}`}
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* ══ confirming（Q&Aゲート） ══ */}
        {phase === 'confirming' && job && (
          <div>
            {/* 2026-09-19追加（Koshoshi合意）: agent_question画面と同じ理由で、
                auto_resolveジョブの場合は自動操縦が裏で対応中であることを
                最初に明示する（実機フィードバック、2026-09-19）。 */}
            {job.auto_resolve && (
              <div style={{ ...card, borderColor: '#8b5cf6' }}>
                <div style={{ fontSize: '12px', fontWeight: 700, color: '#c4b5fd', marginBottom: '4px' }}>
                  {t('registerModal.autopilotWorkingTitle')}
                </div>
                <div style={{ fontSize: '11px', color: '#a78bfa', lineHeight: 1.6 }}>
                  {t('registerModal.autopilotWorkingHint')}
                </div>
              </div>
            )}
            <div style={{ ...card, borderColor: '#f97316' }}>
              <div style={{ fontSize: '15px', fontWeight: 700, color: '#f97316', marginBottom: '6px' }}>
                ❓ {t('registerModal.confirmationNeeded')}
              </div>
              <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>
                {t('registerModal.confirmationNeededHint')}
              </div>
              <div style={{ fontSize: '10px', color: '#f97316', marginTop: '8px', padding: '8px 10px',
                            borderRadius: '6px', background: '#1a1208', lineHeight: 1.6 }}>
                {t(job.loop_exhausted ? 'registerModal.confirmationExplanationLoopExhausted' : 'registerModal.confirmationExplanation')}
              </div>
              {job.match_type && (
                <div style={{ fontSize: '11px', marginTop: '6px' }}>
                  {t('registerModal.processingMethod')}:{' '}
                  <span style={{ color: matchTypeColor(job.match_type), fontWeight: 600 }}>
                    {matchTypeLabel(job.match_type)}
                  </span>
                  {job.base_domain && (
                    <span style={{ color: '#555', marginLeft: '8px' }}>{t('registerModal.base')}: {job.base_domain}</span>
                  )}
                </div>
              )}
            </div>

            {getPresentCategoryGuidanceKeys(job).length > 0 && (
              <div style={{ ...card, borderColor: '#5DCAA5' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#5DCAA5', marginBottom: '8px' }}>
                  {t('registerModal.categorySummaryHeading')}
                </div>
                {getPresentCategoryGuidanceKeys(job).map(key => (
                  <div key={key} style={{ fontSize: '11px', color: '#ccc', padding: '6px 8px',
                                          marginBottom: '4px', borderRadius: '6px',
                                          background: '#0d1f19', lineHeight: 1.6 }}>
                    {t(`registerModal.${key}`)}
                  </div>
                ))}
              </div>
            )}

            <div style={card}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#888', marginBottom: '10px' }}>
                {t('registerModal.pointsToReview', { count: (job.questions ?? []).length })}
              </div>
              {(job.questions ?? []).map((q, i) => {
                const isExpanded = expandedIdx.has(i);
                const firstLine = q.split('\n').find(l => l.trim().length > 0) ?? q;
                const hasMore = q.trim() !== firstLine.trim();
                return (
                <div key={i} style={{ fontSize: '12px', color: '#eee', padding: '8px 10px',
                                      marginBottom: '6px', borderRadius: '6px',
                                      background: '#1a1208', border: '0.5px solid #f9731644',
                                      display: 'flex', alignItems: 'flex-start', gap: '8px' }}>
                  <div style={{ flex: 1, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                    {isExpanded ? q : firstLine}
                    {hasMore && (
                      <button
                        type="button"
                        onClick={() => setExpandedIdx(prev => {
                          const next = new Set(prev);
                          if (next.has(i)) next.delete(i); else next.add(i);
                          return next;
                        })}
                        style={{ marginLeft: '8px', fontSize: '10px', color: '#f97316',
                                 background: 'none', border: 'none', cursor: 'pointer',
                                 textDecoration: 'underline', padding: 0 }}
                      >
                        {isExpanded ? t('registerModal.collapse') : t('registerModal.expand')}
                      </button>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard?.writeText(q);
                      setCopiedIdx(i);
                      setTimeout(() => setCopiedIdx(cur => (cur === i ? null : cur)), 1500);
                    }}
                    title={t('registerModal.copyQuestionTitle')}
                    style={{ flexShrink: 0, fontSize: '10px', padding: '3px 8px', borderRadius: '4px',
                             background: '#2a2010', border: '0.5px solid #f9731666', color: '#f97316',
                             cursor: 'pointer', whiteSpace: 'nowrap' }}
                  >
                    {copiedIdx === i ? t('registerModal.copied') : t('registerModal.copyButton')}
                  </button>
                </div>
                );
              })}
            </div>

            <div style={card}>
              <label style={lbl}>{t('registerModal.memoLabel')}</label>
              <textarea value={answerText} onChange={e => setAnswerText(e.target.value)}
                placeholder={t('registerModal.memoPlaceholder')}
                style={{ ...inp, resize: 'vertical', minHeight: '70px' }} />
            </div>

            {hasBlockingDynamicIssue(job) && (
              <div style={{ ...card, borderColor: '#ff5555' }}>
                <div style={{ fontSize: '10px', color: '#ff5555', marginBottom: '8px', lineHeight: 1.6 }}>
                  {t('registerModal.forceApplyBlockedNote')}
                </div>
                {/* 2026-09-07追加（Koshoshi合意）: 「動的検証由来の指摘は無条件に
                    force_applyで握りつぶせない」という原則は変えないが、人間が
                    実際にコード・シナリオを確認した上で「実装バグではない」と
                    明示判断した場合に正式に登録を通すルートが無かった
                    （実務上のギャップ、2026-09-07実機テストで判明）。
                    上のメモ欄への理由記入とこのチェックの両方が揃わないと
                    ボタンは有効化しない。 */}
                <label style={{ display: 'flex', alignItems: 'flex-start', gap: '8px',
                                fontSize: '11px', color: '#ccc', lineHeight: 1.6, cursor: 'pointer' }}>
                  <input type="checkbox" checked={dynamicOverrideChecked}
                    onChange={e => setDynamicOverrideChecked(e.target.checked)}
                    style={{ marginTop: '2px' }} />
                  {t('registerModal.dynamicOverrideCheckboxLabel')}
                </label>
              </div>
            )}

            {/* 2026-09-19追加（Koshoshi合意）: auto_resolveジョブでは、これらの
                ボタンはバックエンド側の自律判断（_auto_decide_confirm_action）
                と競合しうる人間専用の操作のため、押せる形で出さない。実際に
                クリックされなくても「画面が人間の操作待ちに見える」こと自体が
                自動操縦の状況を誤解させるという実機フィードバック
                （2026-09-19）を踏まえ、自動操縦中はボタン行そのものを
                ステータス表示に置き換える。 */}
            {job.auto_resolve ? (
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <div style={{ fontSize: '11px', color: '#a78bfa' }}>{t('registerModal.autopilotWorkingHint')}</div>
              </div>
            ) : (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', flexWrap: 'wrap' }}>
                <button onClick={handleCancel} style={btnGhost}>{t('registerModal.dealLater')}</button>
                {hasBlockingDynamicIssue(job) ? (
                  <button onClick={() => handleConfirm(true, true)}
                    disabled={confirming || !dynamicOverrideChecked || !answerText.trim()}
                    style={{ ...btnGhost,
                             opacity: (!dynamicOverrideChecked || !answerText.trim()) ? 0.4 : 1,
                             cursor: (!dynamicOverrideChecked || !answerText.trim()) ? 'not-allowed' : undefined,
                             borderColor: '#ff5555', color: '#ff8888' }}
                    title={!answerText.trim()
                      ? t('registerModal.dynamicOverrideReasonRequired')
                      : t('registerModal.dynamicOverrideHint')}>
                    {t('registerModal.dynamicOverrideButton')}
                  </button>
                ) : (
                  <button onClick={() => handleConfirm(true)}
                    disabled={confirming}
                    style={btnGhost}
                    title={t('registerModal.forceApplyHint')}>
                    {t('registerModal.forceApply')}
                  </button>
                )}
                {/* 2026-07-17: loop_exhausted（1ラウンド+Gate2再検証を終えた最終ゲート）
                    では、新しいエージェントラウンドを開始する「続行」は出さない
                    （ユーザー要望: ループは1回で終わらせる）。 */}
                {!job.loop_exhausted && (
                  <button onClick={() => handleConfirm(false)} disabled={confirming}
                    style={{ ...btnPrimary, opacity: confirming ? 0.5 : 1 }}>
                    {confirming ? t('registerModal.applying') : `↻ ${t('registerModal.confirmAndContinue')}`}
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* ══ done ══ */}
        {phase === 'done' && result && (() => {
          // 2026-07-17修正: apply_domain_files はFrontendのtsc型検証に失敗すると
          // 今回のapply全体をロールバックし、status="typecheck_failed"・written=[]を
          // 返す（domain_generator.py参照）。しかしこの画面は以前phaseが'done'なら
          // 常に緑の「✅ 登録完了」を出していたため、実際には何も登録されず
          // ロールバックされたケースでも成功したように見える実機バグがあった。
          // ここでresult.statusを見て、失敗時は明確に失敗として表示する。
          const isRolledBack = result.status === 'typecheck_failed'
            || (result.status !== 'ok' && result.status !== 'partial'
                && (result.written ?? []).length === 0 && (result.errors ?? []).length > 0);
          return (
          <div>
            <div style={{ ...card, borderColor: isRolledBack ? '#ff5555' : '#0F6E56' }}>
              <div style={{ fontSize: '15px', fontWeight: 700,
                            color: isRolledBack ? '#ff5555' : '#50fa7b', marginBottom: '6px' }}>
                {isRolledBack
                  ? `❌ ${t('registerModal.registrationRolledBack')}`
                  : `✅ ${t('registerModal.registrationComplete')}`}
              </div>
              {isRolledBack && (
                <div style={{ fontSize: '11px', color: '#ff9999', marginBottom: '6px', lineHeight: 1.6 }}>
                  {t('registerModal.registrationRolledBackNote')}
                </div>
              )}
              {!isRolledBack && result.match_type && (
                <div style={{ fontSize: '11px', marginBottom: '4px' }}>
                  {t('registerModal.processingMethod')}:{' '}
                  <span style={{ color: matchTypeColor(result.match_type), fontWeight: 600 }}>
                    {matchTypeLabel(result.match_type)}
                  </span>
                  {result.base_domain && (
                    <span style={{ color: '#555', marginLeft: '8px' }}>{t('registerModal.base')}: {result.base_domain}</span>
                  )}
                </div>
              )}
              {!isRolledBack && result.classification?.reason && (
                <div style={{ fontSize: '10px', color: '#555', marginTop: '2px' }}>
                  {result.classification.reason}
                </div>
              )}
              {!isRolledBack && (
                <div style={{ fontSize: '11px', color: '#444', marginTop: '8px' }}>
                  {t('registerModal.restartNote')}
                </div>
              )}
            </div>

            {(result.gate2_warnings ?? []).length > 0 && (
              <div style={{ ...card, borderColor: '#f97316' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#f97316', marginBottom: '8px' }}>
                  ⚠ {t('registerModal.gate2Warnings', { count: result.gate2_warnings.length })}
                </div>
                <div style={{ fontSize: '10px', color: '#888', marginBottom: '8px' }}>
                  {t('registerModal.gate2WarningsHint')}
                </div>
                {result.gate2_warnings.map((w: string, i: number) => (
                  <div key={i} style={{ fontSize: '11px', color: '#ffb86c', marginBottom: '4px', lineHeight: 1.5 }}>
                    {w}
                  </div>
                ))}
                {result.post_fix_summary && (
                  <div style={{ fontSize: '10px', color: '#5DCAA5', marginTop: '8px', marginBottom: '4px', lineHeight: 1.6 }}>
                    {t('registerModal.postFixSummaryLabel')}: {result.post_fix_summary}
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '8px' }}>
                  <button onClick={handleFixRemaining} disabled={startingFix}
                    style={{ ...btnGhost, opacity: startingFix ? 0.5 : 1 }}
                    title={t('registerModal.fixRemainingHint')}>
                    {startingFix ? t('registerModal.applying') : `🛠 ${t('registerModal.fixRemainingNow')}`}
                  </button>
                </div>
              </div>
            )}

            {result.extensions_written_zero_warning && (
              <div style={{ ...card, borderColor: '#f97316' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#f97316', marginBottom: '6px' }}>
                  ⚠ {t('registerModal.extensionsZeroWarningTitle')}
                </div>
                <div style={{ fontSize: '11px', color: '#ccc', lineHeight: 1.6 }}>
                  {t('registerModal.extensionsZeroWarningBody', { domainName: result.domain_name })}
                </div>
              </div>
            )}

            {(result.written ?? []).length > 0 && (
              <div style={card}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#888', marginBottom: '8px' }}>
                  📁 {t('registerModal.writtenFiles')}
                </div>
                {(result.written ?? []).map((p: string) => (
                  <div key={p} style={{ fontSize: '11px', fontFamily: 'monospace',
                                        color: p.includes('skipped') ? '#555' : '#5DCAA5',
                                        marginBottom: '3px' }}>
                    {p.includes('skipped') ? '⏭' : '✓'} {p}
                  </div>
                ))}
              </div>
            )}

            {(result.scenarios_registered ?? []).length > 0 && (
              <div style={card}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#888', marginBottom: '8px' }}>
                  🗄 {t('registerModal.registeredScenarios')}
                </div>
                {(result.scenarios_registered ?? []).map((s: any, i: number) => (
                  <div key={i} style={{ fontSize: '11px', marginBottom: '3px',
                                        color: s.status === 'created' ? '#f97316' : '#555' }}>
                    {s.status === 'created' ? `✓ ${s.name} (ID: ${s.id})` : `⏭ ${s.name} (${t('registerModal.duplicateSkipped')})`}
                  </div>
                ))}
              </div>
            )}

            {(result.errors ?? []).length > 0 && (
              <div style={{ ...card, borderColor: '#ff5555' }}>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#ff5555', marginBottom: '8px' }}>
                  ⚠ {isRolledBack ? t('registerModal.error') : t('registerModal.partialErrors')}
                </div>
                {(result.errors ?? []).map((e: any, i: number) => (
                  <div key={i} style={{ fontSize: '11px', color: '#ff9999', marginBottom: '3px' }}>
                    ✗ {e.path}: {e.error}
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '8px' }}>
              <button onClick={onClose} style={btnPrimary}>{t('registerModal.close')}</button>
            </div>
          </div>
          );
        })()}

        {/* ══ cancelled（自動操縦による見送り） ══ */}
        {phase === 'cancelled' && (
          <div style={{ ...card, borderColor: '#ffaa00' }}>
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#ffaa00', marginBottom: '12px' }}>
              🛑 {t('registerModal.autopilotCancelledTitle')}
            </div>
            <pre style={{ fontSize: '12px', color: '#ddd', whiteSpace: 'pre-wrap', margin: 0 }}>
              {job?.auto_pilot_final_reasoning || t('registerModal.autopilotCancelledNoReason')}
            </pre>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px', gap: '10px' }}>
              <button onClick={() => { setPhase('input'); setJob(null); }} style={btnGhost}>{t('registerModal.backToInput')}</button>
              <button onClick={onClose} style={btnGhost}>{t('registerModal.close')}</button>
            </div>
          </div>
        )}

        {/* ══ error ══ */}
        {phase === 'error' && (
          <div style={{ ...card, borderColor: '#ff5555' }}>
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#ff5555', marginBottom: '12px' }}>❌ {t('registerModal.error')}</div>
            <pre style={{ fontSize: '12px', color: '#ff9999', whiteSpace: 'pre-wrap', margin: 0 }}>{errorMsg}</pre>
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px', gap: '10px' }}>
              <button onClick={() => { setPhase('input'); setErrorMsg(''); sessionStorage.removeItem(JOB_STORAGE_KEY); }} style={btnGhost}>{t('registerModal.backToInput')}</button>
              <button onClick={onClose} style={btnGhost}>{t('registerModal.close')}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
