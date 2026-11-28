import { Brain, CheckCircle2, ClipboardCheck, Code2, FlaskConical, Info, LibraryBig, Loader2, Rocket, ShieldCheck, Stethoscope, XCircle } from 'lucide-react';
import type { FixoraEvent } from '../api/types';

export type AgentKind = 'developer' | 'tester' | 'reviewer' | 'system';

function agentFor(event: FixoraEvent): AgentKind {
  const tagged = String(event.agent || '').toLowerCase();
  if (tagged === 'developer' || tagged === 'tester' || tagged === 'reviewer') return tagged;
  // fallback for events without a server-side agent tag
  const node = String(event.node || event.type || '').toLowerCase();
  if (node.includes('review')) return 'reviewer';
  if (node.includes('test') || node.includes('baseline')) return 'tester';
  if (['analyse', 'plan', 'fix', 'developer'].some((s) => node.includes(s))) return 'developer';
  return 'system';
}

export function AgentMarker({ agent }: { agent: AgentKind }) {
  const common = { className: 'icon', 'aria-hidden': true as const };
  if (agent === 'developer') return <span className="agent-marker agent-developer" title="Developer agent"><Code2 {...common} /></span>;
  if (agent === 'tester') return <span className="agent-marker agent-tester" title="Tester agent"><FlaskConical {...common} /></span>;
  if (agent === 'reviewer') return <span className="agent-marker agent-reviewer" title="Reviewer agent"><ShieldCheck {...common} /></span>;
  return <span className="agent-marker agent-system" title="System"><Info {...common} /></span>;
}

function iconFor(event: FixoraEvent) {
  switch (event.type) {
    case 'start': return <Rocket aria-hidden="true" className="icon" />;
    case 'done': return event.status === 'applied' ? <CheckCircle2 aria-hidden="true" className="icon" /> : <ClipboardCheck aria-hidden="true" className="icon" />;
    case 'awaiting_approval': return <ShieldCheck aria-hidden="true" className="icon" />;
    case 'review_verdict': return event.verdict === 'approve' ? <CheckCircle2 aria-hidden="true" className="icon" /> : <XCircle aria-hidden="true" className="icon" />;
    case 'test_result': return event.status === 'passed' ? <CheckCircle2 aria-hidden="true" className="icon" /> : <XCircle aria-hidden="true" className="icon" />;
    case 'llm_call': return <Brain aria-hidden="true" className="icon" />;
    case 'llm_error': return <XCircle aria-hidden="true" className="icon" />;
    case 'diagnosis': return <Stethoscope aria-hidden="true" className="icon" />;
    case 'retrieval': return <LibraryBig aria-hidden="true" className="icon" />;
    case 'memory': return <LibraryBig aria-hidden="true" className="icon" />;
    default: return <Loader2 aria-hidden="true" className="icon" />;
  }
}

function titleFor(event: FixoraEvent): string {
  switch (event.type) {
    case 'start': return 'Run started';
    case 'step': return String(event.message || 'Step');
    case 'test_result': return `Tests: ${event.status || 'unknown'} (${event.failed ?? 0} failed, ${event.passed ?? 0} passed)`;
    case 'review_verdict': return `Review: ${event.verdict || 'unknown'} risk ${event.risk || 'unknown'}`;
    case 'retry': return `Attempt ${(event.retry_index ?? 1) + 1}`;
    case 'awaiting_approval': return 'Awaiting human approval';
    case 'applied': return 'Changes applied';
    case 'done': return `Run finished: ${String(event.status || 'done')}`;
    case 'llm_call': return 'Model call completed';
    case 'llm_error': return 'Model call failed';
    case 'diagnosis': return `Diagnosis: ${String(event.label || event.category || 'unknown')}`;
    case 'retrieval': return `Retrieval: ${event.chunks?.length ?? 0} chunks (${String(event.backend || 'local')})`;
    case 'memory': return 'Prior knowledge used';
    default: return event.type.replace(/_/g, ' ');
  }
}

function detailFor(event: FixoraEvent): string | null {
  if (event.type === 'start') return `${event.repo || ''}${event.task ? ` — ${event.task}` : ''}`;
  if (typeof event.error === 'string') return event.error;
  if (typeof event.message === 'string' && event.type !== 'step') return event.message;
  if (event.type === 'review_verdict' && typeof event.summary === 'string') return event.summary;
  if (event.type === 'diagnosis') {
    const parts = [typeof event.summary === 'string' ? event.summary : ''];
    if (typeof event.module === 'string') parts.push(`module: ${event.module}`);
    return parts.filter(Boolean).join(' · ') || null;
  }
  if (event.type === 'test_result' && Array.isArray(event.failing_tests) && event.failing_tests.length) return `Failing: ${event.failing_tests.join(', ')}`;
  if (event.type === 'memory' && Array.isArray(event.lessons) && event.lessons.length) {
    return `${event.lessons.length} lesson(s) — hints only, verified independently`;
  }
  return null;
}

function ExtraDetail({ event }: { event: FixoraEvent }) {
  if (event.type === 'retrieval') {
    const chunks = Array.isArray(event.chunks) ? event.chunks : [];
    const queries = Array.isArray(event.queries) ? event.queries.join(' | ') : '';
    return (
      <div className="event-detail">
        {queries && <div className="muted">queries: {queries}</div>}
        <div className="muted">index: {event.chunks_indexed ?? '?'} chunks{typeof event.build_ms === 'number' ? `, built in ${event.build_ms}ms` : ''}{event.rerank_applied ? ' · rerank applied' : ''}</div>
        {chunks.length > 0 && (
          <details>
            <summary>Retrieved code ({chunks.length})</summary>
            <ul className="retrieval-list">
              {chunks.map((c, i) => (
                <li key={i}>
                  <div className="mono retrieval-loc">{c.path}:{c.start_line}-{c.end_line} · {c.kind || 'chunk'} {c.symbol ? `· ${c.symbol}` : ''} · score {c.score ?? '?'}</div>
                  <pre className="retrieval-snippet">{c.snippet}</pre>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    );
  }
  if (event.type === 'memory' && Array.isArray(event.lessons) && event.lessons.length) {
    return (
      <div className="event-detail">
        <ul className="retrieval-list">
          {event.lessons.map((l, i) => (
            <li key={i}>
              <div>{l.symptom}</div>
              <div className="muted">root cause: {l.root_cause} · fix: {l.fix_summary} · outcome: {l.outcome}</div>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return null;
}

export function StepRow({ event }: { event: FixoraEvent }) {
  const agent = agentFor(event);
  const ms = typeof event.duration_ms === 'number' ? `${event.duration_ms.toFixed(0)}ms` : '';
  return (
    <div className="event-row" data-event-type={event.type}>
      <div className="event-time">{event.ts}</div>
      <AgentMarker agent={agent} />
      <div className="event-body">
        <div className="event-title"><span className="event-title-icon" aria-hidden="true">{iconFor(event)}</span><span className="event-title-text">{titleFor(event)}</span></div>
        {detailFor(event) && !(event.type === 'retrieval' || event.type === 'memory') && <div className="event-detail">{detailFor(event)}</div>}
        <ExtraDetail event={event} />
      </div>
      <div className="event-actions mono">{ms}</div>
    </div>
  );
}
