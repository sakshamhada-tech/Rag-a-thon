import { Bot } from 'lucide-react';
import type { RunStatus, ServerConfig } from '../api/types';
import { StatusChip } from './StatusChip';
import { ThemeToggle } from '../theme/ThemeToggle';

export function TopBar({ status, config }: { status: RunStatus | 'idle' | string; config: ServerConfig | null }) {
  const provider = config?.provider || 'provider unknown';
  const model = config?.model || 'model unknown';
  return (
    <header className="topbar" role="banner">
      <div className="product" aria-label="Fixora">
        <span className="product-mark" aria-hidden="true">DM</span>
        <span>Fixora</span>
      </div>
      <div className="topbar-right">
        <StatusChip status={status} />
        <span className="provider-pill" title={`Provider: ${provider}; model: ${model}`}>
          <Bot aria-hidden="true" className="icon" />
          <span className="mono">{provider}/{model}</span>
        </span>
        <ThemeToggle />
      </div>
    </header>
  );
}
