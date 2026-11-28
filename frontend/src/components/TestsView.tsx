import { FlaskConical } from 'lucide-react';
import type { TestResult } from '../api/types';
import { EmptyState } from './EmptyState';

function CountBox({ label, value, tone }: { label: string; value?: number; tone?: 'good' | 'bad' }) {
  const color = tone === 'good' ? 'var(--success)' : tone === 'bad' ? 'var(--danger)' : 'var(--text)';
  return <div className="count-box"><div className="count-num" style={{ color }}>{value ?? '—'}</div><div className="muted" style={{ fontSize: 'var(--text-xs)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div></div>;
}

function ResultBlock({ title, result }: { title: string; result?: TestResult | null }) {
  if (!result) return <div className="notice">{title}: no result recorded.</div>;
  return (
    <section aria-label={title} style={{ marginBottom: 'var(--space-4)' }}>
      <h3 style={{ fontSize: 'var(--text-md)', margin: '0 0 var(--space-2)' }}>{title}</h3>
      <div className="test-counts">
        <CountBox label="passed" value={result.passed} tone="good" />
        <CountBox label="failed" value={result.failed} tone={(result.failed ?? 0) > 0 ? 'bad' : undefined} />
        <CountBox label="errors" value={result.errors} tone={(result.errors ?? 0) > 0 ? 'bad' : undefined} />
      </div>
      <div className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>status: {result.status ?? 'unknown'}</div>
      {!!result.failing_tests?.length && (
        <div style={{ marginTop: 'var(--space-2)' }}>
          <div className="label">Failing tests</div>
          <ul className="issue-list mono" style={{ color: 'var(--danger)' }}>
            {result.failing_tests.map((test) => <li key={test}>{test}</li>)}
          </ul>
        </div>
      )}
      {result.output_tail && <details style={{ marginTop: 'var(--space-2)' }}><summary>Test output tail</summary><pre className="code-block">{result.output_tail}</pre></details>}
    </section>
  );
}

export function TestsView({ before, after }: { before?: TestResult | null; after?: TestResult | null }) {
  if (!before && !after) return <EmptyState icon={FlaskConical} title="No test results yet" detail="The tester runs before edits and after each fix attempt. Counts and failing tests will appear here." />;
  return (
    <div>
      <ResultBlock title="Before edits" result={before} />
      <ResultBlock title="After latest attempt" result={after} />
    </div>
  );
}
