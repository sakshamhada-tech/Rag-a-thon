import { useEffect, useMemo, useState } from 'react';
import { FileDiff, FlaskConical, ScrollText, ShieldCheck, X } from 'lucide-react';
import type { DevmateEvent, RunDetail, ReviewVerdict, TestResult } from '../api/types';
import { DiffView } from './DiffView';
import { TestsView } from './TestsView';
import { ReviewView } from './ReviewView';
import { LogView } from './LogView';
import { ApprovalPanel } from './ApprovalPanel';

type Tab = 'diff' | 'tests' | 'review' | 'log';

interface Props {
  open: boolean;
  onClose: () => void;
  currentRunId: string | null;
  status: string;
  detail: RunDetail | null;
  events: DevmateEvent[];
  outcome: string | null;
  busy: boolean;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}

const TABS: { id: Tab; label: string; icon: typeof FileDiff }[] = [
  { id: 'diff', label: 'Diff', icon: FileDiff },
  { id: 'tests', label: 'Tests', icon: FlaskConical },
  { id: 'review', label: 'Review', icon: ShieldCheck },
  { id: 'log', label: 'Log', icon: ScrollText },
];

export function Inspector({ open, onClose, currentRunId, status, detail, events, outcome, busy, onApprove, onReject }: Props) {
  const [tab, setTab] = useState<Tab>('diff');
  useEffect(() => { if (status === 'awaiting_approval') setTab('diff'); }, [status]);

  const testResults = useMemo(() => events.filter((e) => e.type === 'test_result') as unknown as TestResult[], [events]);
  const testsBefore = testResults[0] ?? detail?.tests_before ?? null;
  const testsAfter = testResults.length > 1 ? testResults[testResults.length - 1] : (detail?.tests_after ?? null);
  const latestReview: ReviewVerdict | null = useMemo(() => {
    const event = [...events].reverse().find((e) => e.type === 'review_verdict');
    if (event?.verdict) return {
      verdict: event.verdict as ReviewVerdict['verdict'],
      issues: Array.isArray(event.issues) ? event.issues as string[] : [],
      risk: (event.risk as ReviewVerdict['risk']) || 'medium',
      summary: String(event.summary || ''),
    };
    return detail?.review ?? null;
  }, [events, detail]);
  const eventDiff = [...events].reverse().find((e) => typeof e.diff === 'string')?.diff as string | undefined;
  const diff = detail?.diff || eventDiff || '';

  return (
    <aside className={`panel inspector ${open ? 'open' : ''}`} aria-label="Run inspector">
      <div className="inspector-head">
        <div style={{ minWidth: 0 }}>
          <div className="panel-title" style={{ margin: 0 }}>Inspector</div>
          <div className="mono muted" style={{ fontSize: 'var(--text-xs)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{currentRunId ? currentRunId.slice(0, 12) : 'no run selected'}</div>
        </div>
        <button className="icon-button" onClick={onClose} aria-label="Collapse inspector"><X aria-hidden="true" className="icon" /></button>
      </div>

      <div className="tabs" role="tablist" aria-label="Inspector views">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} id={`tab-${id}`} className="tab" role="tab" aria-selected={tab === id} aria-controls={`panel-${id}`} onClick={() => setTab(id)}>
            <Icon aria-hidden="true" className="icon" style={{ verticalAlign: '-3px', marginRight: 6 }} />{label}
          </button>
        ))}
      </div>

      <ApprovalPanel runId={currentRunId} status={status} diff={diff} tests={testsAfter} review={latestReview} outcome={outcome} patchAvailable={Boolean(detail?.patch_file)} busy={busy} onApprove={onApprove} onReject={onReject} />

      <div className="inspector-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'diff' && <DiffView diff={diff} />}
        {tab === 'tests' && <TestsView before={testsBefore} after={testsAfter} />}
        {tab === 'review' && <ReviewView review={latestReview} />}
        {tab === 'log' && <LogView events={events} />}
      </div>
    </aside>
  );
}
