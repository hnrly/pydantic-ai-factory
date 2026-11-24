import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { Conflict, type RunDetail } from '../src/api';
import { RunPage } from '../src/views/RunPage';
import { call, detail, fakeApi, job, renderWith, waiting, waitingRun } from './fake';

function showRun(run: RunDetail, extra: Parameters<typeof fakeApi>[0] = {}) {
  const api = fakeApi({ getRun: async () => run, ...extra });
  renderWith(<RunPage runId={run.run_id} name="Ada" />, api);
  return api;
}

const buttons = () => screen.queryAllByRole('button').map((button) => button.textContent);

describe('actions for each waiting kind', () => {
  test('a run waiting for approval offers approve, ask for changes and reject, and no answer box', async () => {
    showRun(waitingRun(waiting({ kind: 'approval' })));

    await screen.findByRole('button', { name: 'Approve' });
    expect(buttons()).toEqual(['Approve', 'Ask for changes', 'Reject']);
    expect(screen.queryByRole('radio')).toBeNull();
  });

  test('a run waiting on an escalation with work offers approve, ask for changes and reject', async () => {
    showRun(waitingRun(waiting({ kind: 'escalation', offers_work: true })));

    await screen.findByRole('button', { name: 'Approve' });
    expect(buttons()).toEqual(['Approve', 'Ask for changes', 'Reject']);
  });

  test('approve is hidden when an escalation offers no work', async () => {
    showRun(waitingRun(waiting({ kind: 'escalation', offers_work: false })));

    await screen.findByRole('button', { name: 'Reject' });
    expect(buttons()).toEqual(['Ask for changes', 'Reject']);
  });

  test("an agent's question gets an answer box and no approve, changes or reject", async () => {
    const question = call({ id: 'q1', question: 'Should finished tasks be hidden?' });
    showRun(waitingRun(waiting({ kind: 'tool-calls', questions: [question] })));

    expect(await screen.findByLabelText('Should finished tasks be hidden?')).toBeTruthy();
    expect(buttons()).toEqual(['Send answers']);
  });

  test('a tool call that needs sign-off shows approve and deny', async () => {
    const signOff = call({ id: 's1', tool: 'delete_file', args: { path: 'notes.md' } });
    showRun(waitingRun(waiting({ kind: 'tool-calls', sign_offs: [signOff] })));

    const group = await screen.findByRole('group', { name: /delete_file/ });
    expect(within(group).getByRole('radio', { name: 'Approve' })).toBeTruthy();
    expect(within(group).getByRole('radio', { name: 'Deny' })).toBeTruthy();
    expect(buttons()).toEqual(['Send answers']);
  });

  test('answers to questions and sign-offs are sent together as one reply', async () => {
    const run = waitingRun(
      waiting({
        kind: 'tool-calls',
        questions: [call({ id: 'q1', question: 'Which colour?' })],
        sign_offs: [call({ id: 's1', tool: 'delete_file' })],
      }),
    );
    const api = showRun(run, { answer: async () => ({ run_id: 'run-1', job_id: 'job-1' }), getJob: async () => job({ state: 'done' }) });

    await userEvent.type(await screen.findByLabelText('Which colour?'), 'Pink');
    await userEvent.click(screen.getByRole('radio', { name: 'Deny' }));
    await userEvent.click(screen.getByRole('button', { name: 'Send answers' }));

    expect(api.answer).toHaveBeenCalledWith('run-1', {
      choice: 'reply',
      by: 'Ada',
      revision: 3,
      replies: { q1: 'Pink' },
      approvals: { s1: false },
    });
  });

  test('asking for changes needs a note, which is sent with the name and revision', async () => {
    const api = showRun(waitingRun(waiting()), {
      answer: async () => ({ run_id: 'run-1', job_id: 'job-1' }),
      getJob: async () => job({ state: 'done' }),
    });

    const changes = (await screen.findByRole('button', { name: 'Ask for changes' })) as HTMLButtonElement;
    expect(changes.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText(/note/i), 'Add due dates.');
    await userEvent.click(changes);

    expect(api.answer).toHaveBeenCalledWith('run-1', {
      choice: 'changes',
      by: 'Ada',
      revision: 3,
      note: 'Add due dates.',
    });
  });
});

