import { useState } from 'react';
import { ChevronDown, ChevronRight, Wrench } from 'lucide-react';
import type { DevmateEvent } from '../api/types';

function shortArgs(args: unknown): string {
  if (args == null) return '{}';
  try {
    const text = JSON.stringify(args);
    return text.length > 120 ? `${text.slice(0, 117)}…` : text;
  } catch {
    return String(args);
  }
}

function pretty(value: unknown): string {
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

export function ToolCallRow({ call, result }: { call: DevmateEvent; result?: DevmateEvent }) {
  const [open, setOpen] = useState(false);
  const tool = String(call.tool || 'tool');
  const cached = Boolean(call.cached || result?.cached);
  const panelId = `tool-${String(call.seq ?? 'x')}-detail`;
  return (
    <div className="tool-row">
      <button className="tool-summary" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-controls={panelId}>
        <span aria-hidden="true">{open ? <ChevronDown className="icon" /> : <ChevronRight className="icon" />}</span>
        <span><span className="tool-name">{tool}</span>{cached && <span className="cached-badge" style={{ marginLeft: 8 }}>cached</span>}<br /><span className="tool-args">{shortArgs(call.args)}</span></span>
        <span className="mono" style={{ color: 'var(--text-faint)', fontSize: 'var(--text-xs)' }}>{typeof result?.duration_ms === 'number' ? `${result.duration_ms.toFixed(0)}ms` : ''}</span>
        <Wrench aria-hidden="true" className="icon" style={{ color: 'var(--text-faint)' }} />
      </button>
      {open && (
        <div className="tool-detail" id={panelId}>
          <div className="label">Arguments</div>
          <pre className="code-block">{pretty(call.args)}</pre>
          <div className="label" style={{ marginTop: 'var(--space-3)' }}>Result</div>
          <pre className="code-block">{result ? pretty(result.result ?? '') : 'No result yet.'}</pre>
        </div>
      )}
    </div>
  );
}
