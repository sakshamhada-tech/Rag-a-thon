import { Code2, FlaskConical, ShieldCheck } from 'lucide-react';
import type { FixoraEvent } from '../api/types';

export type AgentState = 'idle' | 'working' | 'done';
type AgentId = 'developer' | 'tester' | 'reviewer';

const AGENTS: Array<{ id: AgentId; name: string; role: string; icon: typeof Code2 }> = [
  { id: 'developer', name: 'Developer', role: 'Analyses the repo, plans the fix, lands the patch', icon: Code2 },
  { id: 'tester', name: 'Tester', role: 'Runs the real pytest suite — before and after every fix', icon: FlaskConical },
  { id: 'reviewer', name: 'Reviewer', role: 'Second opinion on the diff — verdict only, never writes', icon: ShieldCheck },
];

const TERMINAL = new Set(['applied', 'rejected', 'failed', 'error', 'timed_out', 'awaiting_approval']);

function activityFor(event: FixoraEvent | undefined): string {
  if (!event) return '';
  switch (event.type) {
    case 'tool_call': return `${event.tool ?? 'tool'}()`;
    case 'tool_result': return event.cached ? `${event.tool ?? 'tool'} → cached` : `${event.tool ?? 'tool'} done`;
    case 'test_result': {
      const p = event.passed as number | undefined;
      const f = event.failed as number | undefined;
      if (typeof p === 'number') return `pytest: ${p} passed, ${f ?? 0} failed`;
      return `pytest: ${String(event.status ?? 'done')}`;
    }
    case 'diagnosis': return `diagnosis: ${String(event.label ?? event.category ?? '')}`;
    case 'review_verdict': return `verdict: ${String((event as { verdict?: string }).verdict ?? '')}`;
    case 'retrieval': return `retrieved ${(event.chunks ?? []).length} chunks (${event.backend ?? 'rag'})`;
    case 'memory': return `recalled ${(event.lessons ?? []).length} lesson(s)`;
    case 'retry': return 'another attempt';
    case 'step': return String(event.message ?? 'working');
    default: return event.type;
  }
}

export function deriveAgentStatuses(
  events: FixoraEvent[],
  runStatus: string,
): Record<AgentId, { state: AgentState; activity: string }> {
  const terminal = TERMINAL.has(String(runStatus));
  const latest: Partial<Record<AgentId, FixoraEvent>> = {};
  for (const event of events) {
    const agent = event.agent as AgentId | undefined;
    if (agent === 'developer' || agent === 'tester' || agent === 'reviewer') {
      latest[agent] = event;
    }
  }
  // the agent that produced the newest tagged event is the one on duty
  let active: AgentId | null = null;
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const agent = events[i].agent as AgentId | undefined;
    if (agent === 'developer' || agent === 'tester' || agent === 'reviewer') {
      active = agent;
      break;
    }
  }
  const out = {} as Record<AgentId, { state: AgentState; activity: string }>;
  for (const agent of AGENTS) {
    const id = agent.id;
    const event = latest[id];
    let state: AgentState = 'idle';
    if (event) state = terminal || id !== active ? 'done' : 'working';
    out[id] = { state, activity: activityFor(event) };
  }
  return out;
}

/**
 * Live roster of the three agents. Deterministic render from the SSE event
 * stream: the agent behind the newest tagged event is "working", agents that
 * already acted are "done", agents yet to act are "idle" (or all "done" once
 * the run reaches a terminal state).
 */
export function AgentRoster({ events, status }: { events: FixoraEvent[]; status: string }) {
  const statuses = deriveAgentStatuses(events, status);
  return (
    <div className="agents-roster" role="group" aria-label="Fixora agent team">
      <div className="agents-roster-title">
        <span className="mono">fixora</span> agent team
      </div>
      <div className="agents-roster-grid">
        {AGENTS.map((agent) => {
          const { state, activity } = statuses[agent.id];
          const Icon = agent.icon;
          return (
            <div className={`agent-card agent-card-${agent.id} state-${state}`} key={agent.id}
                 title={`${agent.name} — ${agent.role}`}>
              <span className="agent-card-icon" aria-hidden="true"><Icon className="icon" /></span>
              <div className="agent-card-body">
                <div className="agent-card-head">
                  <span className="agent-card-name">{agent.name}</span>
                  <span className={`agent-status status-${state}`}>
                    <span className="agent-status-dot" aria-hidden="true" />
                    {state}
                  </span>
                </div>
                <div className="agent-card-activity">
                  {activity || (state === 'idle' ? agent.role : 'done')}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
