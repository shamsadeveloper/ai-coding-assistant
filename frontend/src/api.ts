// Typed client for the FastAPI backend. Types mirror app/schemas/*.py.

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(BASE + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) {
    let message = text || response.statusText;
    try {
      const detail = JSON.parse(text).detail;
      message = typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message);
  }
  if (!text) return undefined as T;
  return (response.headers.get("content-type")?.includes("json") ? JSON.parse(text) : text) as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body: unknown = {}) => request<T>("POST", path, body);
const qs = (params: Record<string, string | number | boolean | undefined | null>) =>
  "?" +
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();

// ----- types ---------------------------------------------------------------------------------

export interface ScanSummary {
  file_count: number;
  languages: Record<string, number>;
  frameworks: string[];
  skipped: Record<string, number>;
}
export interface Repository {
  id: number;
  name: string;
  root_path: string;
  created_at: string;
  last_scanned_at: string | null;
  scan_summary: ScanSummary | null;
}
export interface FileEntry { path: string; language: string | null; size_bytes: number; line_count: number }
export interface GitStatus { is_repository: boolean; branch: string | null; changes: string[] }
export interface LLMHealth { reachable: boolean; configured_model: string; model_available: boolean; available_models: string[] }

export interface AgentStep {
  iteration: number;
  tool_name: string;
  arguments: Record<string, unknown>;
  is_error: boolean;
  output: string;
  output_chars: number;
  duration_ms: number;
}
export interface AgentReply {
  conversation_id: number;
  answer: string;
  stopped_reason: string;
  model: string;
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  steps: AgentStep[];
}

export interface SymbolInfo { name: string; qualified_name: string; kind: string; start_line: number; end_line: number; signature: string }
export interface Outline { path: string; language: string | null; supported: boolean; has_syntax_errors: boolean; symbols: SymbolInfo[]; imports: string[] }
export interface Dependencies { path: string; imports: string[]; imported_by: string[]; external: string[] }
export interface DuplicateBlock { line_count: number; locations: { path: string; start_line: number; end_line: number }[]; preview: string }
export interface Explanation { path: string; symbol: string | null; start_line: number; end_line: number; model: string; explanation: string }
export interface Finding {
  id: number;
  source: string;
  title: string;
  category: string;
  severity: string | null;
  status: string | null;
  impact: string | null;
  line_start: number;
  line_end: number;
  explanation: string;
  suggestion: string;
  benefits: string | null;
  risks: string | null;
  snippet: string;
}
export interface AnalysisRun {
  id: number;
  kind: string;
  path: string;
  symbol: string | null;
  model: string;
  summary: string;
  discarded_findings: number;
  duration_ms: number;
  created_at: string;
  findings: Finding[];
}

export interface TestFramework { name: string; language: string; evidence: string }
export interface CoverageGap { path: string; language: string | null; test_files: string[]; untested_symbols: string[]; tested_symbols: string[] }
export interface GeneratedTest {
  id: number;
  source_path: string;
  symbol: string | null;
  framework: string;
  target_path: string;
  target_exists: boolean;
  syntax_ok: boolean;
  status: string;
  test_cases: string[];
  notes: string;
  content: string;
  backup_path: string | null;
  created_at: string;
}
export interface TestRun {
  id: number;
  framework: string;
  command: string[];
  target_path: string | null;
  exit_code: number | null;
  timed_out: boolean;
  passed: number | null;
  failed: number | null;
  errors: number | null;
  skipped: number | null;
  duration_ms: number;
  output: string;
  analysis: string | null;
}

export interface FileChange { path: string; is_new: boolean; diff: string; syntax_ok: boolean; notes: string; backup_path: string | null }
export interface ChangeSet {
  id: number;
  request: string;
  status: string;
  summary: string;
  model: string;
  base_branch: string | null;
  branch_name: string | null;
  created_at: string;
  files: FileChange[];
}

export interface Check { name: string; ok: boolean; detail: string }
export interface Evidence { path: string; line: number; quote: string; explanation: string }
export interface RejectedEvidence { path: string; line: number; quote: string; reason: string }
export interface RootCause {
  is_real_bug: boolean;
  summary: string;
  location: string;
  fix_strategy: string;
  model_confidence: string;
  confidence: string;
  files_to_change: string[];
  evidence: Evidence[];
  rejected_evidence: RejectedEvidence[];
  notes: string;
}
export interface TestResult {
  framework: string;
  exit_code: number | null;
  timed_out: boolean;
  passed: number | null;
  failed: number | null;
  errors: number | null;
  skipped: number | null;
  duration_ms: number;
  failed_ids: string[] | null;
  output: string;
}
export interface Comparison { ok: boolean; reason: string; new_failures: string[]; fixed: string[]; still_failing: string[] }
export interface FixAttempt {
  number: number;
  status: string;
  explanation: string;
  files: { path: string; diff: string; syntax_ok: boolean; notes: string }[];
  checks: Check[];
  test: TestResult | null;
  comparison: Comparison | null;
  failure_analysis: string | null;
  duration_ms: number;
}
export interface Review {
  verdict: string;
  summary: string;
  issues: { severity: string; description: string }[];
  automated: Check[];
}
export interface FixSessionSummary {
  id: number;
  finding_id: number | null;
  description: string;
  path: string;
  status: string;
  validation_status: string | null;
  change_set_id: number | null;
  llm_calls: number;
  created_at: string;
  finished_at: string | null;
}
export interface FixSession extends FixSessionSummary {
  line_start: number | null;
  line_end: number | null;
  options: { run_tests: boolean; reproduction_test: boolean };
  validation_note: string;
  context: { symbol: string | null; callers: { path: string; line: number; text: string }[]; imported_by: string[]; related_tests: string[] } | null;
  root_cause: RootCause | null;
  baseline: TestResult | null;
  reproduction: { path: string; content: string; kept: boolean; note: string } | null;
  review: Review | null;
  final_attempt: number | null;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  error: string | null;
  attempts: FixAttempt[];
}
export interface FixEvent { id: number; stage: string; message: string; duration_ms: number | null; llm_calls: number; created_at: string }
export interface StartFix {
  finding_id?: number;
  description?: string;
  path?: string;
  line_start?: number;
  run_tests?: boolean;
  reproduction_test?: boolean;
}
export const ACTIVE_FIX_STATUSES = ["queued", "investigating", "fixing", "validating", "reviewing"];

