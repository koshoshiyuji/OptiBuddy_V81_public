// ============================================================
// InfeasibleView.tsx  (V3.1: stale closure バグ修正)
// ============================================================
// V3.1 の変更点:
//   - fetchCandidates に dslOverride 引数を追加。
//     handleApply 内の setTimeout から呼ぶ際に最新の patched_dsl を
//     明示的に渡すことで、stale closure による古いDSL参照を防ぐ。
// ============================================================
import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { SOLVER_CONFIG } from '../../../domain/constants.ts';
import type { StudioState } from '../types.ts';

// ─────────────────────────────────────────────
// 型定義
// ─────────────────────────────────────────────

interface PatchParam {
  label:   string;
  min:     number;
  max:     number;
  default: number;
  unit:    string;
  step?:   number;
}

interface PatchOp {
  op:      string;
  path:    string;
  value:   unknown;
  param?:  PatchParam;
  // 2026-07-24追加: LLMに人間向けラベルを必ず出力させ、チャット文面等で生のDSLパス
  // （例: /customers/14/restricted_vehicle_types）をそのまま見せないようにする。
  // 旧レスポンス（labelなし）との互換のため任意項目とし、無ければpathにフォールバックする。
  label?:  string;
}

interface RelaxCandidate {
  title:          string;
  explanation:    string;
  tradeoff:       string;
  dsl_patch:      PatchOp[];
  patch_warnings?: string[];
}

type SearchState = 'idle' | 'loading' | 'done' | 'error';
type ApplyState  = 'idle' | 'applying' | 'done' | 'still_infeasible' | 'error';

// /relax/start + /relax/status のポーリングで受け取る進捗（2026-07-14追加）。
// バックエンドの job.stage と対応: queued → generating → verifying → done / error
interface RelaxProgress {
  stage: 'queued' | 'generating' | 'verifying' | 'done' | 'error';
  totalCandidates?: number;
  verifiedIndex?: number;
  currentTitle?: string | null;
}

// /relax/status/<job_id> のレスポンス型（バックエンドの _run_relax_job / job_set と対応）。
interface RelaxJobStatusResponse {
  status: string;
  message?: string;
  job?: {
    stage: RelaxProgress['stage'];
    total_candidates?: number;
    verified_index?: number;
    current_title?: string | null;
    result?: {
      candidates: RelaxCandidate[];
      dropped_candidates: { title: string; reason: string }[];
    };
    error?: string;
  };
}

