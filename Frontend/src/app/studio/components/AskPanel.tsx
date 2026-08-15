// src/app/studio/components/AskPanel.tsx
import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { SOLVER_CONFIG } from '../../../domain/constants.ts';
import type { Dsl, Issue, Task } from '../../../domain/types.ts';

export interface AskResult {
  explanation: string;
  dsl_patch: object[];
  can_optimize: boolean;
  root_cause: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  root_cause?: string;
  can_optimize?: boolean;
  dsl_patch?: object[];
}

interface Props {
  dsl: Dsl;
  solution: { 
    tasks: Task[]; 
    makespan: number; 
    issues: Issue[];
    solutions?: Array<{ name: string; tasks: Task[]; makespan: number; penalty?: number }>;
  };
  onApplyAndReoptimize: (patchedDsl: Dsl) => void;
  // 外部（制約見直しタブの「チャットで相談する」ボタン等）からパネルを開いて
  // 質問をプリフィルするための任意 props。openSignalは毎回異なる値（nonce）を
  // 渡すことで、同じテキストでも強制的に開くトリガーとして使う。
  initialQuestion?: string;
  openSignal?: number;
  // 2026-07-24追加: パネルの開閉状態を親（StudioShell）に伝える。
  // 本パネルは position:fixed の固定オーバーレイのため、開いた状態のまま
  // メインコンテンツの幅を変えないと、緩和案カードの文章等がパネルの下に
  // 隠れて読めなくなる（「表示エリアが狭い」という報告の原因）。
  // 親側でこれを使ってコンテンツ側に右マージンを取ってもらう。
  onOpenChange?: (open: boolean) => void;
}

// -------------------------------------------------------
// スタイル定数
// -------------------------------------------------------
const PANEL_STYLE: React.CSSProperties = {
  position: 'fixed',
  bottom: 24,
  right: 24,
  zIndex: 1000,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'flex-end',
  gap: 8,
};

const MODAL_STYLE: React.CSSProperties = {
  width: 400,
  height: 640,
  background: '#13131a',
  border: '1px solid #2a2a3a',
  borderRadius: 12,
  boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
  display: 'flex',
  flexDirection: 'column',
  overflow: 'hidden',
};

const HEADER_STYLE: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  padding: '12px 16px',
  background: '#1a1a28',
  borderBottom: '1px solid #2a2a3a',
  flexShrink: 0,
};

const HISTORY_STYLE: React.CSSProperties = {
  flex: 1,
  overflowY: 'auto',
  padding: '12px 16px',
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
};

const INPUT_AREA_STYLE: React.CSSProperties = {
  padding: '12px 16px',
  borderTop: '1px solid #2a2a3a',
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  flexShrink: 0,
  background: '#1a1a28',
};

const TEXTAREA_STYLE: React.CSSProperties = {
  width: '100%',
  minHeight: 60,
  background: '#0d0d14',
  border: '1px solid #2a2a3a',
  borderRadius: 8,
  color: '#eee',
  fontSize: 13,
  padding: '8px 10px',
  resize: 'none',
  fontFamily: 'inherit',
  boxSizing: 'border-box',
};

const SEND_BTN_STYLE: React.CSSProperties = {
  alignSelf: 'flex-end',
  background: '#00e5ff',
  color: '#000',
  border: 'none',
  borderRadius: 6,
  padding: '6px 18px',
  fontSize: 13,
  fontWeight: 700,
  cursor: 'pointer',
};

const APPLY_BTN_STYLE: React.CSSProperties = {
  background: '#00ff9d',
  color: '#000',
  border: 'none',
  borderRadius: 6,
  padding: '7px 14px',
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
  marginTop: 6,
  width: '100%',
};