test('a stalled run offers only "start again", which queues the same mission', async () => {
  const run = detail({ state: 'stalled', outcome: 'running', actions: ['start_again'] });
  const api = showRun(run, { startRun: async () => ({ run_id: 'run-2', job_id: 'job-2' }) });

  await screen.findByRole('button', { name: 'Start again' });
  expect(buttons()).toEqual(['Start again']);
  await userEvent.click(screen.getByRole('button', { name: 'Start again' }));

  expect(api.startRun).toHaveBeenCalledWith('A to-do list web page.', 'Ada');
  expect(window.location.hash).toBe('#/runs/run-2?job=job-2');
});

test('a run from an earlier version of the factory shows no actions', async () => {
  const run = detail({ state: 'earlier_version', outcome: 'waiting', waiting: waiting(), actions: [] });
  showRun(run);

  expect(await screen.findByText(/earlier version of this factory/i)).toBeTruthy();
  expect(buttons()).toEqual([]);
  expect(screen.queryByRole('textbox')).toBeNull();
});

test('the run page shows the mission, the steps and the output rendered from Markdown', async () => {
  showRun(
    detail({
      output: '# To-do list\n\nTick tasks **off**.',
      steps: [{ station: 'design', attempt: 1, outcome: 'waiting', reason: null }],
    }),
  );

  expect(await screen.findByRole('heading', { name: 'To-do list' })).toBeTruthy();
  expect(screen.getByText('off').tagName).toBe('STRONG');
  expect(screen.getByText('A to-do list web page.')).toBeTruthy();
  expect(screen.getByText(/design, attempt 1/i)).toBeTruthy();
});

test('after an action the page follows its job, shows a failed job\'s reason and reloads the run', async () => {
  const states = [job({ state: 'queued' }), job({ state: 'claimed' }), job({ state: 'failed', error: 'The model refused.' })];
  const api = showRun(waitingRun(waiting()), {
    answer: async () => ({ run_id: 'run-1', job_id: 'job-7' }),
    getJob: async () => states.shift() ?? job({ state: 'failed', error: 'The model refused.' }),
  });

  await userEvent.click(await screen.findByRole('button', { name: 'Approve' }));

  expect(await screen.findByText(/the model refused\./i)).toBeTruthy();
  expect(api.getJob).toHaveBeenCalledWith('job-7');
  expect(api.getJob.mock.calls.length).toBeGreaterThanOrEqual(3);
  await waitFor(() => expect(api.getRun.mock.calls.length).toBeGreaterThanOrEqual(2));
});

test('a job given in the address is followed from the start', async () => {
  const api = fakeApi({
    getRun: async () => detail({ state: 'queued', outcome: null, revision: null }),
    getJob: async () => job({ kind: 'start', state: 'failed', error: 'No mission.' }),
  });
  renderWith(<RunPage runId="run-1" jobId="job-3" name="Ada" />, api);

  expect(await screen.findByText(/no mission\./i)).toBeTruthy();
  expect(api.getJob).toHaveBeenCalledWith('job-3');
});

test('a queued or running run is checked again until it waits', async () => {
  const runs = [detail({ state: 'queued', outcome: null }), detail({ state: 'running', outcome: 'running' })];
  const api = fakeApi({ getRun: async () => runs.shift() ?? waitingRun(waiting()) });
  renderWith(<RunPage runId="run-1" name="Ada" />, api);

  await screen.findByRole('button', { name: 'Approve' });
  const calls = api.getRun.mock.calls.length;
  expect(calls).toBe(3);
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(api.getRun.mock.calls.length).toBe(calls);
});

test('when someone answered first the page says so and shows the run as it is now', async () => {
  const now = detail({ state: 'running', outcome: 'running' });
  const runs = [waitingRun(waiting())];
  showRun(now, {
    getRun: async () => runs.shift() ?? now,
    answer: async () => {
      throw new Conflict('Someone answered first. This is the run as it is now.', now);
    },
  });

  await userEvent.click(await screen.findByRole('button', { name: 'Approve' }));

  expect(await screen.findByText(/someone answered first/i)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
});
