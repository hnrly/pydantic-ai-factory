// The board's JSON API, as the interface uses it. Types mirror serve/_board/views.py.

export type CardState =
  | 'passed'
  | 'earlier_version'
  | 'waiting'
  | 'failed'
  | 'did_not_run'
  | 'stalled'
  | 'queued'
  | 'starting'
  | 'running';

export type Action = 'approve' | 'changes' | 'reject' | 'reply' | 'start_again';

export type WaitingKind = 'approval' | 'escalation' | 'tool-calls';

/** One run, or one mission queued but not yet started, as the board shows it. */
export interface Card {
  run_id: string;
  mission: string;
  /**
   * Missions, for a mission whose run is not saved yet; a station's column, with the
   * station in `station`; or Done or Stopped.
   */
  column: 'missions' | 'station' | 'done' | 'stopped';
  station: string | null;
  state: CardState;
  /** An answer is queued, or claimed by a worker whose claim has not lapsed. */
  queued: boolean;
  /** The card waits for a person, and no answer is queued. */
  needs_you: boolean;
  waiting: { kind: WaitingKind; station: string; since: string } | null;
  rework: { count: number; limit: number } | null;
  revision: number | null;
  updated_at: string;
  restarted_as: string | null;
  reason: string | null;
  actions: Action[];
}

export interface StationShape {
  name: string;
  version: string;
  approval: boolean;
}

export interface FactoryShape {
  name: string;
  version: string;
  digest: string;
  stations: StationShape[];
}

export interface Board {
  shape: FactoryShape;
  cards: Card[];
  /** What to send as `since` on the next poll. */
  since: string;
  /** On a full load, whether Done and Stopped hold more cards than were sent. */
  more: { done: boolean; stopped: boolean } | null;
}

export type ListColumn = 'all' | 'done' | 'stopped';

export interface CardPage {
  cards: Card[];
  /** Send back as `before` for the next page, or null when there is none. */
  before: string | null;
}

export interface ToolCall {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  question: string | null;
}

export interface Waiting {
  kind: WaitingKind;
  station: string;
  agent: string | null;
  reasons: string[];
  offers_work: boolean;
  questions: ToolCall[];
  sign_offs: ToolCall[];
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
  job_id: string;
  run_id: string;
  kind: 'start' | 'answer';
  state: 'queued' | 'claimed' | 'done' | 'failed';
  by: string;
  restart_of: string | null;
  refused: boolean;
  error: string | null;
  created_at: string;
  updated_at: string;
  claimed_until: string | null;
  stalled: boolean;
}

/** One station on a run's track, and which attempt the work is on or ended with there. */
export interface TrackStop {
  station: string;
  state: 'done' | 'here' | 'ahead' | 'stopped';
  attempt: number | null;
}

export type HistoryKind =
  | 'mission'
  | 'attempt'
  | 'review'
  | 'evidence'
  | 'gate'
  | 'waiting'
  | 'answer'
  | 'stalled'
  | 'ended'
  | 'restarted'
  | 'done'
  | 'failed';

/** One thing that happened to a run. `at` is null when the records do not say when. */
export interface HistoryEvent {
  kind: HistoryKind;
  title: string;
  detail: string | null;
  station: string | null;
  attempt: number | null;
  by: string | null;
  at: string | null;
  /** Another run this event links to, such as the run a restart began. */
  run_id: string | null;
}

export interface RunDetail extends Card {
  wait: Waiting | null;
  output: string | null;
  output_format: 'text' | 'markdown' | 'json' | null;
  steps: Step[];
  answers: AnswerGiven[];
  job: Job | null;
  /** Who added the mission, and when. */
  started_by: string | null;
  created_at: string | null;
  /** When a waiting run began to wait. */
  waiting_since: string | null;
  factory_version: string | null;
  /** Every station of the factory, in order, with where the work is. */
  track: TrackStop[];
  /** What happened to the run, newest first. */
  history: HistoryEvent[];
}

export interface Queued {
  run_id: string;
  job_id: string;
}

/** What a person decided. Who decided is sent in a header, not here. */
export interface Answer {
  kind: 'approve' | 'changes' | 'reject' | 'reply';
  revision: number;
  note?: string;
  replies?: Record<string, string>;
  approvals?: Record<string, boolean>;
}

/** Everything the interface asks of the web process. Tests pass a fake one. */
export interface Api {
  board(since?: string): Promise<Board>;
  listCards(column: ListColumn, before?: string): Promise<CardPage>;
  getRun(runId: string): Promise<RunDetail>;
  startRun(mission: string, by: string): Promise<Queued>;
  answer(runId: string, answer: Answer, by: string): Promise<Queued>;
  restart(runId: string, by: string): Promise<Queued>;
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

/** Someone acted first. `run` is the run as it is now. */
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
    return (detail as ValidationIssue[]).map((issue) => issue.msg ?? 'Invalid value').join(' ');
  }
  return `The request failed with status ${status}.`;
}

/**
 * The header that carries the typed name. Browsers refuse header values outside
 * Latin-1, so the name is percent-encoded, and the server decodes it.
 */
export function nameHeader(by: string): Record<string, string> {
  return { 'X-Factory-Name': encodeURIComponent(by) };
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
  board(since) {
    return send(since ? `/api/board?${new URLSearchParams({ since })}` : '/api/board');
  },
  listCards(column, before) {
    const query = new URLSearchParams({ column });
    if (before) query.set('before', before);
    return send(`/api/runs?${query}`);
  },
  getRun(runId) {
    return send(`/api/runs/${encodeURIComponent(runId)}`);
  },
  startRun(mission, by) {
    return send('/api/runs', { method: 'POST', body: JSON.stringify({ mission }), headers: nameHeader(by) });
  },
  answer(runId, answer, by) {
    return send(`/api/runs/${encodeURIComponent(runId)}/answer`, {
      method: 'POST',
      body: JSON.stringify(answer),
      headers: nameHeader(by),
    });
  },
  restart(runId, by) {
    return send(`/api/runs/${encodeURIComponent(runId)}/restart`, { method: 'POST', headers: nameHeader(by) });
  },
  getJob(jobId) {
    return send(`/api/jobs/${encodeURIComponent(jobId)}`);
  },
};
