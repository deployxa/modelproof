/** API access layer. Same-origin by contract: the dashboard is served either
 *  by the FastAPI engine itself (http://127.0.0.1:8899) or by the platform's
 *  same-origin proxy at /mp/ (requests proxied at /mp-api/v1). */

function apiBase(): string {
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/mp")) {
    return "/mp-api/v1";
  }
  return "/api/v1";
}

export const BASE = apiBase();

async function parseError(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return (body as { detail?: string }).detail || `${res.status} ${res.statusText}`;
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json() as Promise<T>;
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string }).detail || `${res.status} ${res.statusText}`);
  return data as T;
}

export async function deleteJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE", headers: { Accept: "application/json" } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string }).detail || `${res.status} ${res.statusText}`);
  return data as T;
}

// --------------------------------------------------------------------- types
export interface Health {
  ok: boolean;
  engine_version: string;
  database: { path: string; schema_version: string | null };
  runner: string;
  note: string;
}
export interface SuiteInfo {
  suite_id: string;
  version: string;
  path: string;
  content_hash: string;
  provenance_status: string;
  tasks: number;
  categories: string[];
  leakage: string;
  error?: string;
}
export interface SuiteValidation {
  suite_id: string;
  version: string;
  path: string;
  content_hash: string;
  provenance_status: string;
  tasks: { id: string; title: string; category: string; timeout_seconds: number }[];
  leakage: string;
  checks_performed: string[];
  checks_not_performed: string[];
  registered: boolean;
}
export interface ModelInfo {
  name: string;
  backend: string;
  endpoint: string | null;
  server_model_id: string | null;
  weights_dir: string | null;
  weights_manifest_hash: string | null;
  identity_verified: boolean;
  declared_notes: string | null;
  generation?: Record<string, unknown>;
}
export interface RunRow {
  id: string;
  status: string;
  verdict: string | null;
  model_name: string;
  suite_id: string;
  suite_version: string;
  is_fixture: boolean;
  imported_from?: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  error: string | null;
}
export interface GateRow {
  gate: string;
  outcome: string;
  detail: string;
  evidence_ref: string | null;
}
export interface CaseRow {
  task_id: string;
  category: string;
  status: string;
  is_infrastructure_failure: boolean;
  duration_ms: number | null;
  checks_passed: number;
  checks_total: number;
  failed_checks: string[];
  error_text: string | null;
  is_resumed: boolean;
}
export interface Summary {
  run_id: string;
  status: string;
  verdict: string;
  verdict_explanation: string;
  is_fixture: boolean;
  model_name: string;
  suite_id: string;
  suite_version: string;
  suite_content_hash: string;
  config_hash: string;
  prompt_template_hash: string;
  started_at: string | null;
  ended_at: string | null;
  provenance_status: string;
  leakage: string | null;
  pass_rate: number | null;
  pass_rate_numerator: number | null;
  pass_rate_denominator: number | null;
  completion_rate: number | null;
  wilson95: [number, number] | null;
  category_pass_rates: Record<string, number>;
  tasks_total: number;
  cases_terminal: number;
  passed: number;
  failed: number;
  timeouts: number;
  invalid_outputs: number;
  model_errors: number;
  sandbox_errors: number;
  environment?: { python?: string; platform?: string; modelproof_version?: string };
}
export interface RunDetailT extends RunRow {
  gates: GateRow[];
  summary?: Summary;
  config_json?: string;
}
export interface VerifyResult {
  run_id: string;
  verified: boolean;
  checked_artifacts: number;
  missing: string[];
  mismatched: string[];
  checks: { gate: string; ok: boolean; detail: string }[];
}
export interface EventRow { at: string; level: string; message: string }
export interface Diagnostics {
  engine_version: string;
  python: string;
  platform: string;
  database: { path: string; schema_version: string | null; tables: Record<string, number> };
  sandbox: { docker_available: boolean; detail: string; host_execution_fallback: string };
  inference: { local_only: boolean; note: string };
  telemetry: string;
}
export interface DoctorCheck { name: string; ok: boolean; label: string; detail: string }
export interface StorageInfo {
  artifacts_bytes: number;
  run_count: number;
  runs: { run_id: string; bytes: number; files: number }[];
  bundles_bytes: number;
  db_bytes: number;
  retention_policy: string;
}
export interface ReleaseGate {
  id: string;
  title: string;
  mandatory: boolean;
  status: string;
  detail: string;
  procedure: string;
  preconditions: string;
  evidence: { ref: string; detail: string }[];
}
export interface ReleaseReport {
  schema: string;
  generated_at: string;
  engine_version: string;
  host: { python: string; platform: string };
  qualifier_run_id: string | null;
  gates: ReleaseGate[];
  verdict: string;
  notes: string[];
}
export interface CompareResult {
  comparison_version?: string;
  run_a: string;
  run_b: string;
  compatible: boolean;
  refusal?: string;
  incompatibilities?: string[];
  warnings?: string[];
  compatibility?: { warnings: string[] };
  n_paired_tasks?: number;
  n_excluded_tasks?: number;
  pass_rate_a?: number | null;
  pass_rate_b?: number | null;
  both_passed?: number;
  both_failed?: number;
  a_only_passed?: string[];
  b_only_passed?: string[];
  discordant?: { a_passed_b_failed: number; a_failed_b_passed: number };
  mcnemar_exact_p?: number | null;
  bootstrap?: { ci: [number, number] | null; point_estimate?: number; level: number; n_resamples: number; note: string };
  caveats?: string[];
}
export interface BundleVerify {
  bundle_schema: string;
  run_id: string;
  verified: boolean;
  checked: number;
  missing: string[];
  mismatched: string[];
  meta?: Record<string, unknown>;
}
export interface ChecksumEntry { sha256: string; size: number }
export interface EvidenceExportResult {
  run_id: string;
  bundle_path: string;
  size_bytes: number;
  note: string;
}

