import type { FixoraEvent, ReviewVerdict, RunDetail, RunStatus, TestResult } from '../api/types';

export interface RunState {
  currentRunId: string | null;
  status: RunStatus | 'idle';
  repo: string;
  task: string;
  events: FixoraEvent[];
  detail: RunDetail | null;
  tests: TestResult[];
  reviews: ReviewVerdict[];
  diff: string;
  error: string | null;
  sseStatus: 'idle' | 'connecting' | 'live' | 'disconnected';
}

export const initialRunState: RunState = {
  currentRunId: null,
  status: 'idle',
  repo: '',
  task: '',
  events: [],
  detail: null,
  tests: [],
  reviews: [],
  diff: '',
  error: null,
  sseStatus: 'idle',
};

export type RunAction =
  | { type: 'reset' }
  | { type: 'select_run'; runId: string }
  | { type: 'detail_loaded'; detail: RunDetail }
  | { type: 'detail_failed'; message: string }
  | { type: 'event_received'; event: FixoraEvent }
  | { type: 'sse_connecting' }
  | { type: 'sse_open' }
  | { type: 'sse_error'; message: string };

function statusFromEvent(event: FixoraEvent, fallback: RunState['status']): RunState['status'] {
  if (event.type === 'start') return 'running';
  if (event.type === 'awaiting_approval') return 'awaiting_approval';
  if (event.type === 'applied') return 'applied';
  if (event.type === 'done') return (event.status as RunStatus) || fallback;
  return fallback;
}

function mergeEvent(events: FixoraEvent[], event: FixoraEvent): FixoraEvent[] {
  if (event.seq != null) {
    const index = events.findIndex((e) => e.seq === event.seq && e.type === event.type);
    if (index >= 0) {
      const next = events.slice();
      next[index] = event;
      return next;
    }
  } else {
    const key = JSON.stringify(event);
    if (events.some((e) => JSON.stringify(e) === key)) return events;
  }
  return [...events, event].sort((a, b) => (a.seq ?? Number.MAX_SAFE_INTEGER) - (b.seq ?? Number.MAX_SAFE_INTEGER));
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'reset':
      return initialRunState;
    case 'select_run':
      return {
        ...initialRunState,
        currentRunId: action.runId,
        status: 'pending',
        sseStatus: 'connecting',
      };
    case 'detail_loaded': {
      const d = action.detail;
      return {
        ...state,
        currentRunId: d.run_id,
        status: (d.status as RunStatus) || state.status,
        repo: d.repo || state.repo,
        task: d.task || state.task,
        detail: d,
        diff: d.diff || state.diff,
        error: d.error || null,
        tests: d.tests_after ? [d.tests_after] : state.tests,
        reviews: d.review ? [d.review] : state.reviews,
      };
    }
    case 'detail_failed':
      return { ...state, error: action.message };
    case 'event_received': {
      const event = action.event;
      const events = mergeEvent(state.events, event);
      const tests = event.type === 'test_result'
        ? [...state.tests, event as unknown as TestResult]
        : state.tests;
      const reviews = event.type === 'review_verdict' && event.verdict
        ? [...state.reviews, {
            verdict: event.verdict as ReviewVerdict['verdict'],
            issues: Array.isArray(event.issues) ? event.issues as string[] : [],
            risk: (event.risk as ReviewVerdict['risk']) || 'medium',
            summary: String(event.summary || ''),
          }]
        : state.reviews;
      const diff = typeof event.diff === 'string' && event.diff ? event.diff : state.diff;
      return {
        ...state,
        events,
        tests,
        reviews,
        diff,
        status: statusFromEvent(event, state.status),
        repo: event.type === 'start' && typeof event.repo === 'string' ? event.repo : state.repo,
        task: event.type === 'start' && typeof event.task === 'string' ? event.task : state.task,
        error: typeof event.error === 'string' ? event.error : state.error,
      };
    }
    case 'sse_connecting':
      return { ...state, sseStatus: 'connecting' };
    case 'sse_open':
      return { ...state, sseStatus: 'live', error: null };
    case 'sse_error':
      return { ...state, sseStatus: 'disconnected', error: action.message };
    default:
      return state;
  }
}
