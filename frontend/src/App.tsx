import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
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


const MIN_LEFT = 248;
const MAX_LEFT = 520;
const MIN_INSPECTOR = 320;
const MAX_INSPECTOR = 640;
const MIN_CENTER = 260;
const HANDLE_PX = 7;
const PANEL_SIZES_KEY = 'fixora-panel-sizes';

function clamp(value: number, min: number, max: number): number {
  const effectiveMax = Math.max(min, max);
  return Math.min(Math.max(value, min), effectiveMax);
}

function readStoredSizes(): { left: number; inspector: number } | null {
  try {
    const raw = localStorage.getItem(PANEL_SIZES_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { left?: unknown; inspector?: unknown };
    const left = typeof parsed.left === 'number' && Number.isFinite(parsed.left) ? parsed.left : null;
    const inspector = typeof parsed.inspector === 'number' && Number.isFinite(parsed.inspector) ? parsed.inspector : null;
    if (left === null || inspector === null) return null;
    return { left: clamp(left, MIN_LEFT, MAX_LEFT), inspector: clamp(inspector, MIN_INSPECTOR, MAX_INSPECTOR) };
  } catch {
    return null;
  }
}

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
  const workbenchRef = useRef<HTMLElement | null>(null);
  const leftSeparatorRef = useRef<HTMLDivElement | null>(null);
  const rightSeparatorRef = useRef<HTMLDivElement | null>(null);
  const sizesRef = useRef({ left: 320, inspector: 420 });
  const dragRef = useRef<{ which: 'left' | 'inspector'; startX: number; startLeft: number; startInspector: number; containerWidth: number } | null>(null);

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

  const maxLeftWidth = useCallback((inspectorWidth: number) => {
    const width = workbenchRef.current?.getBoundingClientRect().width ?? 1280;
    const handles = (inspectorOpen ? 2 : 1) * HANDLE_PX;
    return Math.min(MAX_LEFT, width - MIN_CENTER - (inspectorOpen ? inspectorWidth : 0) - handles);
  }, [inspectorOpen]);

  const maxInspectorWidth = useCallback((leftWidth: number) => {
    const width = workbenchRef.current?.getBoundingClientRect().width ?? 1280;
    return Math.min(MAX_INSPECTOR, width - MIN_CENTER - leftWidth - 2 * HANDLE_PX);
  }, []);

  const applySizes = useCallback((sizes: { left: number; inspector: number }) => {
    sizesRef.current = sizes;
    const wb = workbenchRef.current;
    if (wb) {
      wb.style.setProperty('--left-w', `${Math.round(sizes.left)}px`);
      wb.style.setProperty('--inspector-w', `${Math.round(sizes.inspector)}px`);
    }
    if (leftSeparatorRef.current) {
      leftSeparatorRef.current.setAttribute('aria-valuenow', String(Math.round(sizes.left)));
      leftSeparatorRef.current.setAttribute('aria-valuemax', String(Math.round(maxLeftWidth(sizes.inspector))));
    }
    if (rightSeparatorRef.current) {
      rightSeparatorRef.current.setAttribute('aria-valuenow', String(Math.round(sizes.inspector)));
      rightSeparatorRef.current.setAttribute('aria-valuemax', String(Math.round(maxInspectorWidth(sizes.left))));
    }
  }, [maxInspectorWidth, maxLeftWidth]);

  const persistSizes = useCallback(() => {
    try {
      localStorage.setItem(PANEL_SIZES_KEY, JSON.stringify({ left: Math.round(sizesRef.current.left), inspector: Math.round(sizesRef.current.inspector) }));
    } catch {
      // Panel sizes are best-effort; defaults are fine when storage is unavailable.
    }
  }, []);

  useEffect(() => {
    applySizes(readStoredSizes() ?? sizesRef.current);
  }, [applySizes]);

  // When the inspector closes/opens, clamp the stored left width against the new geometry.
  useEffect(() => {
    const current = sizesRef.current;
    applySizes({ ...current, left: clamp(current.left, MIN_LEFT, maxLeftWidth(current.inspector)) });
    persistSizes();
  }, [inspectorOpen, applySizes, maxLeftWidth, persistSizes]);

  const handleSeparatorKeyDown = useCallback((which: 'left' | 'inspector') => (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const step = event.shiftKey ? 48 : 16;
    const dir = event.key === 'ArrowRight' ? 1 : -1;
    const current = sizesRef.current;
    if (which === 'left') {
      applySizes({ ...current, left: clamp(current.left + dir * step, MIN_LEFT, maxLeftWidth(current.inspector)) });
    } else {
      // Right separator: moving it left grows the inspector.
      applySizes({ ...current, inspector: clamp(current.inspector - dir * step, MIN_INSPECTOR, maxInspectorWidth(current.left)) });
    }
    persistSizes();
  }, [applySizes, maxInspectorWidth, maxLeftWidth, persistSizes]);

  const handleSeparatorPointerDown = useCallback((which: 'left' | 'inspector') => (event: PointerEvent<HTMLDivElement>) => {
    const wb = workbenchRef.current;
    if (!wb) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      which,
      startX: event.clientX,
      startLeft: sizesRef.current.left,
      startInspector: sizesRef.current.inspector,
      containerWidth: wb.getBoundingClientRect().width,
    };
    document.documentElement.classList.add('resizing-panels');
  }, []);

  const handleSeparatorPointerMove = useCallback((which: 'left' | 'inspector') => (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.which !== which) return;
    const dx = event.clientX - drag.startX;
    if (drag.which === 'left') {
      const handles = (inspectorOpen ? 2 : 1) * HANDLE_PX;
      const maxL = drag.containerWidth - MIN_CENTER - (inspectorOpen ? drag.startInspector : 0) - handles;
      applySizes({ ...sizesRef.current, left: clamp(drag.startLeft + dx, MIN_LEFT, maxL) });
    } else {
      const maxI = drag.containerWidth - MIN_CENTER - drag.startLeft - 2 * HANDLE_PX;
      applySizes({ ...sizesRef.current, inspector: clamp(drag.startInspector - dx, MIN_INSPECTOR, maxI) });
    }
  }, [applySizes, inspectorOpen]);

  const handleSeparatorPointerEnd = useCallback(() => {
    if (!dragRef.current) return;
    dragRef.current = null;
    document.documentElement.classList.remove('resizing-panels');
    persistSizes();
  }, [persistSizes]);

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
      <main className={`workbench ${inspectorOpen ? '' : 'inspector-closed'}`} ref={workbenchRef} aria-label="Fixora workbench">
        <aside className="panel left-panel" aria-label="Run setup and history">
          <RunSetup status={state.status} onStart={handleStart} />
          <hr className="section-divider" />
          <RunHistory runs={runs} currentRunId={state.currentRunId} onSelect={connectToRun} loading={runsLoading} error={runsError} />
        </aside>

        <div
          ref={leftSeparatorRef}
          className="panel-separator"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize setup and timeline"
          aria-valuemin={MIN_LEFT}
          aria-valuemax={MAX_LEFT}
          aria-valuenow={320}
          tabIndex={0}
          onPointerDown={handleSeparatorPointerDown('left')}
          onPointerMove={handleSeparatorPointerMove('left')}
          onPointerUp={handleSeparatorPointerEnd}
          onPointerCancel={handleSeparatorPointerEnd}
          onKeyDown={handleSeparatorKeyDown('left')}
        />

        <Timeline events={state.events} status={String(state.status)} sseStatus={state.sseStatus} error={terminalError} />

        {inspectorOpen ? (
          <>
            <div
              ref={rightSeparatorRef}
              className="panel-separator panel-separator-right"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize timeline and inspector"
              aria-valuemin={MIN_INSPECTOR}
              aria-valuemax={MAX_INSPECTOR}
              aria-valuenow={420}
              tabIndex={0}
              onPointerDown={handleSeparatorPointerDown('inspector')}
              onPointerMove={handleSeparatorPointerMove('inspector')}
              onPointerUp={handleSeparatorPointerEnd}
              onPointerCancel={handleSeparatorPointerEnd}
              onKeyDown={handleSeparatorKeyDown('inspector')}
            />
            <Inspector open={true} onClose={() => setInspectorOpen(false)} currentRunId={state.currentRunId} status={String(state.status)} detail={state.detail} events={state.events} outcome={outcome} busy={approvalBusy} onApprove={handleApprove} onReject={handleReject} />
          </>
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

      <footer className="app-footer" role="contentinfo" aria-label="Fixora phase 3 status">
        <span className="footer-item footer-primary"><ShieldCheck aria-hidden="true" className="icon" /> Fixora · agent workflow with a human approval gate</span>
        <span className="footer-item footer-right">Sandboxed workspace · nothing is applied until approval · logs stay in <span className="mono">logs/</span></span>
      </footer>
    </div>
  );
}
