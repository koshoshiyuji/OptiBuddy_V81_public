// Frontend/src/i18n/index.ts
//
// i18n初期化。react-i18next + i18next。
// 対応言語: ja(デフォルト) / en。選択言語はlocalStorageに保存し次回起動時も維持する。
// 対象はFrontend静的UI文言（2026-07-13 Koshoshiさんとの合意によりPhase 1）。
// Backend側が動的生成するメッセージ（issue/KPIラベル/検証エラー等）は、
// 2026-07-30時点でNurseShiftWeeklyCapドメインのみPhase 2対応済み
// （DESIGN_2026-07-29_nurse_shift_i18n_implementation_spec.md）。
// 実装方式はFrontend側のen.json/ja.jsonキーではなく、Backend側の
// 辞書引き+テンプレート復元（Backend/i18n/*_messages.py）。ここで選択された
// 言語は useStudioState.ts の withLangParam() 経由で /baseline へ
// ?lang=en|ja として送られる。他6ドメイン（TruckDispatcher等）は引き続き
// 未対応で、動的メッセージは日本語のまま表示される。
// 唯一の例外: night_shift/is_night列のbool→絵文字表示はFrontend側
// （GenericResultTable.tsx）でこのファイルのen.json/ja.jsonを使って解決する
// （genericResultTable.nightShift.*キー）。

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en.json';
import ja from './locales/ja.json';

export const LANGUAGE_STORAGE_KEY = 'optibuddy_lang';

function getInitialLanguage(): 'ja' | 'en' {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (stored === 'ja' || stored === 'en') return stored;
  } catch {
    // localStorage不可の環境（プライベートモード等）ではデフォルトにフォールバック
  }
  return 'ja';
}

i18n
  .use(initReactI18next)
  .init({
    resources: {
      ja: { translation: ja },
      en: { translation: en },
    },
    lng: getInitialLanguage(),
    fallbackLng: 'ja',
    interpolation: {
      escapeValue: false,
    },
  });

export function setLanguage(lang: 'ja' | 'en'): void {
  i18n.changeLanguage(lang);
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, lang);
  } catch {
    // ignore
  }
}

export default i18n;
