// The web process's JSON API, as the interface uses it. Types mirror app/api.py.

export type DisplayState =
  | 'earlier_version'
  | 'waiting'
  | 'passed'
  | 'failed'
  | 'did_not_run'
  | 'stalled'
  | 'queued'
  | 'running';

export type Action = 'approve' | 'changes' | 'reject' | 'reply' | 'start_again';

export interface ToolCall {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  question: string | null;
}

export interface Waiting {
  kind: 'approval' | 'escalation' | 'tool-calls';
  station: string;
  agent: string | null;
  reasons: string[];
  offers_work: boolean;
  questions: ToolCall[];
  sign_offs: ToolCall[];
}

export interface RunSummary {
  run_id: string;
  mission: string;
  state: DisplayState;
  outcome: string | null;
  revision: number | null;
  created_at: string | null;
  updated_at: string | null;
  last_change: string;
  waiting: Waiting | null;
  actions: Action[];
}

export interface Step {
  station: string;
  attempt: number;
  outcome: string;
  reason: string | null;
}

/** An answer a person gave to the run, as the run's history records it. */
export interface AnswerGiven {
  kind: 'approve' | 'changes' | 'reject' | 'reply';
  by: string;
  station: string;
  note: string | null;
  at: string;
}

export interface Job {
  id: string;
  kind: 'start' | 'answer';
  run_id: string;
  body: Record<string, unknown>;
  state: 'queued' | 'claimed' | 'done' | 'failed';
  created_at: string;
  claimed_at: string | null;
  finished_at: string | null;
  error: string | null;
  stalled: boolean;
}

export interface RunDetail extends RunSummary {
  steps: Step[];
  answers: AnswerGiven[];
  output: string | null;
  jobs: Record<string, Job>;
}

export interface RunList {
  runs: RunSummary[];
  more: boolean;
}

export interface Queued {
  run_id: string;
  job_id: string;
}

export interface Answer {
  choice: 'approve' | 'changes' | 'reject' | 'reply';
  by: string;
  revision: number;
  note?: string;
  replies?: Record<string, string>;
  approvals?: Record<string, boolean>;
}

export interface ListQuery {
  outcome?: 'waiting';
  order?: 'newest' | 'oldest';
  limit?: number;
}

/** Everything the interface asks of the web process. Tests pass a fake one. */
export interface Api {
  listRuns(query: ListQuery): Promise<RunList>;
  getRun(runId: string): Promise<RunDetail>;
  startRun(mission: string, by: string): Promise<Queued>;
  answer(runId: string, answer: Answer): Promise<Queued>;
  getJob(jobId: string): Promise<Job>;
}

/** The API refused a request. `message` says why, in words a person can read. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Someone answered first. `run` is the run as it is now. */
export class Conflict extends ApiError {
  readonly run: RunDetail;

  constructor(message: string, run: RunDetail) {
    super(409, message);
    this.name = 'Conflict';
    this.run = run;
  }
}

interface ValidationIssue {
  loc?: unknown[];
  msg?: string;
}

function reasonFrom(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return (detail as ValidationIssue[])
      .map((issue) => issue.msg ?? 'Invalid value')
      .join(' ');
  }
  return `The request failed with status ${status}.`;
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  });
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 409 && body && typeof body === 'object' && 'run' in body) {
    const conflict = body as { detail: string; run: RunDetail };
    throw new Conflict(conflict.detail, conflict.run);
  }
  if (!response.ok) throw new ApiError(response.status, reasonFrom(body, response.status));
  return body as T;
}

/** The real API, over `fetch`, on the same origin as the page. */
export const httpApi: Api = {
  listRuns({ outcome, order, limit }) {
    const query = new URLSearchParams();
    if (outcome) query.set('outcome', outcome);
    if (order) query.set('order', order);
    if (limit) query.set('limit', String(limit));
    return send(`/api/runs?${query}`);
  },
  getRun(runId) {
    return send(`/api/runs/${encodeURIComponent(runId)}`);
  },
  startRun(mission, by) {
    return send('/api/runs', { method: 'POST', body: JSON.stringify({ mission, by }) });
  },
  answer(runId, answer) {
    return send(`/api/runs/${encodeURIComponent(runId)}/answer`, {
      method: 'POST',
      body: JSON.stringify(answer),
    });
  },
  getJob(jobId) {
    return send(`/api/jobs/${encodeURIComponent(jobId)}`);
  },
};
