import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * 2026-07-24追加: メインの最適化計算（handleRunSolver / handleApplyAndReoptimize）実行中に
 * 表示するオーバーレイ。
 *
 * 背景: これまでは isSolverLoading=true の間、コンテンツ全体を opacity 0.3 に薄暗くする
 * だけで、スピナーも経過時間も出ておらず、ユーザーから「画面が固まっているのか、計算が
 * 進んでいるのか分からない」というフィードバックがあった。バックエンドの solve() は
 * 同期呼び出しで真の進捗率（%）を返さないため、代わりに「スピナーで動作中であることを
 * 明示」+「経過秒数を表示」の2点でフリーズとの区別をつける。
 */
export function SolvingOverlay({ visible }: { visible: boolean }) {
  const { t } = useTranslation();
  const [elapsedSec, setElapsedSec] = useState(0);

  useEffect(() => {
    if (!visible) {
      setElapsedSec(0);
      return;
    }
    setElapsedSec(0);
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
    }, 500);
    return () => window.clearInterval(timer);
  }, [visible]);

  if (!visible) return null;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '14px',
        zIndex: 50,
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: '50%',
          border: '4px solid #2a2a30',
          borderTopColor: '#00e5ff',
          animation: 'opti-solving-spin 0.8s linear infinite',
        }}
      />
      <div style={{ fontSize: '14px', color: '#00e5ff', fontWeight: 700 }}>
        {t('solvingOverlay.title')}
      </div>
      <div style={{ fontSize: '12px', color: '#888' }}>
        {t('solvingOverlay.elapsed', { seconds: elapsedSec })}
      </div>
      <div style={{ fontSize: '11px', color: '#555', maxWidth: '320px', textAlign: 'center' }}>
        {t('solvingOverlay.subtext')}
      </div>

      <style>{`
        @keyframes opti-solving-spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
