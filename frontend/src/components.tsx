import type { ReactNode } from "react";

export function Status({ busy, error, elapsed }: { busy: string | null; error: string | null; elapsed: number }) {
  if (busy) {
    return (
      <div className="status busy" role="status">
        <span className="spinner" aria-hidden /> {busy}… {elapsed > 2 && <span className="muted">{elapsed}s</span>}
      </div>
    );
  }
  if (error) return <div className="status error" role="alert">{error}</div>;
  return null;
}

export function Badge({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`badge badge-${tone.toLowerCase().replace(/[^a-z_]/g, "")}`}>{children}</span>;
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-header">
          {title && <h3>{title}</h3>}
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

/** Unified diff with added/removed lines coloured. */
export function DiffView({ diff }: { diff: string }) {
  if (!diff) return <Empty>No changes.</Empty>;
  return (
    <pre className="diff">
      {diff.split("\n").map((line, i) => {
        const kind = line.startsWith("+++") || line.startsWith("---") ? "meta"
          : line.startsWith("@@") ? "hunk"
          : line.startsWith("+") ? "add"
          : line.startsWith("-") ? "del" : "ctx";
        return <div key={i} className={`diff-${kind}`}>{line || " "}</div>;
      })}
    </pre>
  );
}

export function Code({ children }: { children: string }) {
  return <pre className="code">{children}</pre>;
}

/** File path input with suggestions from the repository index. */
export function FileInput({ value, onChange, files, id }: { value: string; onChange: (v: string) => void; files: string[]; id: string }) {
  return (
    <>
      <input list={`${id}-files`} value={value} onChange={(e) => onChange(e.target.value)} placeholder="path/to/file.py" className="grow" />
      <datalist id={`${id}-files`}>
        {files.map((f) => <option key={f} value={f} />)}
      </datalist>
    </>
  );
}
