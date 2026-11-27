import type { LucideIcon } from 'lucide-react';

export function EmptyState({ icon: Icon, title, detail }: { icon: LucideIcon; title: string; detail?: string }) {
  return (
    <div className="empty-block" role="status">
      <div>
        <Icon aria-hidden="true" className="icon" style={{ margin: '0 auto var(--space-3)' }} />
        <div style={{ fontWeight: 700, color: 'var(--text)' }}>{title}</div>
        {detail && <p className="muted" style={{ maxWidth: 340, margin: 'var(--space-2) auto 0' }}>{detail}</p>}
      </div>
    </div>
  );
}
