export type RunStatus = 'pending' | 'running' | 'awaiting_approval' | 'applied' | 'rejected' | 'failed' | 'error' | 'timed_out';

export interface FixoraEvent {
  seq?: number;
  ts: string;
  run_id: string;
  type: string;
  node?: string;
  message?: string;
  tool?: string;
  args?: unknown;
  result?: string;
  cached?: boolean;
  duration_ms?: number;
  status?: RunStatus | string;
  repo?: string;
  task?: string;
  is_github?: boolean;
  attempt?: number;
  retry_index?: number;
  error?: string;
  review?: ReviewVerdict;
  diff?: string;
  category?: string;
  label?: string;
  summary?: string;
  module?: string | null;
  exceptions?: string[];
  patch_file?: string;
  applied_to?: string;
  queries?: string[];
  backend?: string;
  chunks_indexed?: number;
  build_ms?: number;
  rerank_applied?: boolean;
  chunks?: RetrievalChunk[];
  applied?: boolean;
  lessons?: MemoryLesson[];
  [key: string]: unknown;
}

export interface RetrievalChunk {
  path: string;
  symbol?: string;
  kind?: string;
  start_line?: number;
  end_line?: number;
  score?: number;
  snippet?: string;
}

export interface MemoryLesson {
  symptom?: string;
  root_cause?: string;
  fix_summary?: string;
  outcome?: string;
  score?: number;
}

export interface TestResult {
  status?: string;
  passed?: number;
  failed?: number;
  errors?: number;
  failing_tests?: string[];
  output_tail?: string;
}

export interface ReviewVerdict {
  verdict: 'approve' | 'reject';
  issues: string[];
  risk: 'low' | 'medium' | 'high';
  summary: string;
}

export interface RunSummary {
  run_id: string;
  repo: string;
  status: RunStatus | string;
  task?: string;
  error?: string | null;
  created: string;
  duration_s: number;
}

export interface RunDetail {
  run_id: string;
  repo: string;
  is_github: boolean;
  task: string;
  status: RunStatus | string;
  error?: string | null;
  patch_file?: string | null;
  created: string;
  elapsed_s?: number;
  tests_before?: TestResult | null;
  tests_after?: TestResult | null;
  review?: ReviewVerdict | null;
  attempts?: number | null;
  edits_made?: unknown[] | null;
  diff?: string;
  timings?: Record<string, unknown> | null;
}

export interface DemoRepo {
  id: string;
  name: string;
  description: string;
}

export interface ServerConfig {
  provider: string;
  model: string;
}

export interface RunStartRequest {
  repo?: string;
  demo_id?: string;
  task: string;
}

export interface RunStartResponse {
  run_id: string;
  status: string;
}