export type AnalysisMode = "fast" | "thorough";

// ----- endpoints -----------------------------------------------------------------------------

export const api = {
  llmHealth: () => get<LLMHealth>("/llm/health"),

  repositories: () => get<Repository[]>("/repositories"),
  registerRepository: (path: string) => post<Repository>("/repositories", { path }),
  rescan: (id: number) => post<Repository>(`/repositories/${id}/scan`),
  removeRepository: (id: number) => request<void>("DELETE", `/repositories/${id}`),
  files: (id: number, limit = 1000) => get<{ total: number; items: FileEntry[] }>(`/repositories/${id}/files${qs({ limit })}`),
  gitStatus: (id: number) => get<GitStatus>(`/git/status${qs({ repository_id: id })}`),

  chat: (repositoryId: number, message: string, conversationId?: number) =>
    post<AgentReply>("/agent/chat", { repository_id: repositoryId, message, conversation_id: conversationId }),

  outline: (id: number, path: string) => get<Outline>(`/analysis/outline${qs({ repository_id: id, path })}`),
  dependencies: (id: number, path: string) => get<Dependencies>(`/analysis/dependencies${qs({ repository_id: id, path })}`),
  duplicates: (id: number) => get<DuplicateBlock[]>(`/analysis/duplicates${qs({ repository_id: id })}`),
  explain: (id: number, path: string, symbol?: string) =>
    post<Explanation>("/analysis/explain", { repository_id: id, path, symbol: symbol || null }),
  bugs: (id: number, path: string, symbol: string | undefined, mode: AnalysisMode) =>
    post<AnalysisRun>("/analysis/bugs", { repository_id: id, path, symbol: symbol || null, mode }),
  improvements: (id: number, path: string, symbol: string | undefined, mode: AnalysisMode) =>
    post<AnalysisRun>("/analysis/improvements", { repository_id: id, path, symbol: symbol || null, mode }),

  frameworks: (id: number) => get<TestFramework[]>(`/tests/frameworks${qs({ repository_id: id })}`),
  gaps: (id: number) => get<CoverageGap[]>(`/tests/gaps${qs({ repository_id: id })}`),
  generateTest: (id: number, path: string, symbol?: string) =>
    post<GeneratedTest>("/tests/generate", { repository_id: id, path, symbol: symbol || null }),
  generatedTests: (id: number) => get<GeneratedTest[]>(`/tests/generated${qs({ repository_id: id })}`),
  approveTest: (testId: number, overwrite: boolean) => post<GeneratedTest>(`/tests/generated/${testId}/approve`, { overwrite }),
  rejectTest: (testId: number) => post<GeneratedTest>(`/tests/generated/${testId}/reject`),
  runTests: (id: number, path?: string) => post<TestRun>("/tests/run", { repository_id: id, path: path || null }),
  analyzeRun: (runId: number) => post<TestRun>(`/tests/runs/${runId}/analyze`),

  changes: (id: number) => get<ChangeSet[]>(`/changes${qs({ repository_id: id })}`),
  proposeChange: (id: number, requestText: string, paths: string[]) =>
    post<ChangeSet>("/changes", { repository_id: id, request: requestText, paths: paths.length ? paths : null }),
  approveChange: (changeId: number, createBranch: boolean) =>
    post<ChangeSet>(`/changes/${changeId}/approve`, { create_branch: createBranch }),
  rejectChange: (changeId: number) => post<ChangeSet>(`/changes/${changeId}/reject`),
  rollbackChange: (changeId: number) => post<ChangeSet>(`/changes/${changeId}/rollback`),

  startFix: (id: number, body: StartFix) => post<FixSession>("/fix-sessions", { repository_id: id, ...body }),
  fixSessions: (id: number) => get<FixSessionSummary[]>(`/fix-sessions${qs({ repository_id: id })}`),
  fixSession: (sessionId: number) => get<FixSession>(`/fix-sessions/${sessionId}`),
  fixEvents: (sessionId: number, after = 0) => get<FixEvent[]>(`/fix-sessions/${sessionId}/events${qs({ after })}`),
  stopFix: (sessionId: number) => post<FixSession>(`/fix-sessions/${sessionId}/cancel`),
  approveFix: (sessionId: number, createBranch: boolean) =>
    post<FixSession>(`/fix-sessions/${sessionId}/approve`, { create_branch: createBranch }),
  rejectFix: (sessionId: number) => post<FixSession>(`/fix-sessions/${sessionId}/reject`),
};
