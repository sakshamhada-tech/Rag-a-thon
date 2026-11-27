import { AlertTriangle } from 'lucide-react';

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="notice" role="alert" style={{ borderColor: 'var(--danger)', color: 'var(--danger)', background: 'var(--danger-bg)' }}>
      <div style={{ display: 'flex', gap: 'var(--space-2)', alignItems: 'flex-start' }}>
        <AlertTriangle aria-hidden="true" className="icon" />
        <div>
          <div style={{ fontWeight: 700 }}>Something went wrong</div>
          <div style={{ color: 'inherit', opacity: 0.92 }}>{message}</div>
          {onRetry && <button className="button" style={{ marginTop: 'var(--space-2)' }} onClick={onRetry}>Retry</button>}
        </div>
      </div>
    </div>
  );
}
