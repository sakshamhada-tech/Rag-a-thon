import { useCallback, useEffect, useState } from 'react';
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
    setTheme((current) => {
      const next: ThemeName = current === 'dark' ? 'light' : 'dark';
      transitionTheme(next, () => {
        applyTheme(next);
        storeThemePreference(next);
      });
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
