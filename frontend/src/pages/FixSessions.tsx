import { useCallback, useEffect, useState } from "react";
import {
  ACTIVE_FIX_STATUSES, api, type Check, type FixAttempt, type FixEvent, type FixSession, type FixSessionSummary,
  type Repository,
} from "../api";
import { Badge, Card, Code, DiffView, Empty, FileInput, Status } from "../components";
import { useAction } from "../hooks";

const STATUS_TONE: Record<string, string> = {
  queued: "info", investigating: "medium", fixing: "medium", validating: "medium", reviewing: "medium",
  awaiting_approval: "high", applied: "low", rejected: "info", unverified: "info", failed: "critical",
  cancelled: "info", interrupted: "info",
};
const VALIDATION: Record<string, { tone: string; label: string }> = {
  passed: { tone: "low", label: "Tests passed in sandbox" },
  not_validated: { tone: "medium", label: "Not validated" },
  failed: { tone: "critical", label: "Validation failed" },
};
const ATTEMPT_TONE: Record<string, string> = { passed: "low", failed: "critical", rejected: "high", not_validated: "medium" };

const isActive = (status: string) => ACTIVE_FIX_STATUSES.includes(status);
const label = (status: string) => status.replace(/_/g, " ");
const time = (iso: string) => new Date(iso).toLocaleTimeString();

function Checks({ checks }: { checks: Check[] }) {
  return (
    <ul className="checks">
      {checks.map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}><strong>{c.name.replace(/_/g, " ")}</strong>: {c.detail}</li>)}
    </ul>
  );
}

function Attempt({ attempt, isFinal }: { attempt: FixAttempt; isFinal: boolean }) {
  const cmp = attempt.comparison;
  return (
    <div className="attempt">
      <div className="row">
        <strong>Attempt {attempt.number}</strong>
        <Badge tone={ATTEMPT_TONE[attempt.status] ?? "info"}>{label(attempt.status)}</Badge>
        {isFinal && <Badge tone="info">offered for approval</Badge>}
        <span className="muted small">{Math.round(attempt.duration_ms / 1000)}s</span>
      </div>
      <p className="answer small">{attempt.explanation}</p>
      <Checks checks={attempt.checks} />
      {attempt.test && cmp && (
        <div className="small">
          <p><strong>Sandbox tests:</strong> {attempt.test.passed ?? 0} passed, {(attempt.test.failed ?? 0) + (attempt.test.errors ?? 0)} failing · {cmp.reason}</p>
          {cmp.new_failures.length > 0 && <p><Badge tone="critical">new failures</Badge> {cmp.new_failures.join(", ")}</p>}
          {cmp.fixed.length > 0 && <p><Badge tone="low">fixed</Badge> {cmp.fixed.join(", ")}</p>}
          {cmp.still_failing.length > 0 && <p><Badge tone="info">baseline failures</Badge> {cmp.still_failing.join(", ")} <span className="muted">(failed before the patch too)</span></p>}
          <details><summary className="muted">Test output</summary><Code>{attempt.test.output}</Code></details>
        </div>
      )}
      {attempt.failure_analysis && <p className="small"><strong>Why it failed:</strong> {attempt.failure_analysis}</p>}
      {attempt.files.filter((f) => f.diff).map((f) => (
        <div key={f.path}>
          <div className="row small"><code>{f.path}</code>{!f.syntax_ok && <Badge tone="high">syntax error</Badge>}</div>
          {f.notes && <p className="muted small notes">{f.notes}</p>}
          <DiffView diff={f.diff} />
        </div>
      ))}
    </div>
  );
}

