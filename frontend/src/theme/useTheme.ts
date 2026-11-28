import { useCallback, useEffect, useState } from 'react';
import { flushSync } from 'react-dom';
import { applyTheme, getStoredThemePreference, resolveTheme, storeThemePreference, transitionTheme, type ThemeName } from './themeTransition';

function initialTheme(): ThemeName {
  const attr = document.documentElement.dataset.theme;
  if (attr === 'light' || attr === 'dark') return attr;
  return resolveTheme(getStoredThemePreference());
}

export function useTheme() {
  const [theme, setTheme] = useState<ThemeName>(initialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    const next: ThemeName = theme === 'dark' ? 'light' : 'dark';
    transitionTheme(next, () => {
      // Commit React state and the data-theme attribute together, inside the
      // view-transition callback, so the captured "new" snapshot is complete.
      flushSync(() => setTheme(next));
      applyTheme(next);
      storeThemePreference(next);
    });
  }, [theme]);

  return { theme, toggleTheme };
}
