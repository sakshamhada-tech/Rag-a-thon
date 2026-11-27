import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { GitBranch, Play } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { DemoRepo } from '../api/types';
import { ErrorNotice } from './ErrorNotice';

const GITHUB_RE = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/?$/;

interface Props {
  status: string;
  onStart: (payload: { repo?: string; demo_id?: string; task: string }) => Promise<void>;
}

export function RunSetup({ status, onStart }: Props) {
  const [source, setSource] = useState<'demo' | 'github'>('demo');
  const [demoId, setDemoId] = useState('demo_repo_hard');
  const [demos, setDemos] = useState<DemoRepo[]>([]);
  const [repo, setRepo] = useState('');
  const [task, setTask] = useState('Find and fix the failing tests');
  const [demosError, setDemosError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const active = status === 'pending' || status === 'running' || status === 'awaiting_approval';
  const repoValid = GITHUB_RE.test(repo.trim());
  const sourceValid = source === 'demo' ? Boolean(demoId) : repoValid;
  const canSubmit = !active && !submitting && sourceValid && task.trim().length > 0;

  useEffect(() => {
    let cancelled = false;
    api.demos()
      .then((res) => {
        if (cancelled) return;
        setDemos(res.demos);
        if (!res.demos.some((d) => d.id === demoId) && res.demos[0]) setDemoId(res.demos[0].id);
      })
      .catch((error) => {
        if (!cancelled) setDemosError(error instanceof Error ? error.message : 'Could not load demos');
      });
    return () => { cancelled = true; };
  }, [demoId]);

  const hint = useMemo(() => {
    if (source === 'github' && repo.trim() && !repoValid) return 'Use a public HTTPS URL like https://github.com/owner/repo';
    return null;
  }, [repo, repoValid, source]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onStart(source === 'demo' ? { demo_id: demoId, task: task.trim() } : { repo: repo.trim(), task: task.trim() });
    } catch (error) {
      if (error instanceof ApiError) setSubmitError(`${error.status ? `${error.status}: ` : ''}${error.detail}`);
      else setSubmitError(error instanceof Error ? error.message : 'Could not start the run');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="run-setup-title">
      <h2 id="run-setup-title" className="panel-title">Run setup</h2>
      <form onSubmit={handleSubmit}>
        <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">Source</legend>
          <div role="radiogroup" aria-label="Run source" style={{ display: 'grid', gap: 'var(--space-2)' }}>
            <label className="source-option"><input type="radio" name="source" checked={source === 'demo'} onChange={() => setSource('demo')} /> Built-in demo</label>
            <label className="source-option"><input type="radio" name="source" checked={source === 'github'} onChange={() => setSource('github')} /> Public GitHub URL</label>
          </div>
        </fieldset>

        {source === 'demo' ? (
          <div className="field" style={{ marginTop: 'var(--space-4)' }}>
            <label className="label" htmlFor="demo-select">Demo repository</label>
            <select id="demo-select" className="select" value={demoId} onChange={(e) => setDemoId(e.target.value)}>
              {demos.map((demo) => <option key={demo.id} value={demo.id}>{demo.name}</option>)}
            </select>
            <p className="muted" style={{ fontSize: 'var(--text-xs)', margin: 'var(--space-2) 0 0' }}>
              {demos.find((d) => d.id === demoId)?.description || 'Demo definitions come from the server.'}
            </p>
            {demosError && <div className="error-text">{demosError}</div>}
          </div>
        ) : (
          <div className="field" style={{ marginTop: 'var(--space-4)' }}>
            <label className="label" htmlFor="repo-url">GitHub URL</label>
            <div style={{ position: 'relative' }}>
              <GitBranch aria-hidden="true" className="icon" style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-faint)' }} />
              <input id="repo-url" className="input mono" style={{ paddingLeft: 34 }} value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/owner/repo" inputMode="url" autoComplete="off" spellCheck={false} aria-invalid={Boolean(hint)} aria-describedby={hint ? 'repo-hint' : undefined} />
            </div>
            {hint && <div id="repo-hint" className="error-text">{hint}</div>}
          </div>
        )}

        <div className="field">
          <label className="label" htmlFor="task">Task</label>
          <textarea id="task" className="textarea" value={task} onChange={(e) => setTask(e.target.value)} maxLength={2000} />
        </div>

        {submitError && <ErrorNotice message={submitError} />}

        <button type="submit" className="button button-primary" disabled={!canSubmit} style={{ width: '100%', justifyContent: 'center', marginTop: 'var(--space-2)' }}>
          <Play aria-hidden="true" className="icon" />
          {submitting ? 'Starting run' : active ? 'Run active' : 'Start run'}
        </button>
        <p className="muted" style={{ fontSize: 'var(--text-xs)', margin: 'var(--space-2) 0 0' }}>One run at a time. Nothing is applied until you approve it.</p>
      </form>
    </section>
  );
}
