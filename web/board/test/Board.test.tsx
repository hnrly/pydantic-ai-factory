import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { nameHeader, type Board } from '../src/api';
import { merge } from '../src/board';
import { BoardView } from '../src/views/BoardView';
import { board, card, fakeApi, renderWith, shape } from './fake';

const columnTitles = () =>
  screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.firstChild?.textContent?.trim());

test('columns render in factory order', async () => {
  const api = fakeApi({ board: async () => board({ shape: shape(['draft', 'review', 'publish']) }) });
  renderWith(<BoardView name="Ada" />, api);

  await screen.findByRole('region', { name: 'draft' });
  expect(columnTitles()).toEqual(['draft', 'review', 'publish', 'Done', 'Stopped']);
});

test('a card with an unchanged updated_at moves from running to stalled', async () => {
  const at = '2026-11-20T09:00:00Z';
  const running = card({ run_id: 'r', mission: 'A weekly report.', column: 'station', station: 'design', state: 'running', updated_at: at });
  const stalled = { ...running, column: 'stopped' as const, station: null, state: 'stalled' as const, actions: ['start_again' as const] };
  const polls: Board[] = [board({ cards: [running], since: 'first' }), board({ cards: [stalled], since: 'second', more: null })];
  const api = fakeApi({ board: async () => polls.shift() ?? board({ cards: [], since: 'later', more: null }) });
  renderWith(<BoardView name="Ada" />, api);

  const design = await screen.findByRole('region', { name: 'design' });
  await within(design).findByText('A weekly report.');
  const stopped = screen.getByRole('region', { name: 'Stopped' });
  await within(stopped).findByText('A weekly report.');
  expect(within(screen.getByRole('region', { name: 'design' })).queryByText('A weekly report.')).toBeNull();
  expect(within(stopped).getByText('Stalled')).toBeTruthy();
  expect(api.board).toHaveBeenNthCalledWith(1, undefined);
  expect(api.board).toHaveBeenNthCalledWith(2, 'first');
});

test('"Needs you" shows waiting cards, longest wait first', async () => {
  const wait = (since: string) => ({ kind: 'approval' as const, station: 'design', since });
  const cards = [
    card({ run_id: 'b', mission: 'Waiting an hour', column: 'station', station: 'design', state: 'waiting', needs_you: true, waiting: wait('2026-11-20T09:00:00Z'), updated_at: '2026-11-20T09:00:00Z' }),
    card({ run_id: 'a', mission: 'Waiting two hours', column: 'station', station: 'design', state: 'waiting', needs_you: true, waiting: wait('2026-11-20T08:00:00Z'), updated_at: '2026-11-20T08:00:00Z' }),
    card({ run_id: 'q', mission: 'Answer queued', column: 'station', station: 'design', state: 'waiting', queued: true, waiting: wait('2026-11-20T07:00:00Z'), updated_at: '2026-11-20T07:00:00Z' }),
    card({ run_id: 'r', mission: 'Running', column: 'station', station: 'design', state: 'running' }),
  ];
  const api = fakeApi({ board: async () => board({ cards }) });
  renderWith(<BoardView name="Ada" />, api);

  await userEvent.click(await screen.findByRole('checkbox', { name: /needs you/i }));

  const needsYou = screen.getByRole('region', { name: 'Needs you' });
  const shown = within(needsYou)
    .getAllByRole('article')
    .map((article) => article.getAttribute('aria-label'));
  expect(shown).toEqual(['Waiting two hours', 'Waiting an hour']);
});

test('a polled copy older than the card held is ignored', () => {
  const newer = card({ updated_at: '2026-11-20T09:00:01Z', state: 'passed' });
  const older = card({ updated_at: '2026-11-20T09:00:00Z', state: 'running', column: 'station' });

  expect(merge(new Map([['run-1', newer]]), [older]).get('run-1')).toBe(newer);
  expect(merge(new Map([['run-1', older]]), [newer]).get('run-1')).toBe(newer);
});

test('merging keeps every station card but only the newest cards of Done and Stopped', () => {
  const at = (minute: number) => `2026-11-20T09:${String(minute).padStart(2, '0')}:00Z`;
  const many = (column: 'done' | 'stopped' | 'station', count: number) =>
    Array.from({ length: count }, (_, minute) =>
      card({ run_id: `${column}-${minute}`, column, station: column === 'station' ? 'draft' : null, updated_at: at(minute) }),
    );

  const merged = merge(new Map(), [...many('station', 50), ...many('done', 50), ...many('stopped', 50)], 30);

  const kept = (column: string) => [...merged.values()].filter((held) => held.column === column);
  expect(kept('station')).toHaveLength(50);
  expect(kept('done')).toHaveLength(30);
  expect(kept('stopped')).toHaveLength(30);
  expect(merged.has('done-49')).toBe(true);
  expect(merged.has('done-19')).toBe(false);
  expect(merged.has('stopped-20')).toBe(true);
});

test('Start again on a stalled card asks first, warns the worker may be slow, then restarts it', async () => {
  const stalled = card({ column: 'stopped', state: 'stalled', actions: ['start_again'] });
  const api = fakeApi({
    board: async () => board({ cards: [stalled] }),
    restart: async () => ({ run_id: 'run-2', job_id: 'job-2' }),
  });
  renderWith(<BoardView name="Łukasz" />, api);

  await userEvent.click(await screen.findByRole('button', { name: 'Start again' }));
  expect(screen.getByText(/the worker may only be slow/i)).toBeTruthy();
  await userEvent.click(screen.getByRole('button', { name: 'Yes, start again' }));

  await waitFor(() => expect(api.restart).toHaveBeenCalledWith('run-1', 'Łukasz'));
  expect(window.location.hash).toBe('#/runs/run-2?job=job-2');
});

test('a card shows its badges: queued, rework, started again and earlier version', async () => {
  const cards = [
    card({ run_id: 'w', mission: 'Queued answer', column: 'station', station: 'design', state: 'waiting', queued: true, waiting: { kind: 'approval', station: 'design', since: '2026-11-20T09:00:00Z' }, rework: { count: 1, limit: 2 } }),
    card({ run_id: 'f', mission: 'A failed report', column: 'stopped', state: 'failed', restarted_as: 'new', actions: [] }),
    card({ run_id: 'e', mission: 'Old shape', column: 'stopped', state: 'earlier_version' }),
  ];
  renderWith(<BoardView name="Ada" />, fakeApi({ board: async () => board({ cards }) }));

  const queued = await screen.findByRole('article', { name: 'Queued answer' });
  expect(within(queued).getByText('Queued')).toBeTruthy();
  expect(within(queued).getByText('Rework 1 of 2')).toBeTruthy();
  const again = screen.getByRole('article', { name: 'A failed report' });
  expect(within(again).getByRole('link', { name: 'Started again' }).getAttribute('href')).toBe('#/runs/new');
  expect(within(again).queryByRole('button')).toBeNull();
  expect(within(screen.getByRole('article', { name: 'Old shape' })).getByText('Earlier version')).toBeTruthy();
});

test('the typed name is sent percent-encoded, so a name outside Latin-1 survives', () => {
  expect(nameHeader('Łukasz')).toEqual({ 'X-Factory-Name': '%C5%81ukasz' });
});
