import { useState } from 'react';
import { Check, ShieldAlert, X } from 'lucide-react';
import type { ReviewVerdict, TestResult } from '../api/types';
import { patchUrl } from '../api/client';

interface Props {
  runId: string | null;
  status: string;
  diff: string;
  tests?: TestResult | null;
  review?: ReviewVerdict | null;
  outcome?: string | null;
  patchAvailable?: boolean;
  busy: boolean;
  onApprove: () => Promise<void>;
  onReject: () => Promise<void>;
}

function diffSummary(diff: string) {
  const files = new Set<string>();
  let adds = 0; let dels = 0;
  for (const line of diff.split('\n')) {
    if (line.startsWith('+++ b/')) files.add(line.replace('+++ b/', ''));
    else if (line.startsWith('+') && !line.startsWith('+++')) adds += 1;
    else if (line.startsWith('-') && !line.startsWith('---')) dels += 1;
  }
  return { files: files.size, adds, dels };
}

export function ApprovalPanel({ runId, status, diff, tests, review, outcome, patchAvailable, busy, onApprove, onReject }: Props) {
  const [confirm, setConfirm] = useState<'approve' | 'reject' | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  if (status !== 'awaiting_approval' && !outcome) return null;
  const summary = diffSummary(diff);

  async function runConfirmed() {
    setActionError(null);
    try {
      if (confirm === 'approve') await onApprove();
      else if (confirm === 'reject') await onReject();
      setConfirm(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Action failed');
    }
  }

  return (
    <section className="approval-panel" aria-labelledby="approval-title" role="region">
      <div className="approval-title" id="approval-title"><ShieldAlert aria-hidden="true" className="icon" /> Human approval required</div>
      {outcome ? (
        <div>
          <p style={{ margin: 0 }}>{outcome}</p>
          {runId && status === 'applied' && patchAvailable && (
            <p style={{ marginBottom: 0 }}><a className="button" href={patchUrl(runId)}>Download patch</a></p>
          )}
        </div>
      ) : (
        <>
          <p style={{ margin: '0 0 var(--space-2)' }}>Diff: {summary.files} file{summary.files === 1 ? '' : 's'} · +{summary.adds} −{summary.dels}. Tests: {tests ? `${tests.passed ?? 0} passed, ${tests.failed ?? 0} failed` : 'no test data'}. Review: {review ? `${review.verdict} (risk ${review.risk})` : 'no verdict'}.</p>
          <div className="approval-actions">
            <button className="button button-primary" disabled={busy} onClick={() => setConfirm('approve')}><Check aria-hidden="true" className="icon" /> Approve and apply</button>
            <button className="button button-danger" disabled={busy} onClick={() => setConfirm('reject')}><X aria-hidden="true" className="icon" /> Reject</button>
          </div>
          {confirm && (
            <div className="confirm-box" role="alertdialog" aria-labelledby="confirm-title" aria-describedby="confirm-desc">
              <div id="confirm-title" style={{ fontWeight: 700 }}>Confirm {confirm}</div>
              <p id="confirm-desc" className="muted">{confirm === 'approve' ? 'This consequential action applies the reviewed diff to a local target, or exports a patch for demo/GitHub runs.' : 'The workspace will be discarded and the original repository will remain unchanged.'}</p>
              <div className="approval-actions">
                <button className="button button-primary" disabled={busy} onClick={runConfirmed}>Confirm {confirm}</button>
                <button className="button" disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
              </div>
            </div>
          )}
          {actionError && <div className="error-text" role="alert">{actionError}</div>}
        </>
      )}
    </section>
  );
}
