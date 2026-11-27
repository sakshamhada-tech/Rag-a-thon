import { ShieldCheck } from 'lucide-react';
import type { ReviewVerdict } from '../api/types';
import { EmptyState } from './EmptyState';

export function ReviewView({ review }: { review?: ReviewVerdict | null }) {
  if (!review) return <EmptyState icon={ShieldCheck} title="No review yet" detail="The reviewer runs after tests pass. Verdict, risk and issues will appear here." />;
  const approved = review.verdict === 'approve';
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--space-3)' }}>
        <span className={`verdict ${approved ? 'verdict-approve' : 'verdict-reject'}`}>{review.verdict}</span>
        <span className={`mono risk-${review.risk}`} style={{ textTransform: 'uppercase', fontSize: 'var(--text-xs)', letterSpacing: '0.06em' }}>risk: {review.risk}</span>
      </div>
      <p style={{ marginTop: 'var(--space-4)' }}>{review.summary}</p>
      <div className="label">Issues ({review.issues.length})</div>
      {review.issues.length === 0 ? <div className="notice">No issues listed by the reviewer.</div> : <ul className="issue-list">{review.issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>}
    </div>
  );
}
