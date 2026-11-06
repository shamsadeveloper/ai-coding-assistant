# Architecture

## Layers

```text
Browser (React UI)  ──►  FastAPI routes (HTTP only)  ──►  Services (business logic)  ──►  Ollama / disk / Git / DB
                                                         ▲
                     Agent engine ── ToolRegistry ── Tools ┘
```

| Layer | Responsibility | Never does |
|---|---|---|
| `api/routes` | validate input (Pydantic), call one service, shape the response | business rules, file access |
| `services/*` | all logic; raise domain errors (`RepositoryError`, `LLMError`, ...) | know about HTTP |
| `services/llm_service.py` | the only code that talks to Ollama; normalises model quirks | decide what to do with answers |
| `agent/` | the tool-calling loop | touch files except through tools |
| `models/` + Alembic | persistence | logic |

`app/main.py` maps domain errors to HTTP status codes in one table, so routes contain no `try/except`.

## Request flow: an agent question

1. `POST /api/v1/agent/chat` calls `AgentService.ask`, which loads the repository and the last 10 messages of the conversation.
2. `AgentEngine.run` sends system prompt + history + question + tool schemas to the model.
3. If the model requests tools, `ToolRegistry.aexecute` validates the arguments (Pydantic), runs the tool, masks secrets in its output, truncates it, and appends it as a `tool` message.
4. Repeat until the model answers, at most `APP_AGENT_MAX_STEPS` times. After that, one final call without tools forces an answer.
5. The question, the answer and every tool call (audit log) are stored.

## Making a small local model reliable

These measures were added because of failures observed with `qwen2.5-coder:7b`:

| Observed problem | Countermeasure | Where |
|---|---|---|
| Tool calls written as JSON text instead of `tool_calls` | `parse_text_tool_calls`, which also handles calls embedded in prose | `llm_service.py` |
| Answers citing line numbers without looking at the code | Grounding check: one forced verification round | `agent/engine.py` |
| Stops after locating code, without reading it | Tool output suggests the next `read_file` call | `find_function` |
| Repeats the same tool call | Duplicate calls refused with a hint | `agent/engine.py` |
| Loops until "token repeat limit" when long code must be JSON | Code is generated as plain text; JSON schemas only for short structured results | testing, changes |
| Misses bugs in long files | `thorough` mode: one call per top-level symbol | `intelligence/service.py` |
| Hallucinated line numbers in findings | Findings outside the analysed range are discarded and counted | `intelligence/service.py` |
| Ignores the edit-block format and returns the whole file | Accepted only if at least 60% similar, with a reviewer warning | `changes/service.py` |
| Forgets `import pytest` | Deterministic fix after generation | `testing/service.py` |
| Generated code with syntax errors | Parse check plus one repair round | testing, changes |
| Unbounded generation | `num_predict` cap on every call | `llm_service.py` |

General rule: **the model proposes, deterministic code verifies.**

## Deterministic code intelligence

- **Index:** the scanner records readable text files (no secrets, no ignored or binary files). Reads and searches only use indexed files.
- **Tree-sitter** (`services/code/syntax.py`): symbols with line ranges and imports for Python, JS/TS, Go, Java, C#, PHP, Rust and Ruby.
  - Implementation note: `Node.start_point`/`end_point` crashed the garbage collector with tree-sitter 0.26 on Windows, so line numbers are computed from byte offsets. A subprocess regression test guards this.
- **Dependency graph:** Python and JS/TS imports resolved to repository files, in both directions.
- **Duplicates:** hashed windows of normalised lines.
- Whole-repository results are cached, keyed by the last scan time, so a rescan invalidates them.

## Writing to repositories

`SafeFileWriter` is the only code path that writes into a user's project:

1. Validate the target (inside the repository, not a secret, not in `.git` or a dependency folder).
2. Compare the file's SHA-256 with the hash from the proposal; refuse if it changed.
3. Back up the current file to `data/backups/...`.
4. Write.

Changes are all-or-nothing: if one file fails, earlier files are restored and the Git branch is removed.

## Fix sessions

`app/services/fixing/` reuses the existing services instead of replacing them:

```text
collect_context (evidence.py)      deterministic: enclosing function, callers, imports, related tests
RootCauseInvestigator              AgentEngine + read-only ToolRegistry, then a RootCauseReport (JSON)
verify_evidence                    every quoted line must exist near the cited line, else it is dropped;
                                   no verified evidence -> "unverified", no patch is attempted
Sandbox                            folder copy without secrets/.git/dependencies; baseline test run
attempt loop (<= 3)                generate_file_patch (shared with ChangeService) -> check_candidate
                                   -> sandbox tests -> compare_runs(baseline, after, bug test files)
                                   -> failure analysis fed into the next attempt; repeated patch stops
review                             model review (ReviewReport) + automatic checks
approve                            ChangeService.create_reviewed + ChangeService.approve
```

- **Judging a patch:** only *new* failures count against it, and when tests related to the bug were failing before, at least one of them must pass afterwards ("nothing broke" alone is not a fix).
- **Limits:** every model call goes through `BudgetedLLM` (call budget + stop flag); the whole run is inside `asyncio.timeout`; `FixSessionRunner` runs sessions as background tasks with a concurrency limit and a stop switch that also kills the test process.
- **Runs in the background:** the API returns 202 immediately; the UI polls `GET /{id}` and `/{id}/events`. A background task opens its own database session (`get_session_factory`).

## Data

SQLite by default, through SQLAlchemy 2. All tables are in `app/models`, and the schema is versioned with Alembic (`migrations/`). Tables: repositories, repository_files, conversations, conversation_messages, tool_call_logs, analysis_runs, analysis_findings, generated_tests, test_runs, change_sets, file_changes, fix_sessions, fix_attempts, fix_events.