// ----------------------------------------------------------------- endpoints
export const Api = {
  health: () => getJSON<Health>("/health"),
  suites: () => getJSON<{ suites: SuiteInfo[] }>("/suites"),
  suiteValidate: (path: string, also_register: boolean) =>
    postJSON<SuiteValidation>("/suites/validate", { path, also_register }),
  suiteChangelog: (id: string) => getJSON<{ versions: { version: string; content_hash: string; path: string; registered_at: string }[] }>(`/suites/${encodeURIComponent(id)}/changelog`),
  suiteOverlap: (a: string, b: string) =>
    getJSON<{
      suite_a: string; suite_b: string; status: string; tasks_compared: [number, number];
      exact_hash_overlaps: [string, string][]; normalized_overlaps: [string, string][];
      near_dup_candidates: [string, string, number][]; checks_performed: string[]; checks_not_performed: string[]; note: string;
    }>(`/suites/overlap?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  models: () => getJSON<{ models: ModelInfo[] }>("/models"),
  registerModel: (req: { name: string; endpoint: string; weights_dir?: string; server_model_id?: string; notes?: string }) =>
    postJSON<{ name: string; weights_manifest_hash: string | null; identity_verified: boolean }>("/models", req),
  deleteModel: (name: string) => deleteJSON<{ deleted: string }>(`/models/${encodeURIComponent(name)}`),
  testEndpoint: (name: string) => postJSON<{ name: string; health: { ok: boolean; detail?: string; error?: string } }>(`/models/${encodeURIComponent(name)}/test-endpoint`, {}),
  runs: (limit = 50, offset = 0) => getJSON<{ runs: RunRow[] }>(`/runs?limit=${limit}&offset=${offset}`),
  run: (id: string) => getJSON<RunDetailT>(`/runs/${encodeURIComponent(id)}`),
  runCases: (id: string) => getJSON<{ cases: CaseRow[] }>(`/runs/${encodeURIComponent(id)}/cases?limit=200`),
  runEvents: (id: string) => getJSON<{ events: EventRow[] }>(`/runs/${encodeURIComponent(id)}/events?limit=300`),
  runReport: (id: string) => getJSON<Summary>(`/runs/${encodeURIComponent(id)}/report`),
  createRun: (req: { model_name: string; suite_path: string; execution_mode: string; task_ids?: string[] | null; fixture_outputs_path?: string | null }) =>
    postJSON<{ run_id: string; status: string; is_fixture: boolean }>("/runs", req),
  cancelRun: (id: string) => postJSON<{ cancelled: boolean }>(`/runs/${encodeURIComponent(id)}/cancel`, {}),
  verifyRun: (id: string) => postJSON<VerifyResult>(`/runs/${encodeURIComponent(id)}/verify`, {}),
  compare: (a: string, b: string) => getJSON<CompareResult>(`/compare?run_a=${encodeURIComponent(a)}&run_b=${encodeURIComponent(b)}`),
  evidenceExport: (run_id: string) => postJSON<EvidenceExportResult>("/evidence/export", { run_id }),
  evidenceVerify: (path: string) => postJSON<BundleVerify>("/evidence/verify", { path }),
  evidenceImport: (path: string, new_run_id?: string) =>
    postJSON<{ run_id: string; original_run_id: string; checked: number }>("/evidence/import", { path, new_run_id: new_run_id || undefined }),
  doctor: (checkEndpoints = false) => getJSON<{ checks: DoctorCheck[] }>(`/doctor?check_endpoints=${checkEndpoints}`),
  diagnostics: () => getJSON<Diagnostics>("/diagnostics"),
  storage: () => getJSON<StorageInfo>("/storage"),
  gatesReport: () => getJSON<ReleaseReport>("/gates"),
  gatesRun: () => postJSON<ReleaseReport>("/gates/run", {}),
};
