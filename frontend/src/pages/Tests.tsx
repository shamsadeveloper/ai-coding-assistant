import { useCallback, useEffect, useState } from "react";
import { api, type CoverageGap, type GeneratedTest, type Repository, type TestFramework, type TestRun } from "../api";
import { Badge, Card, Code, Empty, FileInput, Status } from "../components";
import { useAction } from "../hooks";

export function Tests({ repo, files, onRepositoryChanged }: { repo: Repository; files: string[]; onRepositoryChanged: () => void }) {
  const [frameworks, setFrameworks] = useState<TestFramework[]>([]);
  const [gaps, setGaps] = useState<CoverageGap[] | null>(null);
  const [generated, setGenerated] = useState<GeneratedTest[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [overwrite, setOverwrite] = useState(false);
  const [path, setPath] = useState("");
  const [runPath, setRunPath] = useState("");
  const [run, setRun] = useState<TestRun | null>(null);
  const action = useAction();

  const refresh = useCallback(() => {
    api.frameworks(repo.id).then(setFrameworks).catch(() => setFrameworks([]));
    api.generatedTests(repo.id).then(setGenerated).catch(() => setGenerated([]));
  }, [repo.id]);

  useEffect(() => {
    setGaps(null);
    setRun(null);
    refresh();
  }, [refresh]);

  const generate = async (source: string, symbol?: string) => {
    const record = await action.run(`Generating tests for ${symbol ?? source}`, () => api.generateTest(repo.id, source, symbol));
    if (record) {
      refresh();
      setOpenId(record.id);
    }
  };

  const decide = async (record: GeneratedTest, approve: boolean) => {
    const done = await action.run(approve ? "Writing test into repository" : "Rejecting",
      () => (approve ? api.approveTest(record.id, overwrite) : api.rejectTest(record.id)));
    if (done) {
      refresh();
      if (approve) onRepositoryChanged();
    }
  };

  const open = generated.find((g) => g.id === openId);

  return (
    <div className="stack">
      <Status {...action} />
      <div className="grid-2">
        <Card title="Test frameworks">
          {frameworks.length === 0 ? <Empty>None detected.</Empty> : (
            <ul>{frameworks.map((f) => <li key={f.name}><Badge tone="info">{f.name}</Badge> {f.language} <span className="muted small">({f.evidence})</span></li>)}</ul>
          )}
        </Card>
        <Card title="Run tests">
          <div className="row">
            <FileInput id="run" value={runPath} onChange={setRunPath} files={files} />
            <button disabled={!!action.busy} onClick={() => action.run("Running tests", () => api.runTests(repo.id, runPath)).then((r) => r && setRun(r))}>
              Run {runPath ? "file" : "all"}
            </button>
          </div>
          <p className="muted small">Uses the configured command only (no shell, timeout, no secrets in the environment).</p>
        </Card>
      </div>

      {run && (
        <Card title={`Test run #${run.id}`} actions={
          <button className="secondary" disabled={!!action.busy} onClick={() => action.run("Analysing failures", () => api.analyzeRun(run.id)).then((r) => r && setRun(r))}>Explain results</button>
        }>
          <p className="row">
            <Badge tone={run.exit_code === 0 ? "low" : "critical"}>{run.timed_out ? "timed out" : `exit ${run.exit_code}`}</Badge>
            passed {run.passed ?? 0} · failed {run.failed ?? 0} · errors {run.errors ?? 0} · skipped {run.skipped ?? 0}
            <span className="muted small">{Math.round(run.duration_ms)} ms · <code>{run.command.slice(1).join(" ")}</code></span>
          </p>
          <Code>{run.output.slice(-4000)}</Code>
          {run.analysis && <div className="answer">{run.analysis}</div>}
        </Card>
      )}

      <Card title="Generate tests">
        <div className="row">
          <FileInput id="gen" value={path} onChange={setPath} files={files} />
          <button disabled={!files.includes(path) || !!action.busy} onClick={() => generate(path)}>Generate</button>
          <button className="secondary" disabled={!!action.busy} onClick={() => action.run("Finding coverage gaps", () => api.gaps(repo.id)).then((g) => g && setGaps(g))}>Find untested code</button>
        </div>
        {gaps && (gaps.length === 0 ? <Empty>Every public function is mentioned in a test.</Empty> : (
          <table>
            <thead><tr><th>File</th><th>Not mentioned in any test</th><th>Test files</th></tr></thead>
            <tbody>
              {gaps.slice(0, 30).map((g) => (
                <tr key={g.path}>
                  <td className="mono small">{g.path}</td>
                  <td>{g.untested_symbols.map((s) => (
                    <button key={s} className="chip-button" disabled={!!action.busy} title="Generate a test for this" onClick={() => generate(g.path, s)}>{s}</button>
                  ))}</td>
                  <td className="small">{g.test_files.join(", ") || "none"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </Card>

      <Card title="Review area">
        {generated.length === 0 ? <Empty>No generated tests yet.</Empty> : (
          <table>
            <thead><tr><th>#</th><th>For</th><th>Target</th><th>Status</th><th /></tr></thead>
            <tbody>
              {generated.map((g) => (
                <tr key={g.id} className={g.id === openId ? "selected" : ""} onClick={() => setOpenId(g.id)}>
                  <td>{g.id}</td>
                  <td className="mono small">{g.source_path}{g.symbol ? ` · ${g.symbol}` : ""}</td>
                  <td className="mono small">{g.target_path} {g.target_exists && <Badge tone="medium">exists</Badge>}</td>
                  <td><Badge tone={g.status === "applied" ? "low" : g.status === "rejected" ? "info" : "medium"}>{g.status}</Badge> {!g.syntax_ok && <Badge tone="high">syntax</Badge>}</td>
                  <td />
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {open && (
          <div className="review">
            <h4>{open.target_path} <span className="muted small">({open.framework})</span></h4>
            {open.test_cases.length > 0 && <ul className="small">{open.test_cases.map((c, i) => <li key={i}>{c}</li>)}</ul>}
            {open.notes && <p className="muted small">{open.notes}</p>}
            <Code>{open.content}</Code>
            {open.status === "pending" && (
              <div className="row">
                {open.target_exists && (
                  <label><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} /> overwrite existing {open.target_path} (a backup is kept)</label>
                )}
                <button disabled={!!action.busy || (open.target_exists && !overwrite)} onClick={() => decide(open, true)}>Approve &amp; add to repository</button>
                <button className="danger" disabled={!!action.busy} onClick={() => decide(open, false)}>Reject</button>
              </div>
            )}
            {open.backup_path && <p className="muted small">Previous file backed up to {open.backup_path}</p>}
          </div>
        )}
      </Card>
    </div>
  );
}
