# Security model

The assistant reads and, with approval, modifies source code on your machine. The model is treated as **untrusted**: it can only request named tools, and every effect is checked by deterministic code.

## What the model can and cannot do

| | |
|---|---|
| Can | call the tools in `app/agent/tools/__init__.py` with arguments validated against their schema |
| Cannot | run shell commands, choose a test command, read outside the selected repository, read secret files, apply a patch or a generated test |

## File access

- Repositories are registered by absolute path. Drive roots are refused, and `APP_ALLOWED_REPOSITORY_ROOTS` can restrict which folders are allowed.
- Every path goes through `resolve_inside()`, which rejects absolute paths, `..`, drive letters, UNC paths, NUL bytes, NTFS alternate data streams (`file:stream`), and symlinks or junctions that resolve outside the repository.
- Only files in the index can be read or searched. The scanner excludes:
  - dependency, build and cache folders, and any virtualenv (detected by `pyvenv.cfg`);
  - everything matched by `.gitignore` files;
  - binaries and files over `APP_MAX_FILE_BYTES`;
  - sensitive files: `.env*` (except `.env.example`-style templates), `*.pem`, `*.key`, `id_rsa*`, `credentials.json`, `.npmrc`, `*.tfvars`, and more.

## Secrets inside ordinary files

`app/core/secrets.py` masks API keys (AWS, GitHub, OpenAI-style, Slack, Google), JWTs, private-key blocks, `password = "..."`-style literals and credentials in URLs. Masking is applied to:

- every agent tool output;
- code sent for explanation, bug analysis and test generation;
- files sent for patch generation. An edit that would write the mask placeholder is dropped, and the whole-file fallback is disabled for such files;
- stored test-run output.

The developer's own `/repositories/{id}/file` view is not masked.

## Writes

- Generated tests go to `data/review/` first. Approval writes them, and an existing file is replaced only with `overwrite=true`, after a backup.
- Patches are proposals with a diff. Approval checks file hashes (a conflict returns 409) and refuses if Git shows uncommitted changes in the touched files. It creates the branch `ai-assistant/change-<id>`, backs up every file, and writes all files or none.
- Rollback restores the backups and only does so if the files still contain the applied content.

## Running tests

- Only commands in `APP_TEST_COMMANDS` run, selected by framework name. The model never supplies a command.
- No shell is used (`shell=False`). An optional target must be an indexed repository file.
- Commands have a timeout (`APP_TEST_TIMEOUT_SECONDS`) and their output size is capped.
- The test process gets a minimal environment (PATH, temp folders and similar), so API keys in the server's environment are not inherited.
- `APP_TEST_EXECUTION_ENABLED=false` disables execution entirely.

Running a project's tests still executes that project's code: only run tests for repositories you trust.

## Fix sessions (sandboxed validation)

- A fix session **never writes to the repository** during investigation, patching, retries or testing. Candidate patches are written only into a copy under `data/sandboxes/<id>/repo`, which is deleted when the session ends (and at startup, for sessions interrupted by a restart).
- The copy leaves out `.git`, virtualenvs and dependency folders, `.gitignore`d files, secret files (`.env`, keys, credentials), symlinks/junctions, and the assistant's own `data/` folder.
- Tests in the sandbox run with the same allowlisted command, `shell=False`, timeout and minimal environment as normal test runs; in addition `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `TEMP` and `TMP` point to an empty folder, and `PYTHONPATH` points at the copy so an editable install of the original package cannot be tested instead of the patch.
- Only Python (pytest/unittest) tests run in the sandbox. Other frameworks need dependency folders that are not copied; their patches are shown as **Not validated**, never tested against the real repository.
- The investigator has read-only tools only. Patches may only touch files the verified root cause names, must stay under a line limit, must not edit test files, and must not add secrets or masked placeholders.
- Limits on attempts (max 3), model calls, total time and concurrency are enforced in the backend (`BudgetedLLM`, `asyncio.timeout`, `FixSessionRunner`). Stop kills a running test process.
- Approval turns the final patch into a normal change set: SHA-256 conflict check against the files as they were when the session started, Git branch, backups, all-or-nothing write, rollback. Nothing is ever pushed.
- Every stage is recorded in `fix_events` (the session timeline), with every attempt's diff, checks and test result in `fix_attempts`.

The sandbox is process-level isolation, not an operating-system sandbox: a test can still read absolute paths or use the network. Run fix sessions only on repositories you trust, as with normal test runs.

## Operations

- Every request gets an `X-Request-ID` (an incoming safe value is reused), and it is logged with the method, path, status and duration.
- Unexpected errors return `{"detail": "Internal server error", "request_id": ...}`. Internals are logged, not returned.
- Every agent tool call is stored in `tool_call_logs`, and every change set and generated test is kept with its status history.

## Known limits

- Redaction is pattern-based and cannot catch every secret format. Keep secrets in excluded files such as `.env`.
- Coverage gaps and framework detection are heuristics.
- The API has no authentication. It is meant for one developer on `localhost`: do not expose port 8001 on a network.
