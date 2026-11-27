export type ThemeName = 'light' | 'dark';

const THEME_KEY = 'fixora-theme';
const LEGACY_THEME_KEY = 'devmate-theme'; // pre-rename key; read-only fallback
const runningTransitions = new WeakSet<Document>();

export function getStoredThemePreference(): 'light' | 'dark' | 'system' {
  try {
    const value = localStorage.getItem(THEME_KEY) ?? localStorage.getItem(LEGACY_THEME_KEY);
    return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
  } catch {
    return 'system';
  }
}

export function storeThemePreference(value: 'light' | 'dark' | 'system') {
  try {
    localStorage.setItem(THEME_KEY, value);
  } catch {
    // Persistence is best-effort; the in-memory theme still changes.
  }
}

export function resolveTheme(preference: 'light' | 'dark' | 'system'): ThemeName {
  if (preference !== 'system') return preference;
  try {
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function applyTheme(theme: ThemeName) {
  document.documentElement.dataset.theme = theme;
}

export function transitionTheme(next: ThemeName, update: () => void) {
  const doc = document;
  const prefersReduced = (() => {
    try { return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false; } catch { return false; }
  })();
  const startViewTransition = (doc as Document & {
    startViewTransition?: (cb: () => void) => { finished: Promise<void>; ready: Promise<void>; };
  }).startViewTransition;

  if (!startViewTransition || prefersReduced) {
    update();
    return;
  }
  if (runningTransitions.has(doc)) return;

  const current = doc.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  if (current === next) {
    update();
    return;
  }

  doc.documentElement.dataset.themeTransition = next === 'dark' ? 'to-dark' : 'to-light';
  runningTransitions.add(doc);
  const transition = startViewTransition.call(doc, () => {
    update();
  });
  transition.finished.finally(() => {
    runningTransitions.delete(doc);
    delete doc.documentElement.dataset.themeTransition;
  });
}
