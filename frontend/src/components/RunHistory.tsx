import { History } from 'lucide-react';
import type { RunSummary } from '../api/types';
import { EmptyState } from './EmptyState';
import { StatusChip } from './StatusChip';

interface Props {
  runs: RunSummary[];
  currentRunId: string | null;
  onSelect: (runId: string) => void;
  loading: boolean;
  error: string | null;
}

function shortId(id: string) { return id.slice(0, 8); }
function repoName(repo: string) {
  if (repo.startsWith('demo:')) return repo.replace('demo:', 'demo ');
  const parts = repo.replace(/\/$/, '').split('/');
  return parts.slice(-2).join('/');
}
function formatDuration(s: number) { return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`; }

export function RunHistory({ runs, currentRunId, onSelect, loading, error }: Props) {
  return (
    <section className="run-history-section" aria-labelledby="run-history-title">
      <h2 id="run-history-title" className="panel-title">Run history</h2>
      {error && <div className="error-text" role="status">{error}</div>}
      {loading && runs.length === 0 ? <div className="muted" aria-busy="true">Loading runs…</div> : null}
      {!loading && runs.length === 0 && !error ? (
        <EmptyState icon={History} title="No runs yet" detail="Start one from the setup panel above." />
      ) : (
        <div className="run-history" role="list" aria-label="Previous runs">
          {runs.map((run) => (
            <button key={run.run_id} role="listitem" className="run-item" aria-current={run.run_id === currentRunId} onClick={() => onSelect(run.run_id)}>
              <span className="run-item-name">{repoName(run.repo)}</span>
              <StatusChip status={run.status} />
              <span className="run-item-meta"><span>{shortId(run.run_id)}</span><span>{formatDuration(run.duration_s)}</span><span>{run.created.split('T')[1]?.slice(0, 5) || run.created}</span></span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
