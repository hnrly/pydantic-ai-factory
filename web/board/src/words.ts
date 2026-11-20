// Words and times as the interface shows them.
import type { AnswerGiven, CardState, WaitingKind } from './api';

export const STATE_LABEL: Record<CardState, string> = {
  earlier_version: 'Earlier version',
  waiting: 'Waiting',
  passed: 'Passed',
  failed: 'Failed',
  did_not_run: 'Did not run',
  stalled: 'Stalled',
  queued: 'Queued',
  starting: 'Starting',
  running: 'Running',
};

export const WAITING_LABEL: Record<WaitingKind, string> = {
  approval: 'Waiting for approval',
  escalation: 'Escalated to a person',
  'tool-calls': 'An agent needs an answer',
};

export const ANSWER_LABEL: Record<AnswerGiven['kind'], string> = {
  approve: 'approved',
  changes: 'asked for changes',
  reject: 'rejected',
  reply: 'replied',
};

export const NEEDS_NAME = 'Give your name first: choose Change name at the top.';

export const BANNER = 'This is a name, not a sign-in. Anyone can type any name.';

export const STALLED_WARNING =
  'The worker may only be slow. If it is, anything it does from now on is discarded, and a new run of the same mission starts.';

/** A time in the reader's own zone, written the British way. */
export function when(iso: string): string {
  return new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}

/** How long since `iso`, roughly: "under a minute", "4 min", "3 h" or "2 days". */
export function lasting(iso: string, now: number = Date.now()): string {
  const minutes = Math.floor((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return 'under a minute';
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} days`;
}

/** How long ago `iso` was, roughly: "just now", "4 min ago" and so on. */
export function ago(iso: string, now: number = Date.now()): string {
  const since = lasting(iso, now);
  return since === 'under a minute' ? 'just now' : `${since} ago`;
}

/** Why something failed, in words a person can read. */
export function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The state pill on an opened run: what the run needs, in a few words. */
export type Pill =
  | 'approval'
  | 'asks'
  | 'stalled'
  | 'running'
  | 'queued'
  | 'done'
  | 'failed'
  | 'did_not_run'
  | 'earlier_version';

export const PILL_LABEL: Record<Pill, string> = {
  approval: 'Needs your approval',
  asks: 'An agent asks you',
  stalled: 'Stalled',
  running: 'Running',
  queued: 'Queued',
  done: 'Done',
  failed: 'Failed',
  did_not_run: 'Did not run',
  earlier_version: 'Earlier version',
};

/** Which pill a run shows. */
export function pillOf(state: CardState, waiting: WaitingKind | null): Pill {
  switch (state) {
    case 'waiting':
      return waiting === 'tool-calls' ? 'asks' : 'approval';
    case 'starting':
    case 'running':
      return 'running';
    case 'passed':
      return 'done';
    default:
      return state;
  }
}

/** A time of day, "10:02", or with the date when it was not today. */
export function clock(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  const time = at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (at.toDateString() === now.toDateString()) return time;
  return `${at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}, ${time}`;
}
