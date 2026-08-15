// Frontend/src/i18n/LanguageToggle.tsx
//
// JA/EN切替トグルボタン。StudioTopBarとHomeScreenNewの両方から共通利用する。

import { useTranslation } from 'react-i18next';
import { setLanguage } from './index';

export default function LanguageToggle({ className }: { className?: string }) {
  const { i18n } = useTranslation();
  const current = i18n.language === 'en' ? 'en' : 'ja';

  const toggle = () => {
    setLanguage(current === 'ja' ? 'en' : 'ja');
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className={className}
      title={current === 'ja' ? 'Switch to English' : '日本語に切り替え'}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 10px',
        borderRadius: 6,
        border: '1px solid #d0d5dd',
        background: '#fff',
        fontSize: 12,
        fontWeight: 600,
        cursor: 'pointer',
        color: '#344054',
      }}
    >
      <span style={{ opacity: current === 'ja' ? 1 : 0.4 }}>JA</span>
      <span style={{ opacity: 0.3 }}>/</span>
      <span style={{ opacity: current === 'en' ? 1 : 0.4 }}>EN</span>
    </button>
  );
}
