import { Moon, Sun } from 'lucide-react';
import { useThemeContext } from './ThemeProvider';

export function ThemeToggle() {
  const { theme, toggleTheme } = useThemeContext();
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      className="icon-button"
      onClick={toggleTheme}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      {theme === 'dark' ? <Sun aria-hidden="true" className="icon" /> : <Moon aria-hidden="true" className="icon" />}
    </button>
  );
}