function SessionDetail({ sessionId, onChanged, onRepositoryChanged }: { sessionId: number; onChanged: () => void; onRepositoryChanged: () => void }) {
  const [session, setSession] = useState<FixSession | null>(null);
  const [events, setEvents] = useState<FixEvent[]>([]);
  const [createBranch, setCreateBranch] = useState(true);
  const action = useAction();

  const load = useCallback(async () => {
    const [s, e] = await Promise.all([api.fixSession(sessionId), api.fixEvents(sessionId)]);
    setSession(s);
    setEvents(e);
    return s;
  }, [sessionId]);

  useEffect(() => {
    setSession(null);
    setEvents([]);
    load().catch(() => undefined);
  }, [load]);

  // Live timeline: poll while the session is running.
  const active = !!session && isActive(session.status);
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => {
      load().then((s) => { if (!isActive(s.status)) onChanged(); }).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [active, load, onChanged]);

  if (!session) return <Card><Empty>Loading session…</Empty></Card>;

  const decide = async (text: string, call: () => Promise<FixSession>) => {
    const result = await action.run(text, call);
    if (result) {
      await load();
      onChanged();
      if (result.status === "applied") onRepositoryChanged();
    }
  };
  const rc = session.root_cause;
  const validation = session.validation_status ? VALIDATION[session.validation_status] : null;

  return (
    <div className="stack">
      <Card
        title={`Fix session #${session.id}`}
        actions={<>
          <Badge tone={STATUS_TONE[session.status] ?? "info"}>{label(session.status)}</Badge>
          {validation && <Badge tone={validation.tone}>{validation.label}</Badge>}
        </>}
      >
        <p><strong>Bug:</strong> {session.description}</p>
        <p className="small muted">
          {session.path}{session.line_start ? `:${session.line_start}` : ""} · {session.llm_calls} model call(s) ·{" "}
          {session.prompt_tokens + session.completion_tokens} tokens{session.model && ` · ${session.model}`}
        </p>
        {session.error && <div className={`notice ${session.status === "failed" ? "bad" : "warn"}`}>{session.error}</div>}
        {session.validation_note && <div className={`notice ${session.validation_status === "passed" ? "good" : "warn"}`}>{session.validation_note}</div>}
        <div className="row">
          {active && <button className="danger" disabled={!!action.busy} onClick={() => decide("Stopping", () => api.stopFix(session.id))}>Stop session</button>}
          {session.status === "awaiting_approval" && (
            <>
              <label><input type="checkbox" checked={createBranch} onChange={(e) => setCreateBranch(e.target.checked)} /> create a Git branch</label>
              <button disabled={!!action.busy}
                onClick={() => confirm(`Apply attempt ${session.final_attempt} to the files on disk?${session.validation_status !== "passed" ? "\n\nWARNING: this patch was NOT validated by tests." : ""}`)
                  && decide("Applying", () => api.approveFix(session.id, createBranch))}>Approve &amp; apply</button>
            </>
          )}
          {["awaiting_approval", "failed", "unverified"].includes(session.status) && (
            <button className="danger" disabled={!!action.busy} onClick={() => decide("Rejecting", () => api.rejectFix(session.id))}>Reject</button>
          )}
          {session.change_set_id && <span className="small">Applied as change <strong>#{session.change_set_id}</strong> (roll back from Change requests).</span>}
        </div>
        <Status {...action} />
      </Card>

      <Card title="Timeline" actions={active ? <span className="small muted"><span className="spinner" aria-hidden /> live</span> : null}>
        {events.length === 0 ? <Empty>No events yet.</Empty> : (
          <ul className="timeline">
            {events.map((e) => (
              <li key={e.id}>
                <span className="muted">{time(e.created_at)}</span>
                <span className="stage">{label(e.stage)}</span>
                <span>{e.message}{e.duration_ms != null && e.duration_ms > 1000 && <span className="muted small"> · {Math.round(e.duration_ms / 1000)}s</span>}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {rc && (
        <Card title="Root cause" actions={<Badge tone={rc.confidence === "high" ? "low" : rc.confidence === "medium" ? "medium" : "high"}>confidence: {rc.confidence}</Badge>}>
          <p>{rc.summary}</p>
          <p className="small"><strong>Location:</strong> <code>{rc.location}</code> · <strong>Files to change:</strong> {rc.files_to_change.map((f) => <code key={f} className="chip">{f}</code>)}</p>
          <p className="small"><strong>Fix strategy:</strong> {rc.fix_strategy}</p>
          <h4>Verified evidence ({rc.evidence.length})</h4>
          {rc.evidence.length === 0 ? <Empty>None of the cited lines could be found in the code.</Empty> : rc.evidence.map((e) => (
            <div key={`${e.path}:${e.line}`} className="small">
              <code>{e.path}:{e.line}</code> <Code>{e.quote}</Code> {e.explanation}
            </div>
          ))}
          {rc.rejected_evidence.length > 0 && (
            <details className="small">
              <summary className="muted">{rc.rejected_evidence.length} claim(s) rejected: not found in the code</summary>
              {rc.rejected_evidence.map((e, i) => <p key={i}><code>{e.path}:{e.line}</code> "{e.quote}": {e.reason}</p>)}
            </details>
          )}
          {session.context && session.context.callers.length > 0 && (
            <details className="small">
              <summary className="muted">Callers of {session.context.symbol} ({session.context.callers.length})</summary>
              {session.context.callers.map((c) => <div key={`${c.path}:${c.line}`}><code>{c.path}:{c.line}</code> {c.text}</div>)}
            </details>
          )}
        </Card>
      )}

      {(session.baseline || session.reproduction) && (
        <Card title="Sandbox baseline">
          {session.baseline && (
            <p className="small">
              Before any change: {session.baseline.passed ?? 0} passed, {(session.baseline.failed ?? 0) + (session.baseline.errors ?? 0)} failing
              {session.baseline.failed_ids?.length ? <> ({session.baseline.failed_ids.join(", ")})</> : null}.
              <span className="muted"> These failures are not blamed on a patch; only new failures are.</span>
            </p>
          )}
          {session.reproduction && (
            <details className="small">
              <summary>Reproduction test <code>{session.reproduction.path}</code>: {session.reproduction.note}</summary>
              <Code>{session.reproduction.content}</Code>
              <p className="muted">This test was used only in the sandbox; it is not part of the patch.</p>
            </details>
          )}
        </Card>
      )}

      {session.attempts.length > 0 && (
        <Card title={`Attempts (${session.attempts.length} of max 3)`}>
          {session.attempts.map((a) => <Attempt key={a.number} attempt={a} isFinal={a.number === session.final_attempt} />)}
        </Card>
      )}

      {session.review && (
        <Card title="Code review" actions={<Badge tone={session.review.verdict === "approve" ? "low" : "high"}>{session.review.verdict}</Badge>}>
          <p>{session.review.summary}</p>
          {session.review.issues.map((i, n) => <p key={n} className="small"><Badge tone={i.severity}>{i.severity}</Badge> {i.description}</p>)}
          <Checks checks={session.review.automated} />
          <p className="muted small">The review is advice from the model plus automatic checks; the decision is yours.</p>
        </Card>
      )}
    </div>
  );
}

export function FixSessions({ repo, files, focusId, onRepositoryChanged }: { repo: Repository; files: string[]; focusId: number | null; onRepositoryChanged: () => void }) {
  const [sessions, setSessions] = useState<FixSessionSummary[]>([]);
  const [openId, setOpenId] = useState<number | null>(focusId);
  const [description, setDescription] = useState("");
  const [path, setPath] = useState("");
  const [line, setLine] = useState("");
  const [runTests, setRunTests] = useState(true);
  const [reproduction, setReproduction] = useState(false);
  const action = useAction();

  const refresh = useCallback(() => api.fixSessions(repo.id).then(setSessions).catch(() => setSessions([])), [repo.id]);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { if (focusId) setOpenId(focusId); }, [focusId]);

  const start = async () => {
    const session = await action.run("Starting fix session", () => api.startFix(repo.id, {
      description: description.trim(), path, line_start: Number(line) || undefined,
      run_tests: runTests, reproduction_test: reproduction,
    }));
    if (session) {
      setDescription("");
      await refresh();
      setOpenId(session.id);
    }
  };

  return (
    <div className="stack">
      <Card title="Investigate and fix a bug">
        <textarea rows={2} className="full" value={description} onChange={(e) => setDescription(e.target.value)}
          placeholder="Describe the bug, e.g. “average_order_value crashes when the order list is empty”" />
        <div className="row">
          <FileInput id="fix" value={path} onChange={setPath} files={files} />
          <input type="number" min={1} value={line} onChange={(e) => setLine(e.target.value)} placeholder="line" style={{ width: 80 }} />
          <label><input type="checkbox" checked={runTests} onChange={(e) => setRunTests(e.target.checked)} /> validate with tests</label>
          <label title="pytest only; adds a model call and a test run"><input type="checkbox" checked={reproduction} onChange={(e) => setReproduction(e.target.checked)} /> write a reproduction test first</label>
          <button disabled={description.trim().length < 5 || !files.includes(path) || !!action.busy} onClick={start}>Start</button>
        </div>
        <p className="muted small">
          The agent investigates, then tries up to 3 patches in an isolated copy of the repository and runs the tests there.
          Your files are only changed if you approve. Runs in the background; it can take several minutes on CPU. You can also start
          a session from a bug finding on the Analysis page.
        </p>
        <Status {...action} />
      </Card>

      <div className="grid-sidebar">
        <Card title="Sessions">
          {sessions.length === 0 ? <Empty>No fix sessions yet.</Empty> : (
            <ul className="list">
              {sessions.map((s) => (
                <li key={s.id} className={s.id === openId ? "selected" : ""} onClick={() => setOpenId(s.id)}>
                  <div className="row">
                    <strong>#{s.id}</strong>
                    <Badge tone={STATUS_TONE[s.status] ?? "info"}>{label(s.status)}</Badge>
                    {s.validation_status && <Badge tone={VALIDATION[s.validation_status]?.tone ?? "info"}>{VALIDATION[s.validation_status]?.label ?? s.validation_status}</Badge>}
                  </div>
                  <div className="small clamp">{s.description}</div>
                  <div className="small muted">{s.path}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {openId ? <SessionDetail sessionId={openId} onChanged={refresh} onRepositoryChanged={onRepositoryChanged} />
          : <Card><Empty>Select a session to see its timeline, root cause, attempts and review.</Empty></Card>}
      </div>
    </div>
  );
}
