import { useEffect, useRef, useState } from "react";
import { api, type AgentReply, type Repository } from "../api";
import { Badge, Empty, Status } from "../components";
import { useAction } from "../hooks";

interface Turn {
  question: string;
  reply?: AgentReply;
}

export function Chat({ repo }: { repo: Repository }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState<number | undefined>();
  const [message, setMessage] = useState("");
  const action = useAction();
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setTurns([]);
    setConversationId(undefined);
  }, [repo.id]);

  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would treat a
  // returned value as the effect's cleanup function and crash the page when it tries to call it.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, action.busy]);

  const send = async () => {
    const question = message.trim();
    if (!question) return;
    setMessage("");
    setTurns((t) => [...t, { question }]);
    const reply = await action.run("The agent is working", () => api.chat(repo.id, question, conversationId));
    if (reply) {
      setConversationId(reply.conversation_id);
      setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, reply } : turn)));
    }
  };

  return (
    <div className="chat">
      <div className="chat-log">
        {turns.length === 0 && (
          <Empty>
            Ask about <strong>{repo.name}</strong>, e.g. “Where is the database configured?” or “Which functions have no tests?”
          </Empty>
        )}
        {turns.map((turn, i) => (
          <div key={i} className="turn">
            <div className="bubble user">{turn.question}</div>
            {turn.reply && (
              <div className="bubble assistant">
                <div className="answer">{turn.reply.answer || <em>(empty answer)</em>}</div>
                <details>
                  <summary>
                    {turn.reply.steps.length} tool step(s) · {turn.reply.llm_calls} model call(s) ·{" "}
                    {turn.reply.prompt_tokens + turn.reply.completion_tokens} tokens
                    {turn.reply.stopped_reason !== "completed" && <> · <Badge tone="medium">{turn.reply.stopped_reason}</Badge></>}
                  </summary>
                  {turn.reply.steps.map((step, j) => (
                    <div key={j} className="step">
                      <div>
                        <code>{step.tool_name}</code> <span className="mono small">{JSON.stringify(step.arguments)}</span>{" "}
                        {step.is_error && <Badge tone="high">error</Badge>} <span className="muted small">{step.duration_ms} ms</span>
                      </div>
                      <pre className="code small">{step.output}</pre>
                    </div>
                  ))}
                </details>
              </div>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>
      <Status {...action} />
      <div className="row chat-input">
        <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={2} className="grow"
          placeholder="Ask the agent… (Enter to send, Shift+Enter for a new line)"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <div className="stack-tight">
          <button onClick={send} disabled={!!action.busy || !message.trim()}>Send</button>
          <button className="secondary" onClick={() => { setTurns([]); setConversationId(undefined); }}>New chat</button>
        </div>
      </div>
    </div>
  );
}
