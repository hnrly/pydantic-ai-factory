// A faked API and run fixtures for the component tests.
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import type { Api, Job, RunDetail, RunSummary, ToolCall, Waiting } from '../src/api';
import { Services } from '../src/context';

export const FAST = { moving: 10, lists: 10 };

/** A fake API: every method is a mock that fails unless the test says what it returns. */
export function fakeApi(overrides: Partial<Api> = {}) {
  const refuse = (name: string) => () => Promise.reject(new Error(`The test did not expect ${name}.`));
  const api = {
    listRuns: vi.fn<Api['listRuns']>(refuse('listRuns')),
    getRun: vi.fn<Api['getRun']>(refuse('getRun')),
    startRun: vi.fn<Api['startRun']>(refuse('startRun')),
    answer: vi.fn<Api['answer']>(refuse('answer')),
    getJob: vi.fn<Api['getJob']>(refuse('getJob')),
  };
  for (const [name, impl] of Object.entries(overrides)) {
    (api[name as keyof Api] as ReturnType<typeof vi.fn>).mockImplementation(impl as never);
  }
  return api;
}

export function renderWith(ui: ReactElement, api: Api): RenderResult {
  return render(
    <Services api={api} every={FAST}>
      {ui}
    </Services>,
  );
}

const AT = '2026-11-18T10:00:00Z';

export function summary(fields: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: 'run-1',
    mission: 'A to-do list web page.',
    state: 'passed',
    outcome: 'passed',
    revision: 3,
    created_at: AT,
    updated_at: AT,
    last_change: AT,
    waiting: null,
    actions: [],
    ...fields,
  };
}

export function detail(fields: Partial<RunDetail> = {}): RunDetail {
  return { ...summary(), steps: [], answers: [], output: null, jobs: {}, ...fields };
}

export function waiting(fields: Partial<Waiting> = {}): Waiting {
  return {
    kind: 'approval',
    station: 'design',
    agent: null,
    reasons: [],
    offers_work: true,
    questions: [],
    sign_offs: [],
    ...fields,
  };
}

export function call(fields: Partial<ToolCall> = {}): ToolCall {
  return { id: 'call-1', tool: 'ask_person', args: {}, question: null, ...fields };
}

export function job(fields: Partial<Job> = {}): Job {
  return {
    id: 'job-1',
    kind: 'answer',
    run_id: 'run-1',
    body: {},
    state: 'queued',
    created_at: AT,
    claimed_at: null,
    finished_at: null,
    error: null,
    stalled: false,
    ...fields,
  };
}

/** A run that waits, with the actions the API gives each waiting kind. */
export function waitingRun(wait: Waiting): RunDetail {
  const actions: RunDetail['actions'] =
    wait.kind === 'tool-calls'
      ? ['reply']
      : wait.kind === 'escalation' && !wait.offers_work
        ? ['changes', 'reject']
        : ['approve', 'changes', 'reject'];
  return detail({ state: 'waiting', outcome: 'waiting', waiting: wait, actions, output: '# To-do list' });
}
