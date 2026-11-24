// Words and times as the interface shows them.
import type { DisplayState, Waiting } from './api';

export const STATE_LABEL: Record<DisplayState, string> = {
  earlier_version: 'Earlier version',
  waiting: 'Waiting',
  passed: 'Passed',
  failed: 'Failed',
  did_not_run: 'Did not run',
  stalled: 'Stalled',
  queued: 'Queued',
  running: 'Running',
};

export const WAITING_LABEL: Record<Waiting['kind'], string> = {
  approval: 'Waiting for approval',
  escalation: 'Escalated to a person',
  'tool-calls': 'An agent needs an answer',
};

export const NEEDS_NAME = 'Type your name at the top first.';

/** A time in the reader's own zone, written the British way. */
export function when(iso: string | null): string {
  if (!iso) return 'an unknown time';
  return new Date(iso).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}

/** Why something failed, in words a person can read. */
export function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
