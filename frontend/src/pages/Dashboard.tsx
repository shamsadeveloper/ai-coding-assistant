import { useEffect, useState } from "react";
import { api, type GitStatus, type LLMHealth, type Repository } from "../api";
import { Badge, Card, Empty, Status } from "../components";
import { useAction } from "../hooks";

interface Props {
  repositories: Repository[];
  selected: Repository | null;
  onSelect: (id: number) => void;
  onChanged: () => Promise<void>;
}

export function Dashboard({ repositories, selected, onSelect, onChanged }: Props) {
  const [path, setPath] = useState("");
  const [health, setHealth] = useState<LLMHealth | null>(null);
  const [git, setGit] = useState<GitStatus | null>(null);
  const action = useAction();

  useEffect(() => {
    api.llmHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    setGit(null);
    if (selected) api.gitStatus(selected.id).then(setGit).catch(() => setGit(null));
  }, [selected]);

  const register = async () => {
    const repo = await action.run("Registering and scanning", () => api.registerRepository(path.trim()));
    if (repo) {
      setPath("");
      await onChanged();
      onSelect(repo.id);
    }
  };

  const summary = selected?.scan_summary;

  return (
    <div className="stack">
      <Card title="Local model">
        {health === null ? (
          <Empty>Checking Ollama…</Empty>
        ) : health.reachable ? (
          <p>
            Ollama is running. Model <code>{health.configured_model}</code>{" "}
            {health.model_available ? <Badge tone="low">available</Badge> : <Badge tone="critical">not pulled</Badge>}
          </p>
        ) : (
          <p><Badge tone="critical">offline</Badge> Ollama is not reachable. Start the Ollama app.</p>
        )}
      </Card>

      <Card title="Add a repository">
        <div className="row">
          <input className="grow" value={path} onChange={(e) => setPath(e.target.value)}
            placeholder="Absolute folder path, e.g. D:/projects/my-app" onKeyDown={(e) => e.key === "Enter" && path && register()} />
          <button onClick={register} disabled={!path.trim() || !!action.busy}>Register</button>
        </div>
        <Status {...action} />
      </Card>

      <Card title="Repositories">
        {repositories.length === 0 ? <Empty>No repositories yet.</Empty> : (
          <table>
            <thead><tr><th>Name</th><th>Path</th><th>Files</th><th>Last scan</th><th /></tr></thead>
            <tbody>
              {repositories.map((r) => (
                <tr key={r.id} className={r.id === selected?.id ? "selected" : ""} onClick={() => onSelect(r.id)}>
                  <td>{r.name}</td>
                  <td className="mono small">{r.root_path}</td>
                  <td>{r.scan_summary?.file_count ?? "–"}</td>
                  <td className="small">{r.last_scanned_at ? new Date(r.last_scanned_at).toLocaleString() : "never"}</td>
                  <td className="row">
                    <button className="secondary" onClick={(e) => { e.stopPropagation(); action.run("Rescanning", () => api.rescan(r.id)).then(onChanged); }}>Rescan</button>
                    <button className="danger" onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(`Remove ${r.name} from the assistant? Files on disk are not touched.`)) {
                        action.run("Removing", () => api.removeRepository(r.id)).then(onChanged);
                      }
                    }}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {selected && summary && (
        <div className="grid-2">
          <Card title={`${selected.name}: languages`}>
            <ul className="bars">
              {Object.entries(summary.languages).map(([lang, count]) => (
                <li key={lang}>
                  <span>{lang}</span>
                  <span className="bar" style={{ width: `${(count / summary.file_count) * 100}%` }} />
                  <span className="muted">{count}</span>
                </li>
              ))}
            </ul>
            <p>Frameworks: {summary.frameworks.length ? summary.frameworks.map((f) => <Badge key={f} tone="info">{f}</Badge>) : "none detected"}</p>
          </Card>
          <Card title="Skipped while scanning">
            {Object.keys(summary.skipped).length === 0 ? <Empty>Nothing skipped.</Empty> : (
              <ul>{Object.entries(summary.skipped).map(([reason, count]) => <li key={reason}>{reason.replace("_", " ")}: {count}</li>)}</ul>
            )}
            <h4>Git</h4>
            {git === null ? <Empty>…</Empty> : !git.is_repository ? <Empty>Not a Git repository.</Empty> : (
              <>
                <p>Branch <code>{git.branch}</code>, {git.changes.length} uncommitted change(s)</p>
                {git.changes.length > 0 && <pre className="code small">{git.changes.join("\n")}</pre>}
              </>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