// ─────────────────────────────────────────────
// ParamControl — スライダー + 数値入力
// ─────────────────────────────────────────────
// ─────────────────────────────────────────────
// CandidateCard
// ─────────────────────────────────────────────
function CandidateCard({
  candidate,
  index,
  applyState,
  onApply,
  onOpenChat,
}: {
  candidate:  RelaxCandidate;
  index:      number;
  applyState: ApplyState;
  onApply:    (c: RelaxCandidate) => void;
  onOpenChat: (c: RelaxCandidate) => void;
}) {
  const { t } = useTranslation();
  const isApplying        = applyState === 'applying';
  const isDone            = applyState === 'done';
  const isStillInfeasible = applyState === 'still_infeasible';

  // value: null の opが1つでもあれば、値の決定に人間の判断が必要
  // （以前はスライダーでこの場で入力させていたが、種類が多いので
  // チャット（AskPanel）へ誘導する方式に変更）。
  const needsHumanInput = candidate.dsl_patch.some((op) => op.value === null);
  const canApply = !isApplying && !isDone && !isStillInfeasible && !needsHumanInput && candidate.dsl_patch.length > 0;

  return (
    <div style={{
      background: isDone ? '#0a1f14' : isStillInfeasible ? '#1a0e00' : '#16161e',
      border: `1px solid ${isDone ? '#1D9E75' : isStillInfeasible ? '#f9731644' : '#2a2a30'}`,
      borderRadius: '10px',
      padding: '16px',
      display: 'flex',
      flexDirection: 'column',
      gap: '10px',
      transition: 'border-color 0.2s',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{
          fontSize: '10px', fontWeight: 900, padding: '2px 8px',
          borderRadius: '4px', background: '#00e5ff22',
          color: '#00e5ff', border: '1px solid #00e5ff44',
          flexShrink: 0,
        }}>
          {t('infeasible.candidateLabel', { n: index + 1 })}
        </span>
        <span style={{ fontSize: '13px', fontWeight: 700, color: '#eee' }}>
          {candidate.title}
        </span>
        {needsHumanInput && !isDone && (
          <span style={{
            marginLeft: 'auto', fontSize: '10px', color: '#00e5ff',
            border: '1px solid #00e5ff44', borderRadius: '4px',
            padding: '1px 6px', fontWeight: 700,
          }}>
            💬 {t('infeasible.needsConsultation')}
          </span>
        )}
        {isDone && (
          <span style={{ marginLeft: 'auto', fontSize: '11px', color: '#1D9E75', fontWeight: 700 }}>
            ✓ {t('infeasible.applied')}
          </span>
        )}
      </div>

      <div style={{ fontSize: '12px', color: '#bbb', lineHeight: 1.6 }}>
        {candidate.explanation}
      </div>

      <div style={{
        fontSize: '11px', color: '#888',
        background: '#0d0d12', borderRadius: '6px',
        padding: '8px 10px', borderLeft: '3px solid #BA7517',
        lineHeight: 1.5,
      }}>
        ⚠️ {t('infeasible.tradeoffPrefix')} {candidate.tradeoff}
      </div>

      {needsHumanInput && !isDone && (
        <div style={{
          fontSize: '11px', color: '#00e5ff',
          background: '#0d1a1f', borderRadius: '6px',
          padding: '8px 10px', borderLeft: '3px solid #00e5ff',
          lineHeight: 1.6,
        }}>
          {t('infeasible.needsHumanInputNote')}
        </div>
      )}

      {(candidate.patch_warnings ?? []).length > 0 && (
        <div style={{
          fontSize: '10px', color: '#ff4d4d',
          background: '#1a0a0a', borderRadius: '6px',
          padding: '6px 10px', lineHeight: 1.6,
        }}>
          ⚠️ {t('infeasible.pathWarningsPrefix')} {candidate.patch_warnings!.join(' / ')}
        </div>
      )}

      {candidate.dsl_patch.length > 0 && (
        <details style={{ fontSize: '11px' }}>
          <summary style={{ color: '#555', cursor: 'pointer', userSelect: 'none' }}>
            {t('infeasible.confirmDslPatch', { n: candidate.dsl_patch.length })}
          </summary>
          <pre style={{
            marginTop: '8px', padding: '8px', background: '#0a0a10',
            borderRadius: '6px', color: '#888', overflowX: 'auto',
            fontSize: '10px', lineHeight: 1.5,
          }}>
            {JSON.stringify(candidate.dsl_patch, null, 2)}
          </pre>
        </details>
      )}

      {!isDone && !needsHumanInput && (
        <button
          onClick={() => onApply(candidate)}
          disabled={!canApply}
          style={{
            padding: '8px 16px', borderRadius: '6px',
            fontSize: '12px', fontWeight: 700,
            cursor: canApply ? 'pointer' : isApplying ? 'wait' : 'not-allowed',
            background: isApplying ? '#333' : canApply ? '#1D9E7522' : '#1a1a1a',
            border: `1px solid ${isApplying ? '#555' : canApply ? '#1D9E75' : '#333'}`,
            color: isApplying ? '#888' : canApply ? '#1D9E75' : '#444',
            alignSelf: 'flex-start',
            transition: 'all 0.15s',
          }}
        >
          {isApplying ? t('infeasible.applying') : `✅ ${t('infeasible.applyButton')}`}
        </button>
      )}

      {!isDone && needsHumanInput && (
        <button
          onClick={() => onOpenChat(candidate)}
          style={{
            padding: '8px 16px', borderRadius: '6px',
            fontSize: '12px', fontWeight: 700,
            cursor: 'pointer',
            background: '#00e5ff18',
            border: '1px solid #00e5ff44',
            color: '#00e5ff',
            alignSelf: 'flex-start',
            transition: 'all 0.15s',
          }}
        >
          💬 {t('infeasible.openChatButton')}
        </button>
      )}

      {isDone && (
        <div style={{ fontSize: '12px', color: '#1D9E75' }}>
          {t('infeasible.doneMessage')}
        </div>
      )}
      {isStillInfeasible && (
        <div style={{ fontSize: '12px', color: '#f97316' }}>
          ⚠️ {t('infeasible.stillInfeasibleMessage')}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// InfeasibleView 本体
// ─────────────────────────────────────────────
export function InfeasibleView({ studio }: { studio: StudioState }) {
  const { t, i18n } = useTranslation();
  const [searchState, setSearchState] = useState<SearchState>('idle');
  const [candidates, setCandidates]   = useState<RelaxCandidate[]>([]);
  const [applyStates, setApplyStates] = useState<Record<number, ApplyState>>({});
  const [errorMsg, setErrorMsg]       = useState<string | null>(null);
  const [localPatchedDsl, setLocalPatchedDsl] = useState<unknown | null>(null);
  const [progress, setProgress]       = useState<RelaxProgress | null>(null);

  // アンマウント後にポーリングのsetStateが走らないようにするフラグ。
  const cancelledRef = useRef(false);
  useEffect(() => () => { cancelledRef.current = true; }, []);

  const effectiveDsl = (
    localPatchedDsl ??
    studio.patchedDsls?.[studio.selectedSolutionIndex] ??
    studio.initialInputData
  );

  const solveFailedIssues = studio.allIssues.filter(
    (i) => i.id === 'solve_failed' || (i as any).type === 'SOLVE_FAILED'
  );
  const isInfeasible = solveFailedIssues.length > 0 || !!studio.solverErrorMessage;

  // ── ★ 修正: dslOverride 引数を追加 ──────────────────────────
  // setTimeout から呼ぶ際に最新の patched_dsl を明示的に渡す。
  // React の state 更新は次レンダリングまで反映されないため、
  // setLocalPatchedDsl 直後の fetchCandidates は古い effectiveDsl を
  // 参照してしまう（stale closure）。dslOverride で回避する。
  // ポーリング間隔とタイムアウト上限（緩和候補は多くても数件想定のため、
  // ドメイン登録のポーリング(1400ms)より短めにして体感の反応を良くする）。
  // 2026-07-24: 一時的に20分へ延長したが、「頭数不足」系の原因は検証で
  // 弾かれ続けて結局候補にたどり着かないケースがあり、長く待たせるだけで
  // 益がないと判断。5分に戻す（根本対策はLLM側の緩和案の質・検証ロジックの
  // 見直しで行うべき問題であり、待機時間の延長では解決しない）。
  const RELAX_POLL_INTERVAL_MS = 1000;
  const RELAX_MAX_POLLS        = 300; // 5分でタイムアウト

  // ── ★ 修正: dslOverride 引数を追加 ──────────────────────────
  // setTimeout から呼ぶ際に最新の patched_dsl を明示的に渡す。
  // React の state 更新は次レンダリングまで反映されないため、
  // setLocalPatchedDsl 直後の fetchCandidates は古い effectiveDsl を
  // 参照してしまう（stale closure）。dslOverride で回避する。
  //
  // 2026-07-14: /relax → /relax/start + /relax/status のポーリング方式に変更。
  // 緩和案の生成(LLM)と候補ごとのドライラン再ソルブ検証はどちらも時間がかかり
  // 得るため、進捗を progress state に反映して画面が固まって見える問題に対応する。
  const fetchCandidates = useCallback(async (
    dslOverride?: unknown,
    auto = false,
  ) => {
    setSearchState('loading');
    setErrorMsg(null);
    setCandidates([]);
    setProgress({ stage: 'queued' });
    if (!auto) setApplyStates({});

    // dslOverride が渡された場合は優先使用（stale closure 回避）
    const dslToUse = dslOverride ?? effectiveDsl;

    try {
      const currentSolveFailedIssues = studio.allIssues.filter(
        (i) => i.id === 'solve_failed' || (i as any).type === 'SOLVE_FAILED'
      );
      // 2026-07-30 i18n対応: LLMが生成する緩和案(title/explanation/tradeoff/
      // dsl_patchのlabel)の出力言語を切り替えるため?lang=を付与する
      // （Backend/llm/relax_interface.py, app.py _run_relax_job参照）。
      const startRes = await fetch(`${SOLVER_CONFIG.API_BASE}/relax/start?lang=${i18n.language}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dsl:    dslToUse,
          issues: currentSolveFailedIssues,
        }),
      });
      if (!startRes.ok) throw new Error(`HTTP ${startRes.status}`);
      const startData = await startRes.json();
      if (startData.status !== 'started' || !startData.job_id) {
        throw new Error(startData.message ?? 'Unknown error');
      }
      const jobId = startData.job_id as string;

      for (let i = 0; i < RELAX_MAX_POLLS; i++) {
        if (cancelledRef.current) return;
        await new Promise((r) => setTimeout(r, RELAX_POLL_INTERVAL_MS));
        if (cancelledRef.current) return;

        let statusData: RelaxJobStatusResponse | undefined;
        try {
          const statusRes = await fetch(`${SOLVER_CONFIG.API_BASE}/relax/status/${jobId}`);
          if (!statusRes.ok) continue; // 一時的な接続断は無視してポーリング継続
          statusData = await statusRes.json();
        } catch {
          continue; // ネットワーク瞬断はリトライ（サーバー側ジョブは動き続けている）
        }
        if (statusData?.status !== 'ok' || !statusData.job) continue;

        const j = statusData.job;
        setProgress({
          stage:           j.stage,
          totalCandidates: j.total_candidates,
          verifiedIndex:   j.verified_index,
          currentTitle:    j.current_title,
        });

        if (j.stage === 'done') {
          setCandidates(j.result?.candidates ?? []);
          setSearchState('done');
          return;
        }
        if (j.stage === 'error') {
          throw new Error(j.error ?? 'Unknown error');
        }
      }
      throw new Error(t('infeasible.searchTimeout'));
    } catch (e) {
      setErrorMsg(String(e));
      setSearchState('error');
    }
  }, [effectiveDsl, studio.allIssues, t, i18n.language]);

  const searchStateRef = useRef(searchState);
  useEffect(() => { searchStateRef.current = searchState; }, [searchState]);

  const handleApply = async (
    candidate: RelaxCandidate,
    index:     number,
  ) => {
    setApplyStates((prev) => ({ ...prev, [index]: 'applying' }));
    try {
      const patchRes = await fetch(`${SOLVER_CONFIG.API_BASE}/apply_patch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dsl:       effectiveDsl,
          dsl_patch: candidate.dsl_patch,
        }),
      });
      if (!patchRes.ok) throw new Error(`apply_patch HTTP ${patchRes.status}`);
      const patchData = await patchRes.json();
      if (patchData.status !== 'ok') throw new Error(patchData.message ?? 'patch failed');

      // ★ 修正: nextDsl を変数に保持し、setTimeout に明示的に渡す
      const nextDsl = patchData.patched_dsl;
      setLocalPatchedDsl(nextDsl);

      const result = await studio.handleApplyAndReoptimize(nextDsl);
      const feasible = result?.feasible ?? true;

      if (feasible) {
        setLocalPatchedDsl(null);
        setApplyStates((prev) => ({ ...prev, [index]: 'done' }));
      } else {
        setApplyStates((prev) => ({ ...prev, [index]: 'still_infeasible' }));
        // ★ 修正: nextDsl を dslOverride として渡す（stale closure 回避）
        setTimeout(() => {
          fetchCandidates(nextDsl, true);
        }, 800);
      }
    } catch (e) {
      console.error('[InfeasibleView] apply failed:', e);
      setApplyStates((prev) => ({ ...prev, [index]: 'error' }));
    }
  };

  // 値の決定にユーザー判断が必要な緩和案について、AskPanel（チャット）に
  // 渡すプリフィル文を組み立てる。
  const buildChatPrompt = (candidate: RelaxCandidate): string => {
    const needsInput = candidate.dsl_patch.filter((op) => op.value === null);
    const resolved   = candidate.dsl_patch.filter((op) => op.value !== null);

    let text = `緩和案「${candidate.title}」について相談したいです。\n\n${candidate.explanation}\n\n`;

    if (needsInput.length > 0) {
      text += `以下の値を具体的に指定してください:\n`;
      needsInput.forEach((op) => {
        const p = op.param;
        const label = op.label ?? p?.label ?? op.path;
        text += p
          ? `- ${label}（最小推奨: ${p.min}${p.unit ?? ''}、現実的な上限: ${p.max}${p.unit ?? ''}）\n`
          : `- ${label}\n`;
      });
    }

    if (resolved.length > 0) {
      text += `\nこの案では他に以下も変更されます: ${resolved.map((op) => `${op.label ?? op.path} → ${JSON.stringify(op.value)}`).join(', ')}\n`;
    }

    return text;
  };

  const handleOpenChat = (candidate: RelaxCandidate) => {
    (studio as any).requestAskPanel(buildChatPrompt(candidate));
  };

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: '12px',
      height: '100%', overflowY: 'auto', padding: '4px 2px',
      boxSizing: 'border-box',
    }}>

      {/* ステータスバナー */}
      <div style={{
        padding: '12px 16px', borderRadius: '10px',
        background: isInfeasible ? '#1a0a0a' : '#0a1a10',
        border: `1px solid ${isInfeasible ? '#ff4d4d44' : '#1D9E7544'}`,
        display: 'flex', alignItems: 'center', gap: '10px',
      }}>
        <span style={{ fontSize: '18px' }}>
          {isInfeasible ? '❌' : '✅'}
        </span>
        <div>
          <div style={{ fontSize: '13px', fontWeight: 700, color: isInfeasible ? '#ff4d4d' : '#1D9E75' }}>
            {isInfeasible ? t('infeasible.statusInfeasible') : t('infeasible.statusFeasible')}
          </div>
          {isInfeasible && solveFailedIssues[0] && (
            <div style={{ fontSize: '12px', color: '#888', marginTop: '4px' }}>
              {(solveFailedIssues[0] as any).message ?? (solveFailedIssues[0] as any).description ?? t('infeasible.constraintConflictFallback')}
            </div>
          )}
          {!isInfeasible && (
            <div style={{ fontSize: '12px', color: '#888', marginTop: '4px' }}>
              {t('infeasible.feasibleHint')}
            </div>
          )}
        </div>
        {isInfeasible && searchState === 'idle' && (
          <button
            onClick={() => fetchCandidates()}
            style={{
              marginLeft: 'auto', padding: '6px 14px', borderRadius: '6px',
              fontSize: '11px', fontWeight: 700, cursor: 'pointer',
              background: '#00e5ff18', border: '1px solid #00e5ff44', color: '#00e5ff',
            }}
          >
            🤖 {t('infeasible.searchCandidates')}
          </button>
        )}
        {isInfeasible && searchState !== 'idle' && (
          <button
            onClick={() => fetchCandidates()}
            disabled={searchState === 'loading'}
            style={{
              marginLeft: 'auto', padding: '6px 14px', borderRadius: '6px',
              fontSize: '11px', fontWeight: 700,
              cursor: searchState === 'loading' ? 'wait' : 'pointer',
              background: 'none', border: '1px solid #555', color: '#888',
            }}
          >
            {searchState === 'loading' ? t('infeasible.searching') : t('infeasible.searchAgain')}
          </button>
        )}
      </div>

      {/* ローディング（2026-07-14: job/status ポーリングの進捗を段階表示） */}
      {searchState === 'loading' && (
        <div style={{
          padding: '24px', textAlign: 'center',
          background: '#121216', borderRadius: '10px', border: '1px solid #2a2a30',
        }}>
          {progress?.stage === 'verifying' && progress.totalCandidates ? (
            <>
              <div style={{ fontSize: '13px', color: '#00e5ff', marginBottom: '8px' }}>
                🧪 {t('infeasible.verifyingProgress', {
                  current: progress.verifiedIndex ?? 0,
                  total:   progress.totalCandidates,
                })}
              </div>
              <div style={{
                width: '100%', height: '4px', borderRadius: '2px',
                background: '#2a2a30', overflow: 'hidden', margin: '10px 0',
              }}>
                <div style={{
                  width: `${Math.min(100, ((progress.verifiedIndex ?? 0) / progress.totalCandidates) * 100)}%`,
                  height: '100%', background: '#00e5ff', transition: 'width 0.3s ease',
                }} />
              </div>
              {progress.currentTitle && (
                <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>
                  {progress.currentTitle}
                </div>
              )}
              <div style={{ fontSize: '11px', color: '#555' }}>
                {t('infeasible.verifyingSubtext')}
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: '13px', color: '#00e5ff', marginBottom: '8px' }}>
                🤖 {t('infeasible.analyzing')}
              </div>
              <div style={{ fontSize: '11px', color: '#555' }}>
                {t('infeasible.analyzingSubtext')}
              </div>
            </>
          )}
        </div>
      )}

      {/* エラー */}
      {searchState === 'error' && (
        <div style={{
          padding: '16px', borderRadius: '10px',
          background: '#1a0a0a', border: '1px solid #ff4d4d44',
          fontSize: '12px', color: '#ff4d4d',
        }}>
          ⚠️ {errorMsg}
          <button
            onClick={() => fetchCandidates()}
            style={{
              marginLeft: '12px', fontSize: '11px', color: '#ff4d4d',
              background: 'none', border: '1px solid #ff4d4d44',
              borderRadius: '4px', padding: '2px 8px', cursor: 'pointer',
            }}
          >
            {t('infeasible.retry')}
          </button>
        </div>
      )}

      {/* 緩和候補一覧 */}
      {searchState === 'done' && candidates.length === 0 && (
        <div style={{
          padding: '24px', textAlign: 'center',
          background: '#121216', borderRadius: '10px', border: '1px solid #2a2a30',
          fontSize: '12px', color: '#555',
        }}>
          {t('infeasible.noCandidates')}
        </div>
      )}

      {searchState === 'done' && candidates.length > 0 && (
        <>
          <div style={{ fontSize: '11px', color: '#555', fontWeight: 700, letterSpacing: '1px' }}>
            {t('infeasible.candidatesHeader', { n: candidates.length })}
          </div>
          {candidates.map((c, i) => (
            <CandidateCard
              key={i}
              candidate={c}
              index={i}
              applyState={applyStates[i] ?? 'idle'}
              onApply={(candidate) => void handleApply(candidate, i)}
              onOpenChat={(candidate) => handleOpenChat(candidate)}
            />
          ))}
        </>
      )}

      {!isInfeasible && searchState === 'idle' && (
        <div style={{
          padding: '40px', textAlign: 'center',
          background: '#121216', borderRadius: '10px', border: '1px solid #2a2a30',
          fontSize: '12px', color: '#444',
        }}>
          {t('infeasible.idleHintLine1')}<br />
          {t('infeasible.idleHintLine2')}
        </div>
      )}
    </div>
  );
}