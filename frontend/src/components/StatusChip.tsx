import type { RunStatus } from '../api/types';

export function StatusChip({ status }: { status: RunStatus | 'idle' | string }) {
  const label = status === 'idle' ? 'idle' : String(status).replace(/_/g, ' ');
  return <span className={`status-chip status-${status}`} aria-label={`Run status: ${label}`}>{label}</span>;
}
