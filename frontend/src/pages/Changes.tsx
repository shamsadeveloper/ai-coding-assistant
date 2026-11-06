import { useCallback, useEffect, useState } from "react";
import { api, type ChangeSet, type Repository } from "../api";
import { Badge, Card, DiffView, Empty, Status } from "../components";
import { useAction } from "../hooks";
import { FixSessions } from "./FixSessions";

const STATUS_TONE: Record<string, string> = { proposed: "medium", applied: "low", rolled_back: "info", rejected: "info", failed: "critical" };

type Tab = "requests" | "fixes";

export function Changes({ repo, files, onRepositoryChanged, fixFocus }: { repo: Repository; files: string[]; onRepositoryChanged: () => void; fixFocus: number | null }) {
  const [tab, setTab] = useState<Tab>(fixFocus ? "fixes" : "requests");
  useEffect(() => { if (fixFocus) setTab("fixes"); }, [fixFocus]);
  return (
    <div className="stack">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "requests"} className={tab === "requests" ? "active" : ""} onClick={() => setTab("requests")}>Change requests</button>
        <button role="tab" aria-selected={tab === "fixes"} className={tab === "fixes" ? "active" : ""} onClick={() => setTab("fixes")}>Fix sessions</button>
      </div>
      {tab === "requests"
        ? <ChangeRequests repo={repo} files={files} onRepositoryChanged={onRepositoryChanged} />
        : <FixSessions repo={repo} files={files} focusId={fixFocus} onRepositoryChanged={onRepositoryChanged} />}
    </div>
  );
}

function ChangeRequests({ repo, files, onRepositoryChanged }: { repo: Repository; files: string[]; onRepositoryChanged: () => void }) {
  const [changes, setChanges] = useState<ChangeSet[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [requestText, setRequestText] = useState("");
  const [paths, setPaths] = useState<string[]>([]);
  const [pathInput, setPathInput] = useState("");
  const [createBranch, setCreateBranch] = useState(true);
  const action = useAction();

  const refresh = useCallback(() => api.changes(repo.id).then(setChanges).catch(() => setChanges([])), [repo.id]);
  useEffect(() => { refresh(); }, [refresh]);

  const propose = async () => {
    const change = await action.run("Generating patch", () => api.proposeChange(repo.id, requestText.trim(), paths));
    if (change) {
      await refresh();
      setOpenId(change.id);
      setRequestText("");
    }
  };

  const decide = async (label: string, call: () => Promise<ChangeSet>) => {
    const change = await action.run(label, call);
    if (change) {
      await refresh();
      if (change.status === "applied" || change.status === "rolled_back") onRepositoryChanged();
    }
  };

  const open = changes.find((c) => c.id === openId);

  return (
    <div className="stack">
      <Card title="Request a change">
        <textarea rows={3} value={requestText} onChange={(e) => setRequestText(e.target.value)} className="full"
          placeholder="Describe the change, e.g. “Return 0.0 from average_order_value when the list is empty”" />
        <div className="row">
          <input list="change-files" value={pathInput} onChange={(e) => setPathInput(e.target.value)} placeholder="Optional: limit to files" className="grow" />
          <datalist id="change-files">{files.map((f) => <option key={f} value={f} />)}</datalist>
          <button className="secondary" disabled={!pathInput.trim()} onClick={() => { setPaths((p) => [...new Set([...p, pathInput.trim()])]); setPathInput(""); }}>Add file</button>
          <button disabled={requestText.trim().length < 5 || !!action.busy} onClick={propose}>Propose patch</button>
        </div>
        {paths.length > 0 && <p>{paths.map((p) => <code key={p} className="chip" title="Remove" onClick={() => setPaths(paths.filter((x) => x !== p))}>{p} ✕</code>)}</p>}
        <p className="muted small">Nothing on disk changes until you approve. Files are chosen automatically when none are given.</p>
        <Status {...action} />
      </Card>

      <div className="grid-sidebar">
        <Card title="History">
          {changes.length === 0 ? <Empty>No changes yet.</Empty> : (
            <ul className="list">
              {changes.map((c) => (
                <li key={c.id} className={c.id === openId ? "selected" : ""} onClick={() => setOpenId(c.id)}>
                  <div className="row"><strong>#{c.id}</strong> <Badge tone={STATUS_TONE[c.status] ?? "info"}>{c.status.replace("_", " ")}</Badge></div>
                  <div className="small clamp">{c.request}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {open ? (
          <Card title={`Change #${open.id}`} actions={<Badge tone={STATUS_TONE[open.status] ?? "info"}>{open.status.replace("_", " ")}</Badge>}>
            <p><strong>Request:</strong> {open.request}</p>
            <p className="answer">{open.summary}</p>
            {open.branch_name && <p className="small">Applied on branch <code>{open.branch_name}</code> (from <code>{open.base_branch}</code>)</p>}
            {open.files.map((f) => (
              <div key={f.path} className="file-change">
                <h4 className="row">
                  <code>{f.path}</code>
                  {f.is_new && <Badge tone="info">new file</Badge>}
                  {!f.syntax_ok && <Badge tone="high">syntax error</Badge>}
                </h4>
                {f.notes && <p className="muted small notes">{f.notes}</p>}
                <DiffView diff={f.diff} />
              </div>
            ))}
            <div className="row">
              {open.status === "proposed" && (
                <>
                  <label><input type="checkbox" checked={createBranch} onChange={(e) => setCreateBranch(e.target.checked)} /> create a Git branch</label>
                  <button disabled={!!action.busy} onClick={() => confirm("Apply this patch to the files on disk?") && decide("Applying", () => api.approveChange(open.id, createBranch))}>Approve &amp; apply</button>
                </>
              )}
              {(open.status === "proposed" || open.status === "failed") && (
                <button className="danger" disabled={!!action.busy} onClick={() => decide("Rejecting", () => api.rejectChange(open.id))}>Reject</button>
              )}
              {open.status === "applied" && (
                <button className="danger" disabled={!!action.busy} onClick={() => confirm("Restore the files from backup?") && decide("Rolling back", () => api.rollbackChange(open.id))}>Roll back</button>
              )}
            </div>
          </Card>
        ) : <Card><Empty>Select a change to review its diff.</Empty></Card>}
      </div>
    </div>
  );
}
