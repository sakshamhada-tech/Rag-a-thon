import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, FileDiff } from 'lucide-react';
import { EmptyState } from './EmptyState';

interface DiffLine {
  kind: 'add' | 'del' | 'context' | 'hunk';
  oldLine: number | null;
  newLine: number | null;
  text: string;
}

interface DiffFile {
  path: string;
  lines: DiffLine[];
  adds: number;
  dels: number;
}

function parseHunkHeader(line: string): [number, number] {
  const match = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
  if (!match) return [1, 1];
  return [Number(match[1]), Number(match[2])];
}

export function parseUnifiedDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;
  let oldLine = 1;
  let newLine = 1;

  const ensure = () => {
    if (!current) {
      current = { path: '(unknown file)', lines: [], adds: 0, dels: 0 };
      files.push(current);
    }
    return current;
  };

  for (const raw of diff.split('\n')) {
    if (raw.startsWith('diff --git ')) {
      current = null;
      const match = / b\/(.+)$/.exec(raw);
      current = { path: match?.[1] || raw.replace('diff --git ', ''), lines: [], adds: 0, dels: 0 };
      files.push(current);
      oldLine = 1; newLine = 1;
      continue;
    }
    if (raw.startsWith('--- ')) continue;
    if (raw.startsWith('+++ ')) {
      const file = ensure();
      const path = raw.slice(4).replace(/^b\//, '');
      if (path !== '/dev/null') file.path = path;
      continue;
    }
    if (raw.startsWith('@@')) {
      const file = ensure();
      const [o, n] = parseHunkHeader(raw);
      oldLine = o; newLine = n;
      file.lines.push({ kind: 'hunk', oldLine: null, newLine: null, text: raw });
      continue;
    }
    if (!current) ensure();
    const file = current!;
    if (raw.startsWith('+')) {
      file.lines.push({ kind: 'add', oldLine: null, newLine, text: raw.slice(1) });
      newLine += 1; file.adds += 1;
    } else if (raw.startsWith('-')) {
      file.lines.push({ kind: 'del', oldLine, newLine: null, text: raw.slice(1) });
      oldLine += 1; file.dels += 1;
    } else {
      file.lines.push({ kind: 'context', oldLine, newLine, text: raw.startsWith(' ') ? raw.slice(1) : raw });
      oldLine += 1; newLine += 1;
    }
  }
  return files;
}

function DiffFileBlock({ file }: { file: DiffFile }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="diff-file" aria-label={`Diff for ${file.path}`}>
      <button className="diff-file-header" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 }}>
          {open ? <ChevronDown className="icon" /> : <ChevronRight className="icon" />}
          <span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.path}</span>
        </span>
        <span className="mono" style={{ fontSize: 'var(--text-xs)' }}><span style={{ color: 'var(--diff-add-text)' }}>+{file.adds}</span> <span style={{ color: 'var(--diff-del-text)' }}>-{file.dels}</span></span>
      </button>
      {open && (
        <div role="table" aria-label={`Unified diff for ${file.path}`}>
          {file.lines.map((line, index) => {
            const cls = line.kind === 'add' ? 'diff-add' : line.kind === 'del' ? 'diff-del' : line.kind === 'hunk' ? 'diff-hunk' : '';
            const marker = line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : line.kind === 'hunk' ? '@@' : ' ';
            return (
              <div className={`diff-line ${cls}`} role="row" key={index}>
                <span role="cell">{line.oldLine ?? ''}</span>
                <span role="cell">{line.newLine ?? ''}</span>
                <span role="cell" style={{ textAlign: 'center' }}>{marker}</span>
                <span className="diff-code" role="cell">{line.text}</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function DiffView({ diff }: { diff: string }) {
  const files = useMemo(() => parseUnifiedDiff(diff || ''), [diff]);
  const totals = files.reduce((acc, f) => ({ adds: acc.adds + f.adds, dels: acc.dels + f.dels }), { adds: 0, dels: 0 });
  if (!diff.trim()) return <EmptyState icon={FileDiff} title="No diff yet" detail="The unified diff appears after the agent edits the workspace and before human approval." />;
  return (
    <div>
      <div className="muted mono" style={{ fontSize: 'var(--text-xs)', marginBottom: 'var(--space-3)' }}>{files.length} file{files.length === 1 ? '' : 's'} · +{totals.adds} −{totals.dels}</div>
      {files.map((file) => <DiffFileBlock key={file.path} file={file} />)}
    </div>
  );
}
