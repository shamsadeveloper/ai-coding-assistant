# AI Coding Assistant

A local AI coding assistant for any software repository. It reads and indexes a project, answers questions about the code with an agent that calls tools, finds bugs and improvements, writes unit tests, and prepares code changes as diffs. **Nothing is written to your project until you approve it.**

Everything runs on your machine: Python + FastAPI backend, a local model through [Ollama](https://ollama.com), SQLite (PostgreSQL-ready), and a React web UI. No paid AI APIs.

| | |
|---|---|
| Web UI | http://127.0.0.1:8001/ui/ |
| API docs (Swagger) | http://127.0.0.1:8001/docs |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Security model | [docs/SECURITY.md](docs/SECURITY.md) |

## Requirements

- Windows 10/11 (also runs on Linux/macOS)
- Python 3.12+ · Node.js 20+ (for the UI) · Git · Ollama
- 16 GB RAM for a 7B model on CPU

### Choosing a model

| Hardware | Model | Download |
|---|---|---|
| 8 GB RAM, no GPU | `qwen2.5-coder:3b` | ~1.9 GB |
| 16 GB RAM, no GPU | `qwen2.5-coder:7b` (default) | ~4.7 GB |
| GPU with 12 GB+ VRAM | `qwen2.5-coder:14b` | ~9 GB |

Check your hardware in PowerShell:

```powershell
(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB
Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM
```

On CPU, expect 20–90 seconds per answer. Set `APP_OLLAMA_MODEL` in `.env` to switch models.

## Setup and start (Command Prompt)

```cmd
cd D:\llm-learning\ai-coding-assistant
ollama pull qwen2.5-coder:7b
setup.cmd
start.cmd
```

`setup.cmd` creates `.venv`, installs packages, creates `.env`, applies database migrations, and builds the UI. `start.cmd` starts the server on **port 8001** (port 8000 is taken by Laragon on this machine).

Manual equivalent:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env
alembic upgrade head
cd frontend && npm install && npm run build && cd ..
uvicorn app.main:app --port 8001
```

### Developing the UI

Run the backend (`start.cmd`), then in a second terminal:

```cmd
cd frontend
npm run dev
```

Open http://127.0.0.1:5173. Vite forwards `/api` to port 8001 and reloads on every change.

## Using it

1. **Dashboard:** register a folder by absolute path (e.g. `D:/projects/my-app`). It is scanned and indexed.
2. **Chat:** ask questions. Every tool the agent used, with its arguments and output, is shown under the answer.
3. **Analysis:** pick a file (and optionally a function). *Explain*, *Find bugs* (severity + confirmed/possible), *Suggest improvements* (benefits + risks), *Duplicates*. Use **thorough** mode for one model call per function: slower, finds more.
4. **Tests:** see untested functions, generate tests into the review area, read them, then *Approve* (writes the file) or *Reject*. *Run* executes the configured test command.
5. **Changes > Change requests:** describe a change. Review the colored diff, then *Approve & apply* (new Git branch + backups), *Reject*, or later *Roll back*.
6. **Changes > Fix sessions:** describe a bug (or click *Investigate & fix* on a bug finding in Analysis). In the background the agent
   collects evidence (enclosing function, callers, imports, related tests), finds the root cause with read-only tools,
   verifies every quoted line against the real code, copies the repository to a sandbox and runs the tests (baseline),
   tries up to 3 patches, each checked (scope, size, syntax, no test edits, no secrets) and tested in the sandbox, with the
   test failures fed back into the next attempt, and finally reviews the diff. Follow it on the live timeline, press *Stop*
   at any time, then *Approve & apply* or *Reject*. Your repository is not touched until you approve.

## API overview

| Area | Endpoints |
|---|---|
| Health | `GET /health` (liveness), `GET /ready` (database + Ollama) |
| LLM | `GET /api/v1/llm/health`, `POST /api/v1/llm/chat` |
| Repositories | `POST/GET /api/v1/repositories`, `GET/DELETE /{id}`, `POST /{id}/scan`, `GET /{id}/files`, `GET /{id}/file`, `GET /{id}/search` |
| Agent | `POST /api/v1/agent/chat`, `GET /api/v1/agent/tools`, `GET /api/v1/agent/conversations/{id}` |
| Analysis | `GET /api/v1/analysis/outline`, `/dependencies`, `/duplicates`; `POST /explain`, `/bugs`, `/improvements`; `GET /runs`, `/runs/{id}` |
| Tests | `GET /api/v1/tests/frameworks`, `/gaps`; `POST /generate`; `GET /generated`; `POST /generated/{id}/approve`, `/reject`; `POST /run`; `GET /runs/{id}`; `POST /runs/{id}/analyze` |
| Changes | `POST/GET /api/v1/changes`, `GET /{id}`, `POST /{id}/approve`, `/reject`, `/rollback` |
| Git | `GET /api/v1/git/status`, `GET /api/v1/git/diff` |
| Fix sessions | `POST /api/v1/fix-sessions` (202, runs in background), `GET /api/v1/fix-sessions?repository_id=`, `GET /{id}`, `GET /{id}/events?after=`, `POST /{id}/cancel`, `/approve`, `/reject` |

Errors are JSON `{"detail": ...}`. Status codes: 400 invalid input or unsafe path · 403 sensitive file · 404 not found · 409 conflict (state, file changed, dirty Git tree) · 413 file too large · 422 validation · 429 a fix session is already running · 502/503/504 model errors.

### Fix-session limits (enforced by the backend)

| Setting | Default | Meaning |
|---|---|---|
| `APP_FIX_MAX_ATTEMPTS` | 3 (max 3) | patch attempts per session |
| `APP_FIX_MAX_LLM_CALLS` | 20 | model calls per session, all stages together |
| `APP_FIX_SESSION_TIMEOUT_SECONDS` | 1800 | wall-clock limit per session |
| `APP_FIX_INVESTIGATION_MAX_STEPS` | 4 | tool calls during the root-cause investigation |
| `APP_FIX_MAX_CHANGED_LINES` | 60 | larger patches are rejected as not minimal |
| `APP_FIX_MAX_CONCURRENT_SESSIONS` | 1 | extra requests get HTTP 429 |
| `APP_FIX_SANDBOX_MAX_FILES` / `_MAX_BYTES` | 20 000 / 200 MB | sandbox copy size limit |

Every response has an `X-Request-ID` header that also appears in the logs.

## Agent tools

All tools are limited to the selected repository and go through the same path checks. Their output is secret-masked before the model sees it.

| Tool | Effect |
|---|---|
| `get_repository_overview`, `list_project_files`, `read_file`, `search_code`, `find_function`, `analyze_file` | read-only |
| `generate_unit_test` | writes to the **review area** only |
| `run_project_tests` | runs the **configured** test command |
| `generate_code_patch` | creates a **proposal** only; the agent cannot approve it |
| `inspect_git_diff` | read-only |

## Tests

```cmd
pytest                 :: unit/API tests, no Ollama needed (about 1.5 minutes)
pytest -m integration  :: real round trips with Ollama and the configured model
pytest -m integration tests/test_fix_integration.py -s   :: live fix session on the known-bug repository (minutes on CPU)
cd frontend && npm run typecheck
```

## Database

- Development: tables are created automatically (`APP_DATABASE_AUTO_CREATE=true`).
- Production: set it to `false` and run `alembic upgrade head` on each deploy.
- After changing a model: `alembic revision --autogenerate -m "describe change"`, review the file in `migrations/versions/`, then `alembic upgrade head`.
- PostgreSQL: `pip install "psycopg[binary]"` and set `APP_DATABASE_URL=postgresql+psycopg://user:pass@host/db`.

## Project layout

```text
app/
  main.py                 app factory, request-id middleware, error-to-HTTP mapping, UI mount
  core/                   config, database, logging, secret redaction
  models/                 SQLAlchemy tables
  schemas/                API request/response models
  api/routes/             HTTP layer only
  agent/                  engine (loop), registry, tools, prompts, conversation store
  services/
    llm_service.py        the only code that talks to Ollama
    repository/           path guard, ignore rules, scanner, reader, search, safe writer
    code/                 Tree-sitter outlines, dependency graph, duplicates, syntax checks
    intelligence/         explanations, bug and improvement analysis
    testing/              frameworks, coverage gaps, generation, runner
    changes/              edit blocks, patch generation, Git, proposals, approval, rollback
    fixing/               fix sessions: evidence, investigator, sandbox, validation, retry loop, review, runner
frontend/                 React + TypeScript (Vite)
migrations/               Alembic
tests/                    pytest suite
docs/                     architecture and security
```
