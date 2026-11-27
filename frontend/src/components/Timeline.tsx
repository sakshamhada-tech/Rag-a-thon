import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, Radio } from 'lucide-react';
import type { DevmateEvent } from '../api/types';
import { EmptyState } from './EmptyState';
import { StepRow } from './StepRow';
import { ToolCallRow } from './ToolCallRow';

interface Props {
  events: DevmateEvent[];
  status: string;
  sseStatus: 'idle' | 'connecting' | 'live' | 'disconnected';
  error?: string | null;
}

interface RenderItem {
  key: string;
  kind: 'event' | 'tool';
  event: DevmateEvent;
  result?: DevmateEvent;
}

function buildItems(events: DevmateEvent[]): RenderItem[] {
  const items: RenderItem[] = [];
  const used = new Set<number>();
  for (let i = 0; i < events.length; i += 1) {
    if (used.has(i)) continue;
    const event = events[i];
    if (event.type === 'tool_call') {
      let result: DevmateEvent | undefined;
      for (let j = i + 1; j < events.length; j += 1) {
        const candidate = events[j];
        if (candidate.type === 'tool_result' && candidate.tool === event.tool) {
          result = candidate;
          used.add(j);
          break;
        }
        if (candidate.type === 'tool_call') break;
      }
      items.push({ key: `tool-${event.seq ?? i}`, kind: 'tool', event, result });
      continue;
    }
    if (event.type === 'tool_result') continue;
    items.push({ key: `event-${event.seq ?? i}`, kind: 'event', event });
  }
  return items;
}

export function Timeline({ events, status, sseStatus, error }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const items = useMemo(() => buildItems(events), [events]);
  const live = status === 'running' || status === 'pending';

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || userScrolledUp) return;
    node.scrollTop = node.scrollHeight;
  }, [events.length, userScrolledUp]);

  function handleScroll() {
    const node = scrollRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    setUserScrolledUp(distance > 90);
  }

  function jumpToLatest() {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    setUserScrolledUp(false);
  }

  return (
    <section className="panel center-panel" aria-labelledby="timeline-title">
      <div className="timeline-header">
        <div>
          <h1 id="timeline-title" style={{ fontSize: 'var(--text-lg)', margin: 0 }}>Execution timeline</h1>
          <div className="muted mono" aria-live="polite" style={{ fontSize: 'var(--text-xs)' }}>
            {sseStatus === 'live' ? 'live' : sseStatus} · {events.length} events
          </div>
        </div>
        {live && <span className="status-chip status-running"><Radio aria-hidden="true" className="icon" /> running</span>}
      </div>

      <div ref={scrollRef} className="timeline-scroll" onScroll={handleScroll} tabIndex={0} aria-label="Run event timeline">
        {error && <div className="inline-notice" role="alert" style={{ borderLeftColor: 'var(--danger)', color: 'var(--danger)', marginBottom: 'var(--space-3)' }}>{error}</div>}
        {items.length === 0 ? (
          <EmptyState icon={Radio} title="No run selected" detail="Start a run from the setup panel. Steps, tool calls and review verdicts will appear here live." />
        ) : (
          <div className="timeline">
            {items.map((item) => {
              if (item.kind === 'tool') {
                return (
                  <div className="event-row" key={item.key}>
                    <div className="event-time">{item.event.ts}</div>
                    <span className="agent-marker agent-system" aria-hidden="true"><span className="mono" style={{ fontSize: 10 }}>tool</span></span>
                    <div className="event-body"><ToolCallRow call={item.event} result={item.result} /></div>
                    <div className="event-actions" />
                  </div>
                );
              }
              const event = item.event;
              if (event.type === 'retry') {
                return <div key={item.key} className="retry-row" role="note">Attempt {(event.retry_index ?? 0) + 2}: reviewer or tests requested another pass.</div>;
              }
              if (event.type === 'step' && String(event.node) === 'llm' && String(event.message || '').toLowerCase().includes('provider')) {
                return <div key={item.key} className="inline-notice" role="note">{event.message}</div>;
              }
              return <StepRow key={item.key} event={event} />;
            })}
          </div>
        )}
      </div>

      {userScrolledUp && (
        <button className="button jump-latest" onClick={jumpToLatest}>
          <ArrowDown aria-hidden="true" className="icon" /> Jump to latest
        </button>
      )}
    </section>
  );
}
