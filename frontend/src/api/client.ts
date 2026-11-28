import type { DemoRepo, RunDetail, RunStartRequest, RunStartResponse, RunSummary, ServerConfig } from './types';

// API base URL. Empty = same origin (local dev / Docker / FastAPI-served UI).
// Set VITE_API_BASE at build time (e.g. on Vercel) to point the UI at a
// separately hosted Fixora backend.
const API_BASE = ((import.meta.env?.VITE_API_BASE as string | undefined) ?? '').replace(/\/+$/, '');

function url(path: string): string {
  return `${API_BASE}${path}`;
}

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function parseDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg || String(d)).join('; ');
    return JSON.stringify(body);
  } catch {
    return response.statusText || `Request failed with ${response.status}`;
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url(path), {
      ...init,
      headers: {
        'content-type': 'application/json',
        ...(init?.headers || {}),
      },
    });
  } catch (error) {
    throw new ApiError(0, error instanceof Error ? error.message : 'Network request failed');
  }
  if (!response.ok) {
    throw new ApiError(response.status, await parseDetail(response));
  }
  return response.json() as Promise<T>;
}

export const api = {
  async health(): Promise<{ ok: boolean }> {
    return fetchJson('/health');
  },
  async config(): Promise<ServerConfig> {
    return fetchJson('/config');
  },
  async demos(): Promise<{ demos: DemoRepo[] }> {
    return fetchJson('/demos');
  },
  async runs(): Promise<{ runs: RunSummary[] }> {
    return fetchJson('/runs');
  },
  async run(runId: string): Promise<RunDetail> {
    return fetchJson(`/runs/${encodeURIComponent(runId)}`);
  },
  async startRun(payload: RunStartRequest): Promise<RunStartResponse> {
    return fetchJson('/runs', { method: 'POST', body: JSON.stringify(payload) });
  },
  async resumeRun(runId: string): Promise<{ run_id: string; status: string }> {
    return fetchJson(`/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST', body: '{}' });
  },
  async approveRun(runId: string): Promise<Record<string, unknown>> {
    return fetchJson(`/runs/${encodeURIComponent(runId)}/approve`, { method: 'POST', body: '{}' });
  },
  async rejectRun(runId: string): Promise<Record<string, unknown>> {
    return fetchJson(`/runs/${encodeURIComponent(runId)}/reject`, { method: 'POST', body: '{}' });
  },
};

export function patchUrl(runId: string): string {
  return url(`/runs/${encodeURIComponent(runId)}/patch`);
}
