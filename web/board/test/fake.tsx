// A faked API and card fixtures for the component tests.
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import type { Api, Board, Card, FactoryShape, Job, RunDetail, ToolCall, Waiting } from '../src/api';
import { Services } from '../src/context';

export const FAST = { moving: 10, board: 10 };

/** A fake API: every method is a mock that fails unless the test says what it returns. */
export function fakeApi(overrides: Partial<Api> = {}) {
  const refuse = (name: string) => () => Promise.reject(new Error(`The test did not expect ${name}.`));
  const api = {
    board: vi.fn<Api['board']>(refuse('board')),
    listCards: vi.fn<Api['listCards']>(refuse('listCards')),
    getRun: vi.fn<Api['getRun']>(refuse('getRun')),
    startRun: vi.fn<Api['startRun']>(refuse('startRun')),
    answer: vi.fn<Api['answer']>(refuse('answer')),
    restart: vi.fn<Api['restart']>(refuse('restart')),
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

export function shape(stations: string[] = ['design']): FactoryShape {
  return {
    name: 'software',
    version: '1',
    digest: 'abc',
    stations: stations.map((name) => ({ name, version: '1', approval: false })),
  };
}

export function card(fields: Partial<Card> = {}): Card {
  return {
    run_id: 'run-1',
    mission: 'A to-do list web page.',
    column: 'done',
    station: null,
    state: 'passed',
    queued: false,
    needs_you: false,
    waiting: null,
    rework: null,
    revision: 3,
    updated_at: AT,
    restarted_as: null,
    reason: null,
    actions: [],
    ...fields,
  };
}

export function board(fields: Partial<Board> = {}): Board {
  return { shape: shape(), cards: [], since: AT, more: { done: false, stopped: false }, ...fields };
}

export function detail(fields: Partial<RunDetail> = {}): RunDetail {
  return { ...card(), wait: null, output: null, output_format: null, steps: [], answers: [], job: null, ...fields };
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
    job_id: 'job-1',
    run_id: 'run-1',
    kind: 'answer',
    state: 'queued',
    by: 'Ada',
    restart_of: null,
    refused: false,
    error: null,
    created_at: AT,
    updated_at: AT,
    claimed_until: null,
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
  return detail({
    column: 'station',
    station: wait.station,
    state: 'waiting',
    needs_you: true,
    waiting: { kind: wait.kind, station: wait.station, since: AT },
    wait,
    actions,
    output: '# To-do list',
    output_format: 'markdown',
  });
}
