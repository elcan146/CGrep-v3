import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import translations from './translations.json';

const STORAGE_KEY = 'cgrep-lang';
const DEFAULT_LANG = 'en';
const SUPPORTED = ['en', 'az'];

const LanguageContext = createContext(null);

function getNestedValue(obj, path) {
  return path.split('.').reduce((acc, key) => (acc && acc[key] !== undefined ? acc[key] : undefined), obj);
}

export function LanguageProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && SUPPORTED.includes(stored)) return stored;
    } catch {}
    return DEFAULT_LANG;
  });

  const setLang = useCallback((next) => {
    const value = SUPPORTED.includes(next) ? next : DEFAULT_LANG;
    try { localStorage.setItem(STORAGE_KEY, value); } catch {}
    setLangState(value);
  }, []);

  const toggleLanguage = useCallback(() => {
    setLang(lang === 'en' ? 'az' : 'en');
  }, [lang, setLang]);

  const t = useCallback(
    (key, params = {}) => {
      const raw = getNestedValue(translations[lang], key) ?? getNestedValue(translations[DEFAULT_LANG], key) ?? key;
      if (typeof raw !== 'string' || Object.keys(params).length === 0) return raw;
      return raw.replace(/\{([a-zA-Z0-9_]+)\}/g, (_, name) => (params[name] !== undefined ? params[name] : `{${name}}`));
    },
    [lang]
  );

  const value = useMemo(
    () => ({ lang, setLang, toggleLanguage, t, languages: SUPPORTED }),
    [lang, setLang, toggleLanguage, t]
  );

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useTranslation() {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error('useTranslation must be used inside LanguageProvider');
  return ctx;
}