const FLOAT_BTN_STYLE: React.CSSProperties = {
  width: 52,
  height: 52,
  borderRadius: '50%',
  background: '#00e5ff',
  color: '#000',
  border: 'none',
  fontSize: 22,
  cursor: 'pointer',
  boxShadow: '0 4px 16px rgba(0,229,255,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};

// -------------------------------------------------------
// メインコンポーネント
// -------------------------------------------------------
export function AskPanel({ dsl, solution, onApplyAndReoptimize, initialQuestion, openSignal, onOpenChange }: Props) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  useEffect(() => { onOpenChange?.(open); }, [open, onOpenChange]);
  const [question, setQuestion] = useState('');
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [applyLoading, setApplyLoading] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const historyEndRef = useRef<HTMLDivElement>(null);

  // パネルを開いたらtextareaにフォーカス
  useEffect(() => {
    if (open) setTimeout(() => textareaRef.current?.focus(), 100);
  }, [open]);

  // 外部トリガー（openSignal）が更新されたら、質問をプリフィルしてパネルを開く。
  useEffect(() => {
    if (!openSignal) return;
    setOpen(true);
    if (initialQuestion) setQuestion(initialQuestion);
    setTimeout(() => textareaRef.current?.focus(), 100);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openSignal]);

  // 新しいメッセージが来たら一番下にスクロール
  useEffect(() => {
    historyEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory, loading]);

  const handleClose = () => {
    setOpen(false);
    // 注意: 履歴は閉じても保持。リセットしたい場合は setChatHistory([]) を追加
  };

  const handleSend = async () => {
    if (!question.trim() || loading) return;

    const currentQuestion = question.trim();
    setQuestion('');
    setLoading(true);
    setError(null);

    // ユーザーメッセージを先に表示
    setChatHistory(prev => [...prev, { role: 'user', content: currentQuestion }]);

    // LLMに渡す履歴（直近10件、role/contentのみ）
    const historyForApi = chatHistory.slice(-10).map(m => ({
      role: m.role,
      content: m.content,
    }));

    try {
      const res = await fetch(`${SOLVER_CONFIG.API_BASE}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: currentQuestion,
          dsl,
          solution,
          history: historyForApi,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.status !== 'ok') throw new Error(data.message ?? 'Unknown error');

      // アシスタントの回答を履歴に追加
      setChatHistory(prev => [
        ...prev,
        {
          role: 'assistant',
          content: data.explanation,
          root_cause: data.root_cause,
          can_optimize: data.can_optimize,
          dsl_patch: data.dsl_patch,
        },
      ]);
    } catch (e) {
      setError(String(e));
      // 失敗したらユーザーメッセージも取り消す
      setChatHistory(prev => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  };

  const handleApply = async (msg: ChatMessage, index: number) => {
    if (!msg.dsl_patch?.length || applyLoading !== null) return;
    setApplyLoading(index);

    try {
      const patchRes = await fetch(`${SOLVER_CONFIG.API_BASE}/apply_patch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dsl, dsl_patch: msg.dsl_patch }),
      });
      if (!patchRes.ok) throw new Error(`patch HTTP ${patchRes.status}`);
      const patchData = await patchRes.json();
      if (patchData.status !== 'ok') throw new Error('patch failed');

      onApplyAndReoptimize(patchData.patched_dsl as Dsl);
      // 再最適化したら履歴リセット＆パネルを閉じる
      setChatHistory([]);
      setOpen(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setApplyLoading(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSend();
  };

  return (
    <div style={PANEL_STYLE}>
      {open && (
        <div style={MODAL_STYLE}>
          {/* ヘッダー */}
          <div style={HEADER_STYLE}>
            <span style={{ fontSize: 14, fontWeight: 700, color: '#00e5ff' }}>
              💬 OptiBuddy AI
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              {chatHistory.length > 0 && (
                <button
                  onClick={() => setChatHistory([])}
                  style={{ background: 'none', border: 'none', color: '#555', fontSize: 11, cursor: 'pointer' }}
                >
                  {t('askPanel.clearHistory')}
                </button>
              )}
              <button
                onClick={handleClose}
                style={{ background: 'none', border: 'none', color: '#888', fontSize: 18, cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>
          </div>

          {/* チャット履歴エリア */}
          <div style={HISTORY_STYLE}>
            {chatHistory.length === 0 && (
              <div style={{ color: '#444', fontSize: 12, textAlign: 'center', marginTop: 24 }}>
                {t('askPanel.emptyStateHint')}
              </div>
            )}

            {chatHistory.map((msg, i) => (
              <div key={i}>
                {msg.role === 'user' ? (
                  /* ユーザーメッセージ */
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <div style={{
                      maxWidth: '80%',
                      background: '#00e5ff22',
                      border: '1px solid #00e5ff44',
                      borderRadius: '10px 10px 2px 10px',
                      padding: '8px 12px',
                      fontSize: 13,
                      color: '#cce',
                      lineHeight: 1.5,
                      whiteSpace: 'pre-wrap',
                    }}>
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  /* アシスタントメッセージ */
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxWidth: '90%' }}>
                    {msg.root_cause && (
                      <div style={{ fontSize: 11, color: '#666', fontStyle: 'italic', paddingLeft: 4 }}>
                        {t('askPanel.rootCausePrefix')}{msg.root_cause}
                      </div>
                    )}
                    <div style={{
                      background: '#0d0d14',
                      border: '1px solid #2a2a3a',
                      borderRadius: '10px 10px 10px 2px',
                      padding: '10px 12px',
                      fontSize: 13,
                      color: '#ccc',
                      lineHeight: 1.6,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}>
                      {msg.content}
                    </div>
                    {msg.can_optimize && (msg.dsl_patch?.length ?? 0) > 0 && (
                      <button
                        style={{ ...APPLY_BTN_STYLE, opacity: applyLoading === i ? 0.6 : 1 }}
                        onClick={() => handleApply(msg, i)}
                        disabled={applyLoading !== null}
                      >
                        {applyLoading === i ? t('askPanel.applying') : `✅ ${t('askPanel.applyAndReoptimize')}`}
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* ローディング表示 */}
            {loading && (
              <div style={{ display: 'flex', gap: 4, paddingLeft: 4 }}>
                {[0, 1, 2].map(i => (
                  <div key={i} style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: '#00e5ff',
                    animation: `bounce 1s ease-in-out ${i * 0.15}s infinite`,
                  }} />
                ))}
              </div>
            )}

            {/* エラー表示 */}
            {error && (
              <div style={{ fontSize: 12, color: '#ff6b6b', background: 'rgba(255,100,100,0.1)', padding: '8px 10px', borderRadius: 6 }}>
                ⚠️ {error}
              </div>
            )}

            <div ref={historyEndRef} />
          </div>

          {/* 入力エリア */}
          <div style={INPUT_AREA_STYLE}>
            <textarea
              ref={textareaRef}
              style={TEXTAREA_STYLE}
              placeholder={t('askPanel.inputPlaceholder')}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button
              style={{ ...SEND_BTN_STYLE, opacity: (loading || !question.trim()) ? 0.6 : 1 }}
              onClick={handleSend}
              disabled={loading || !question.trim()}
            >
              {loading ? '…' : t('askPanel.send')}
            </button>
          </div>
        </div>
      )}

      {/* バウンスアニメーション用style */}
      <style>{`
        @keyframes bounce {
          0%, 100% { transform: translateY(0); opacity: 0.4; }
          50% { transform: translateY(-5px); opacity: 1; }
        }
      `}</style>

      {/* フローティングボタン */}
      <button style={FLOAT_BTN_STYLE} onClick={() => setOpen(v => !v)}>
        💬
      </button>
    </div>
  );
}