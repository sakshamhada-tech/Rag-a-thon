import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ListChecks, PanelRightOpen, Play, ShieldCheck, TerminalSquare } from 'lucide-react';
import { api } from './api/client';
import { streamRun, type StreamHandle } from './api/sse';
import type { RunStartRequest, RunSummary, ServerConfig } from './api/types';
import { Inspector } from './components/Inspector';
import { RunHistory } from './components/RunHistory';
import { RunSetup } from './components/RunSetup';
import { Timeline } from './components/Timeline';
import { TopBar } from './components/TopBar';
import { useRunState } from './state/RunContext';

export default function App() {
  const { state, dispatch } = useRunState();
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [mobileTab, setMobileTab] = useState<'run' | 'timeline' | 'inspector'>('run');
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const streamRef = useRef<StreamHandle | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const res = await api.runs();
      setRuns(res.runs);
      setRunsError(null);
    } catch (error) {
      setRunsError(error instanceof Error ? error.message : 'Could not load runs');
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    api.config().then((cfg) => { if (mounted) setConfig(cfg); }).catch(() => setConfig(null));
    refreshRuns();
    const interval = window.setInterval(refreshRuns, 5000);
    return () => { mounted = false; window.clearInterval(interval); };
  }, [refreshRuns]);

  const connectToRun = useCallback((runId: string) => {
    streamRef.current?.close();
    setOutcome(null);
    dispatch({ type: 'select_run', runId });
    api.run(runId)
      .then((detail) => dispatch({ type: 'detail_loaded', detail }))
      .catch((error) => dispatch({ type: 'detail_failed', message: error instanceof Error ? error.message : 'Could not load run' }));
    dispatch({ type: 'sse_connecting' });
    streamRef.current = streamRun(
      runId,
      (event) => {
        dispatch({ type: 'event_received', event });
        if (event.type === 'done' || event.type === 'stream_end') {
          streamRef.current?.close();
          api.run(runId).then((detail) => dispatch({ type: 'detail_loaded', detail })).catch(() => undefined);
          refreshRuns();
        }
        if (event.type === 'awaiting_approval') {
          api.run(runId).then((detail) => dispatch({ type: 'detail_loaded', detail })).catch(() => undefined);
          setMobileTab('inspector');
        }
      },
      (message) => dispatch({ type: 'sse_error', message }),
      () => dispatch({ type: 'sse_open' }),
    );
    setMobileTab('timeline');
  }, [dispatch, refreshRuns]);

  useEffect(() => () => streamRef.current?.close(), []);

  async function handleStart(payload: RunStartRequest) {
    const res = await api.startRun(payload);
    await refreshRuns();
    connectToRun(res.run_id);
  }

  async function handleApprove() {
    if (!state.currentRunId) return;
    setApprovalBusy(true);
    setOutcome(null);
    try {
      const res = await api.approveRun(state.currentRunId);
      const appliedTo = typeof res.applied_to === 'string' ? `Applied to ${res.applied_to}.` : '';
      const patch = typeof res.patch_file === 'string' ? 'Patch exported. Use the download link below.' : '';
      setOutcome(appliedTo || patch || 'Approval recorded.');
      const detail = await api.run(state.currentRunId);
      dispatch({ type: 'detail_loaded', detail });
      await refreshRuns();
    } finally {
      setApprovalBusy(false);
    }
  }

  async function handleReject() {
    if (!state.currentRunId) return;
    setApprovalBusy(true);
    setOutcome(null);
    try {
      await api.rejectRun(state.currentRunId);
      setOutcome('Rejected. The workspace was discarded; the original repository was not changed.');
      const detail = await api.run(state.currentRunId);
      dispatch({ type: 'detail_loaded', detail });
      await refreshRuns();
    } finally {
      setApprovalBusy(false);
    }
  }

  const idle = state.status === 'idle' && !state.currentRunId;
  const terminalError = useMemo(() => {
    if (!['failed', 'error', 'timed_out'].includes(String(state.status))) return null;
    return state.detail?.error || state.error || 'Run ended without a passing result. Open the Log tab for details.';
  }, [state.detail?.error, state.error, state.status]);

  return (
    <div className={`app-shell ${idle ? 'idle-grid' : ''} mobile-tab-${mobileTab}`}>
      <TopBar status={state.status} config={config} />
      <main className={`workbench ${inspectorOpen ? '' : 'inspector-closed'}`} aria-label="DevMate workbench">
        <aside className="panel left-panel" aria-label="Run setup and history">
          <RunSetup status={state.status} onStart={handleStart} />
          <hr className="section-divider" />
          <RunHistory runs={runs} currentRunId={state.currentRunId} onSelect={connectToRun} loading={runsLoading} error={runsError} />
        </aside>

        <Timeline events={state.events} status={String(state.status)} sseStatus={state.sseStatus} error={terminalError} />

        {inspectorOpen ? (
          <Inspector open={true} onClose={() => setInspectorOpen(false)} currentRunId={state.currentRunId} status={String(state.status)} detail={state.detail} events={state.events} outcome={outcome} busy={approvalBusy} onApprove={handleApprove} onReject={handleReject} />
        ) : (
          <button className="inspector-reopen" onClick={() => setInspectorOpen(true)} aria-label="Show inspector" title="Show inspector">
            <PanelRightOpen aria-hidden="true" className="icon" />
          </button>
        )}
      </main>

      <nav className="bottom-tabs" aria-label="Mobile sections">
        <button className="bottom-tab" aria-current={mobileTab === 'run'} onClick={() => setMobileTab('run')}><Play aria-hidden="true" className="icon" />Run</button>
        <button className="bottom-tab" aria-current={mobileTab === 'timeline'} onClick={() => setMobileTab('timeline')}><ListChecks aria-hidden="true" className="icon" />Timeline</button>
        <button className="bottom-tab" aria-current={mobileTab === 'inspector'} onClick={() => setMobileTab('inspector')}><TerminalSquare aria-hidden="true" className="icon" />Inspector</button>
      </nav>

      <footer className="app-footer" role="contentinfo" aria-label="DevMate phase 3 status">
        <span className="footer-item footer-primary"><ShieldCheck aria-hidden="true" className="icon" /> DevMate · agent workflow with a human approval gate</span>
        <span className="footer-item footer-right">Sandboxed workspace · nothing is applied until approval · logs stay in <span className="mono">logs/</span></span>
      </footer>
    </div>
  );
}
