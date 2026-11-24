import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { ApiError, Conflict, type RunDetail } from '../src/api';
import { Services } from '../src/context';
import { App } from '../src/App';
import { RunPanel } from '../src/views/RunPanel';
import { board, call, card, detail, event, fakeApi, job, renderWith, waiting, waitingRun } from './fake';

function showRun(run: RunDetail, extra: Parameters<typeof fakeApi>[0] = {}, onClose = () => {}) {
  const api = fakeApi({ getRun: async () => run, ...extra });
  renderWith(<RunPanel runId={run.run_id} name="Ada" onClose={onClose} />, api);
  return api;
}

const buttons = () =>
  screen
    .queryAllByRole('button')
    .map((button) => button.textContent)
    .filter((text) => text !== '' && text !== 'Copy link');

describe('opening a run from the board', () => {
  function boardWithRun() {
    localStorage.setItem('factory.name', 'Ada');
    window.location.hash = '#/';
    const waits = card({
      column: 'station',
      station: 'design',
      state: 'waiting',
      needs_you: true,
      waiting: { kind: 'approval', station: 'design', since: '2026-11-18T10:00:00Z' },
    });
    return fakeApi({
      board: async () => board({ cards: [waits] }),
      getRun: async () => waitingRun(waiting()),
    });
  }

  test('opening a card shows the run as a panel over the board and sets the address to the run', async () => {
    renderWith(<App />, boardWithRun());

    await userEvent.click(await screen.findByRole('link', { name: 'A to-do list web page.' }));

    const panel = await screen.findByRole('dialog', { name: 'A to-do list web page.' });
    expect(window.location.hash).toBe('#/runs/run-1');
    expect(await within(panel).findByRole('button', { name: 'Approve' })).toBeTruthy();
    expect(document.querySelector('[aria-label="The software board"]')).not.toBeNull();
    expect(document.querySelector('.board-under')).not.toBeNull();
    await waitFor(() => expect(panel.contains(document.activeElement)).toBe(true));
  });

  test('Escape closes the panel, restores the board address and puts focus back on the card', async () => {
    renderWith(<App />, boardWithRun());
    await userEvent.click(await screen.findByRole('link', { name: 'A to-do list web page.' }));
    await screen.findByRole('dialog');

    await userEvent.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(window.location.hash).toBe('#/');
    await waitFor(() => expect(document.activeElement?.textContent).toBe('A to-do list web page.'));
  });

  test('opening a new run from a run, going back, then Escape returns to the board and no further', async () => {
    localStorage.setItem('factory.name', 'Ada');
    window.location.hash = '#/list?column=done';
    window.location.hash = '#/';
    await new Promise((resolve) => setTimeout(resolve, 0));
    const ended = card({ column: 'stopped', state: 'did_not_run', restarted_as: 'run-2' });
    const api = fakeApi({
      board: async () => board({ cards: [ended] }),
      getRun: async (runId) =>
        runId === 'run-1'
          ? detail({ column: 'stopped', state: 'did_not_run', restarted_as: 'run-2' })
          : waitingRun(waiting()),
    });
    renderWith(<App />, api);

    await userEvent.click(await screen.findByRole('link', { name: 'A to-do list web page.' }));
    await waitFor(() => expect(window.location.hash).toBe('#/runs/run-1'));
    const links = await screen.findAllByRole('link', { name: 'Open the new run' });
    await userEvent.click(links[0]!);
    await waitFor(() => expect(window.location.hash).toBe('#/runs/run-2'));
    await screen.findByRole('button', { name: 'Approve' });
    window.history.back();
    await waitFor(() => expect(window.location.hash).toBe('#/runs/run-1'));
    await screen.findAllByRole('link', { name: 'Open the new run' });

    await userEvent.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(window.location.hash).toBe('#/');
  });

  test('a link straight to a run opens the board with that panel, and the close button returns to the board', async () => {
    const api = boardWithRun();
    window.location.hash = '#/runs/run-1';
    renderWith(<App />, api);

    await screen.findByRole('dialog', { name: 'A to-do list web page.' });
    await userEvent.click(screen.getByRole('button', { name: 'Close and return to the board' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(window.location.hash).toBe('#/');
    expect(screen.getByRole('region', { name: 'Missions' })).toBeTruthy();
  });
});

describe('the one thing the run asks of a person', () => {
  test('a run waiting for approval offers Approve, Ask for changes and Reject, with Approve first', async () => {
    showRun(waitingRun(waiting({ kind: 'approval' })));

    expect(await screen.findByText('Needs your approval')).toBeTruthy();
    await screen.findByRole('button', { name: 'Approve' });
    expect(buttons()).toEqual(['Approve', 'Ask for changes', 'Reject']);
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  test('approve is hidden when an escalation offers no work', async () => {
    showRun(waitingRun(waiting({ kind: 'escalation', offers_work: false })));

    await screen.findByRole('button', { name: 'Reject' });
    expect(buttons()).toEqual(['Ask for changes', 'Reject']);
  });

  test('asking for changes opens a note box first, then sends the note with the name and revision', async () => {
    const api = showRun(waitingRun(waiting()), {
      answer: async () => ({ run_id: 'run-1', job_id: 'job-1' }),
      getJob: async () => job({ state: 'done' }),
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Ask for changes' }));
    const send = screen.getByRole('button', { name: 'Ask for changes' }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(api.answer).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText('What should change?'), 'Add due dates.');
    await userEvent.click(send);

    expect(api.answer).toHaveBeenCalledWith('run-1', { kind: 'changes', revision: 3, note: 'Add due dates.' }, 'Ada');
  });

  test('rejecting opens a note box first, and Cancel goes back to the three choices', async () => {
    showRun(waitingRun(waiting()));

    await userEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    expect(screen.getByLabelText('Why are you rejecting it?')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(buttons()).toEqual(['Approve', 'Ask for changes', 'Reject']);
  });

  test('the decision box says how many review rounds the evidence shows and how the last went', async () => {
    showRun({
      ...waitingRun(waiting()),
      history: [
        event({ kind: 'review', title: 'Goldfish round 2: clean', station: 'design' }),
        event({ kind: 'review', title: 'Goldfish round 1: 2 findings', station: 'design' }),
      ],
    });

    expect(await screen.findByText('Reviewed in 2 rounds. Last round: clean.')).toBeTruthy();
  });

  test("an agent's question shows as a message with a reply box, and sign-offs are sent with the reply", async () => {
    const run = waitingRun(
      waiting({
        kind: 'tool-calls',
        agent: 'designer',
        questions: [call({ id: 'q1', question: 'Should finished tasks be hidden?' })],
        sign_offs: [call({ id: 's1', tool: 'read_file', args: { path: 'README.md' } })],
      }),
    );
    const api = showRun(run, {
      answer: async () => ({ run_id: 'run-1', job_id: 'job-1' }),
      getJob: async () => job({ state: 'done' }),
    });

    expect(await screen.findByText('An agent asks you')).toBeTruthy();
    expect(screen.getByText('Should finished tasks be hidden?')).toBeTruthy();
    const send = screen.getByRole('button', { name: 'Send reply' }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText('Your reply'), 'Yes, hide them.');
    const signOff = screen.getByRole('group', { name: 'May the designer run read_file?' });
    await userEvent.click(within(signOff).getByRole('button', { name: 'Deny' }));
    expect(within(signOff).getByRole('button', { name: 'Deny' }).getAttribute('aria-pressed')).toBe('true');
    await userEvent.click(send);

    expect(api.answer).toHaveBeenCalledWith(
      'run-1',
      { kind: 'reply', revision: 3, replies: { q1: 'Yes, hide them.' }, approvals: { s1: false } },
      'Ada',
    );
  });

  test('a stalled run says why, offers Start again with its warning, and Keep waiting closes the panel', async () => {
    const onClose = vi.fn();
    const run = detail({
      column: 'stopped',
      state: 'stalled',
      actions: ['start_again'],
      track: [{ station: 'design', state: 'stopped', attempt: 1 }],
      job: job({ kind: 'start', state: 'claimed', claimed_until: '2026-11-18T09:47:00Z', stalled: true }),
    });
    const api = showRun(run, { restart: async () => ({ run_id: 'run-2', job_id: 'job-2' }) }, onClose);

    expect(await screen.findByText('The worker stopped answering while design was running')).toBeTruthy();
    expect(screen.getByText(/If the worker was only slow, anything it does from now on is thrown away\./)).toBeTruthy();
    expect(buttons()).toEqual(['Start again', 'Keep waiting']);

    await userEvent.click(screen.getByRole('button', { name: 'Keep waiting' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(api.restart).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Start again' }));
    expect(api.restart).toHaveBeenCalledWith('run-1', 'Ada');
  });

  test('a run that was started again says who started it again and links to the new run', async () => {
    showRun(
      detail({
        column: 'stopped',
        state: 'did_not_run',
        restarted_as: 'run-2',
        history: [event({ kind: 'restarted', title: 'Started again by Ben', by: 'Ben', run_id: 'run-2' })],
      }),
    );

    expect(await screen.findByText(/Started again by Ben at .*\. This run ended as did not run\./)).toBeTruthy();
    const links = screen.getAllByRole('link', { name: 'Open the new run' });
    expect(links.map((link) => link.getAttribute('href'))).toEqual(['#/runs/run-2', '#/runs/run-2']);
    expect(buttons()).toEqual([]);
  });

  test('a run from an earlier version of the factory shows no actions', async () => {
    showRun(detail({ column: 'stopped', state: 'earlier_version', wait: waiting(), actions: [] }));

    expect(await screen.findByText(/earlier version of this factory/i)).toBeTruthy();
    expect(buttons()).toEqual([]);
    expect(screen.queryByRole('textbox')).toBeNull();
  });

  test('a done run shows its output and history with no decision box', async () => {
    showRun(detail({ output: '# To-do list\n\nTick tasks **off**.', output_format: 'markdown' }));

    expect(await screen.findByText('Done', { selector: '.pill' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'To-do list' })).toBeTruthy();
    expect(screen.getByText('off').tagName).toBe('STRONG');
    expect(buttons()).toEqual([]);
  });

  test('an output shown as text keeps its characters and is not read as Markdown', async () => {
    showRun(detail({ output: '# Not a heading\n\nTick tasks **off**.', output_format: 'text' }));

    expect(await screen.findByText(/# Not a heading/)).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Not a heading' })).toBeNull();
    expect(screen.getByText(/\*\*off\*\*/).tagName).toBe('PRE');
  });
});

describe('the track and the history', () => {
  test('the track lists every station from the shape, in order, with the current one marked', async () => {
    showRun({
      ...waitingRun(waiting({ station: 'review' })),
      track: [
        { station: 'design', state: 'done', attempt: 1 },
        { station: 'review', state: 'here', attempt: 2 },
        { station: 'release', state: 'ahead', attempt: null },
      ],
    });

    const track = await screen.findByRole('navigation', { name: 'Where the work is' });
    const stops = within(track).getAllByRole('listitem');
    expect(stops.map((stop) => stop.textContent)).toEqual([
      'Mission',
      'design',
      'review · attempt 2 · waits for you',
      'release',
      'Done',
    ]);
    expect(stops.map((stop) => stop.getAttribute('aria-current'))).toEqual([null, null, 'step', null, null]);
  });

  test('the track is shown even when the factory has one station', async () => {
    showRun(waitingRun(waiting()));

    const track = await screen.findByRole('navigation', { name: 'Where the work is' });
    expect(within(track).getAllByRole('listitem').map((stop) => stop.textContent)).toEqual([
      'Mission',
      'design · attempt 1 · waits for you',
      'Done',
    ]);
  });

  test('the history lists answers with who gave them, newest first', async () => {
    showRun(
      detail({
        history: [
          event({ kind: 'answer', title: 'Approved by Ben', by: 'Ben', station: 'design', at: '2026-11-20T09:05:00Z' }),
          event({ kind: 'answer', title: 'Ada asked for changes', detail: 'Shorter.', by: 'Ada', station: 'design', at: '2026-11-20T09:00:00Z' }),
          event(),
        ],
      }),
    );

    const history = await screen.findByRole('complementary', { name: 'History' });
    const items = within(history).getAllByRole('listitem');
    expect(items.map((item) => item.querySelector('.event-title')?.textContent)).toEqual([
      'Approved by Ben',
      'Ada asked for changes',
      'Mission added by Ada',
    ]);
    expect(within(items[1]!).getByText('Shorter.')).toBeTruthy();
    expect(within(history).getByText('Technical detail')).toBeTruthy();
  });
});

test('after an answer the panel follows its job, shows a failed job\'s reason and reloads the run', async () => {
  const states = [job({ state: 'queued' }), job({ state: 'claimed' }), job({ state: 'failed', error: 'The model refused.' })];
  const api = showRun(waitingRun(waiting()), {
    answer: async () => ({ run_id: 'run-1', job_id: 'job-7' }),
    getJob: async () => states.shift() ?? job({ state: 'failed', error: 'The model refused.' }),
  });

  await userEvent.click(await screen.findByRole('button', { name: 'Approve' }));

  expect(await screen.findByText(/the model refused\./i)).toBeTruthy();
  expect(api.getJob).toHaveBeenCalledWith('job-7');
  await waitFor(() => expect(api.getRun.mock.calls.length).toBeGreaterThanOrEqual(2));
});

test('a job given in the address is followed from the start', async () => {
  const api = fakeApi({
    getRun: async () => detail({ column: 'station', station: 'design', state: 'queued', revision: null }),
    getJob: async () => job({ kind: 'start', state: 'failed', error: 'No mission.' }),
  });
  renderWith(<RunPanel runId="run-1" jobId="job-3" name="Ada" onClose={() => {}} />, api);

  expect(await screen.findByText(/no mission\./i)).toBeTruthy();
  expect(api.getJob).toHaveBeenCalledWith('job-3');
});

test("a queued or running run is checked quickly until it waits, then at the board's pace", async () => {
  const runs = [detail({ state: 'queued', column: 'station' }), detail({ state: 'running', column: 'station' })];
  const api = fakeApi({ getRun: async () => runs.shift() ?? waitingRun(waiting()) });
  render(
    <Services api={api} every={{ moving: 10, board: 200 }}>
      <RunPanel runId="run-1" name="Ada" onClose={() => {}} />
    </Services>,
  );

  await screen.findByRole('button', { name: 'Approve' });
  expect(api.getRun.mock.calls.length).toBe(3);
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(api.getRun.mock.calls.length).toBe(3);
  await waitFor(() => expect(api.getRun.mock.calls.length).toBe(4), { timeout: 1000 });
});

test('a waiting run is checked again while the panel is open, so an answer from someone else shows', async () => {
  const answered = detail({
    column: 'station',
    station: 'design',
    state: 'running',
    history: [event({ kind: 'answer', title: 'Approved by Ben', by: 'Ben', station: 'design' }), event()],
  });
  const runs = [waitingRun(waiting())];
  showRun(answered, { getRun: async () => runs.shift() ?? answered });

  await screen.findByRole('button', { name: 'Approve' });

  expect(await screen.findByText('Approved by Ben')).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
});

test('a run that does not exist is asked for once, and the panel says so with a way back to the board', async () => {
  const api = fakeApi({
    getRun: async () => {
      throw new ApiError(404, "There is no run 'run-9'.");
    },
  });
  renderWith(<RunPanel runId="run-9" name="Ada" onClose={() => {}} />, api);

  expect(await screen.findByText(/There is no run run-9\./)).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Back to the board' }).getAttribute('href')).toBe('#/');
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(api.getRun).toHaveBeenCalledTimes(1);
});

test('Escape in a note that holds text leaves the note first, and only a second Escape closes the panel', async () => {
  const onClose = vi.fn();
  showRun(waitingRun(waiting()), {}, onClose);

  await userEvent.click(await screen.findByRole('button', { name: 'Ask for changes' }));
  await userEvent.type(screen.getByLabelText('What should change?'), 'Add due dates.');
  await userEvent.keyboard('{Escape}');

  expect(onClose).not.toHaveBeenCalled();
  expect((screen.getByLabelText('What should change?') as HTMLTextAreaElement).value).toBe('Add due dates.');
  await userEvent.keyboard('{Escape}');
  expect(onClose).toHaveBeenCalledTimes(1);
});

test('when the run waits again at a new revision, the note form is closed and starts empty', async () => {
  let revision = 3;
  showRun(waitingRun(waiting()), { getRun: async () => ({ ...waitingRun(waiting()), revision }) });

  await userEvent.click(await screen.findByRole('button', { name: 'Ask for changes' }));
  await userEvent.type(screen.getByLabelText('What should change?'), 'Half a note');
  revision = 4;

  await waitFor(() => expect(screen.queryByLabelText('What should change?')).toBeNull());
  await userEvent.click(screen.getByRole('button', { name: 'Ask for changes' }));
  expect((screen.getByLabelText('What should change?') as HTMLTextAreaElement).value).toBe('');
});

test('when someone answered first the panel says so and shows the run as it is now', async () => {
  const now = detail({ state: 'running', column: 'station', station: 'design' });
  let answered = false;
  showRun(now, {
    getRun: async () => (answered ? now : waitingRun(waiting())),
    answer: async () => {
      answered = true;
      throw new Conflict('Someone answered first. This is the run as it is now.', now);
    },
  });

  await userEvent.click(await screen.findByRole('button', { name: 'Approve' }));

  expect(await screen.findByText(/someone answered first/i)).toBeTruthy();
  expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
});
