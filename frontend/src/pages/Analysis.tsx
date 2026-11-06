import { useEffect, useState } from "react";
import {
  api, type AnalysisMode, type AnalysisRun, type Dependencies, type DuplicateBlock, type Explanation, type Finding,
  type Outline, type Repository,
} from "../api";
import { Badge, Card, Code, Empty, FileInput, Status } from "../components";
import { useAction } from "../hooks";

function FindingCard({ finding, onFix }: { finding: Finding; onFix?: () => void }) {
  return (
    <div className="finding">
      <div className="row">
        {onFix && <button className="secondary" onClick={onFix} title="Investigate the root cause and try a fix in a sandbox">Investigate &amp; fix</button>}
        {finding.severity && <Badge tone={finding.severity}>{finding.severity}</Badge>}
        {finding.impact && <Badge tone={finding.impact}>impact: {finding.impact}</Badge>}
        {finding.status && <Badge tone={finding.status === "confirmed" ? "high" : "info"}>{finding.status}</Badge>}
        <Badge tone="info">{finding.category.replace("_", " ")}</Badge>
        {finding.source === "static" && <Badge tone="low">static check</Badge>}
        <strong>{finding.title}</strong>
        <span className="muted small">lines {finding.line_start}–{finding.line_end}</span>
      </div>
      <Code>{finding.snippet}</Code>
      <p>{finding.explanation}</p>
      {finding.suggestion !== finding.explanation && <p><strong>Fix:</strong> {finding.suggestion}</p>}
      {finding.benefits && <p><strong>Benefits:</strong> {finding.benefits}</p>}
      {finding.risks && <p><strong>Risks:</strong> {finding.risks}</p>}
    </div>
  );
}

export function Analysis({ repo, files, onFixStarted }: { repo: Repository; files: string[]; onFixStarted: (sessionId: number) => void }) {
  const [path, setPath] = useState("");
  const [symbol, setSymbol] = useState("");
  const [mode, setMode] = useState<AnalysisMode>("fast");
  const [outline, setOutline] = useState<Outline | null>(null);
  const [deps, setDeps] = useState<Dependencies | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [duplicates, setDuplicates] = useState<DuplicateBlock[] | null>(null);
  const action = useAction();

  useEffect(() => {
    setOutline(null);
    setDeps(null);
    setSymbol("");
    if (!files.includes(path)) return;
    api.outline(repo.id, path).then(setOutline).catch(() => setOutline(null));
    api.dependencies(repo.id, path).then(setDeps).catch(() => setDeps(null));
  }, [repo.id, path, files]);

  const ready = files.includes(path) && !action.busy;

  return (
    <div className="stack">
      <Card title="Target">
        <div className="row">
          <FileInput id="analysis" value={path} onChange={setPath} files={files} />
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={!outline?.symbols.length}>
            <option value="">Whole file</option>
            {outline?.symbols.map((s) => <option key={s.qualified_name + s.start_line} value={s.qualified_name}>{s.kind} {s.qualified_name}</option>)}
          </select>
          <select value={mode} onChange={(e) => setMode(e.target.value as AnalysisMode)} title="thorough = one model call per top-level function">
            <option value="fast">fast</option>
            <option value="thorough">thorough</option>
          </select>
        </div>
        <div className="row">
          <button disabled={!ready} onClick={() => action.run("Explaining", () => api.explain(repo.id, path, symbol)).then((r) => r && setExplanation(r))}>Explain</button>
          <button disabled={!ready} onClick={() => action.run("Looking for bugs", () => api.bugs(repo.id, path, symbol, mode)).then((r) => r && setRun(r))}>Find bugs</button>
          <button disabled={!ready} onClick={() => action.run("Looking for improvements", () => api.improvements(repo.id, path, symbol, mode)).then((r) => r && setRun(r))}>Suggest improvements</button>
          <button className="secondary" disabled={!!action.busy} onClick={() => action.run("Finding duplicates", () => api.duplicates(repo.id)).then((r) => r && setDuplicates(r))}>Duplicates in repository</button>
        </div>
        <Status {...action} />
      </Card>

      {outline && (
        <div className="grid-2">
          <Card title="Structure">
            {!outline.supported ? <Empty>Structure parsing is not available for this language.</Empty> : (
              <ul className="small">
                {outline.symbols.map((s) => (
                  <li key={s.qualified_name + s.start_line} style={{ paddingLeft: `${(s.qualified_name.split(".").length - 1) * 16}px` }}>
                    <Badge tone="info">{s.kind}</Badge> <code>{s.name}</code> <span className="muted">{s.start_line}–{s.end_line}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Relationships">
            <p><strong>Imports:</strong> {deps?.imports.length ? deps.imports.map((p) => <code key={p} className="chip" onClick={() => setPath(p)}>{p}</code>) : "none"}</p>
            <p><strong>Used by:</strong> {deps?.imported_by.length ? deps.imported_by.map((p) => <code key={p} className="chip" onClick={() => setPath(p)}>{p}</code>) : "none"}</p>
            {deps?.external.length ? <p className="small muted">External: {deps.external.join(", ")}</p> : null}
          </Card>
        </div>
      )}

      {explanation && (
        <Card title={`Explanation of ${explanation.symbol ?? explanation.path}`} actions={<button className="secondary" onClick={() => setExplanation(null)}>Close</button>}>
          <div className="answer">{explanation.explanation}</div>
        </Card>
      )}

      {run && (
        <Card title={`${run.kind === "bugs" ? "Potential bugs" : "Improvements"} in ${run.path}${run.symbol ? ` · ${run.symbol}` : ""}`}
          actions={<span className="muted small">{Math.round(run.duration_ms / 1000)}s · {run.model}</span>}>
          <p>{run.summary}</p>
          {run.discarded_findings > 0 && <p className="muted small">{run.discarded_findings} finding(s) pointed outside the analysed code and were discarded.</p>}
          {run.findings.length === 0 ? <Empty>No findings.</Empty> : run.findings.map((f) => (
            <FindingCard key={f.id} finding={f} onFix={run.kind === "bugs" ? () => {
              action.run("Starting fix session", () => api.startFix(repo.id, { finding_id: f.id })).then((s) => s && onFixStarted(s.id));
            } : undefined} />
          ))}
        </Card>
      )}

      {duplicates && (
        <Card title="Duplicated code" actions={<button className="secondary" onClick={() => setDuplicates(null)}>Close</button>}>
          {duplicates.length === 0 ? <Empty>No duplicated blocks found.</Empty> : duplicates.map((d, i) => (
            <div key={i} className="finding">
              <strong>{d.line_count} lines</strong> in {d.locations.map((l) => `${l.path}:${l.start_line}-${l.end_line}`).join(", ")}
              <Code>{d.preview}</Code>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
