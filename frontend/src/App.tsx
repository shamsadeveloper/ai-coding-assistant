import { useCallback, useEffect, useState } from "react";
import { api, type Repository } from "./api";
import { Empty } from "./components";
import { Analysis } from "./pages/Analysis";
import { Changes } from "./pages/Changes";
import { Chat } from "./pages/Chat";
import { Dashboard } from "./pages/Dashboard";
import { Tests } from "./pages/Tests";

const PAGES = ["Dashboard", "Chat", "Analysis", "Tests", "Changes"] as const;
type Page = (typeof PAGES)[number];

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the choice just isn't remembered */
  }
}

export function App() {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(() => Number(readStored("repositoryId")) || null);
  const [page, setPage] = useState<Page>(() => (PAGES as readonly string[]).includes(readStored("page") ?? "") ? (readStored("page") as Page) : "Dashboard");
  const [files, setFiles] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadRepositories = useCallback(async () => {
    try {
      setRepositories(await api.repositories());
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { loadRepositories(); }, [loadRepositories]);

  const selected = repositories.find((r) => r.id === selectedId) ?? null;

  const loadFiles = useCallback(() => {
    if (!selected) return setFiles([]);
    api.files(selected.id).then((r) => setFiles(r.items.map((f) => f.path))).catch(() => setFiles([]));
  }, [selected]);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  const select = (id: number) => { setSelectedId(id); store("repositoryId", String(id)); };
  const go = (p: Page) => { setPage(p); store("page", p); };
  const repositoryChanged = () => { loadRepositories(); loadFiles(); };
  const [fixFocus, setFixFocus] = useState<number | null>(null);
  const openFixSession = (id: number) => { setFixFocus(id); go("Changes"); };

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>AI Coding Assistant</h1>
        <label className="small muted" htmlFor="repo-select">Repository</label>
        <select id="repo-select" value={selected?.id ?? ""} onChange={(e) => select(Number(e.target.value))}>
          <option value="" disabled>Select…</option>
          {repositories.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <nav>
          {PAGES.map((p) => (
            <button key={p} className={`nav ${p === page ? "active" : ""}`} onClick={() => go(p)}>{p}</button>
          ))}
        </nav>
        <p className="small muted footer">Runs locally with Ollama. Changes are applied only after your approval.</p>
      </aside>
      <main>
        <h2>{page}{selected && page !== "Dashboard" && <span className="muted"> · {selected.name}</span>}</h2>
        {loadError && <div className="status error">Cannot reach the API: {loadError}. Is the backend running on port 8001?</div>}
        {page === "Dashboard" ? (
          <Dashboard repositories={repositories} selected={selected} onSelect={select} onChanged={loadRepositories} />
        ) : !selected ? (
          <Empty>Select or register a repository on the Dashboard first.</Empty>
        ) : page === "Chat" ? (
          <Chat repo={selected} />
        ) : page === "Analysis" ? (
          <Analysis repo={selected} files={files} onFixStarted={openFixSession} />
        ) : page === "Tests" ? (
          <Tests repo={selected} files={files} onRepositoryChanged={repositoryChanged} />
        ) : (
          <Changes repo={selected} files={files} onRepositoryChanged={repositoryChanged} fixFocus={fixFocus} />
        )}
      </main>
    </div>
  );
}
