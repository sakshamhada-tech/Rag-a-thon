import { useMemo, useState } from 'react';
import { Copy, Download, ScrollText, Check } from 'lucide-react';
import type { DevmateEvent } from '../api/types';
import { EmptyState } from './EmptyState';

export function LogView({ events }: { events: DevmateEvent[] }) {
  const [copied, setCopied] = useState(false);
  const json = useMemo(() => JSON.stringify(events, null, 2), [events]);

  async function copy() {
    try { await navigator.clipboard.writeText(json); setCopied(true); window.setTimeout(() => setCopied(false), 1200); } catch { setCopied(false); }
  }
  function download() {
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `devmate-${events[0]?.run_id || 'run'}-events.json`; a.click();
    URL.revokeObjectURL(url);
  }

  if (events.length === 0) return <EmptyState icon={ScrollText} title="No events yet" detail="The raw event log appears here once a run starts." />;
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 'var(--space-2)', marginBottom: 'var(--space-2)', flexWrap: 'wrap' }}>
        <button className="button" onClick={copy}>{copied ? <Check aria-hidden="true" className="icon" /> : <Copy aria-hidden="true" className="icon" />} Copy JSON</button>
        <button className="button" onClick={download}><Download aria-hidden="true" className="icon" /> Download JSON</button>
      </div>
      <pre className="code-block" style={{ maxHeight: 'none' }}>{json}</pre>
    </div>
  );
}
