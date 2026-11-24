import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { BoardView } from '../src/views/BoardView';
import { board, card, fakeApi, renderWith } from './fake';

test('a mission added in Missions is posted under the name and shows as a card there', async () => {
  const api = fakeApi({
    board: async () => board(),
    startRun: async () => ({ run_id: 'run-9', job_id: 'job-9' }),
  });
  renderWith(<BoardView name="Ada" />, api);

  const missions = await screen.findByRole('region', { name: 'Missions' });
  await userEvent.type(within(missions).getByLabelText('Add a mission'), 'A to-do list web page.');
  await userEvent.click(within(missions).getByRole('button', { name: 'Add' }));

  expect(api.startRun).toHaveBeenCalledWith('A to-do list web page.', 'Ada');
  const added = await within(missions).findByRole('article', { name: 'A to-do list web page.' });
  expect(within(added).getByText('Queued')).toBeTruthy();
  expect((within(missions).getByLabelText('Add a mission') as HTMLTextAreaElement).value).toBe('');
  expect(window.location.hash).toBe('');
});

test('Enter adds a mission and Shift+Enter starts a new line', async () => {
  const api = fakeApi({
    board: async () => board(),
    startRun: async () => ({ run_id: 'run-9', job_id: 'job-9' }),
  });
  renderWith(<BoardView name="Ada" />, api);

  const box = await screen.findByLabelText('Add a mission');
  await userEvent.type(box, 'A weekly report.{Shift>}{Enter}{/Shift}With charts.');
  expect(api.startRun).not.toHaveBeenCalled();
  await userEvent.type(box, '{Enter}');

  await waitFor(() => expect(api.startRun).toHaveBeenCalledWith('A weekly report.\nWith charts.', 'Ada'));
  expect(await screen.findByRole('article', { name: 'A weekly report.' })).toBeTruthy();
});

test('a mission picked up by the worker leaves Missions for its first station', async () => {
  const queued = card({ run_id: 'm', mission: 'A weekly report.', column: 'missions', state: 'queued', queued: true, updated_at: '2026-11-20T09:00:00Z' });
  const moved = { ...queued, column: 'station' as const, station: 'design', state: 'running' as const, queued: false, updated_at: '2026-11-20T09:00:05Z' };
  let pickedUp = false;
  const api = fakeApi({
    board: async () => (pickedUp ? board({ cards: [moved], since: 'second', more: null }) : board({ cards: [queued], since: 'first' })),
  });
  renderWith(<BoardView name="Ada" />, api);

  await within(await screen.findByRole('region', { name: 'Missions' })).findByText('A weekly report.');
  pickedUp = true;
  await within(screen.getByRole('region', { name: 'design' })).findByText('A weekly report.');
  expect(within(screen.getByRole('region', { name: 'Missions' })).queryByText('A weekly report.')).toBeNull();
});

test('a mission the API refuses says why and keeps the text', async () => {
  const api = fakeApi({
    board: async () => board(),
    startRun: async () => {
      throw new Error('Type your name first');
    },
  });
  renderWith(<BoardView name="Ada" />, api);

  const box = await screen.findByLabelText('Add a mission');
  await userEvent.type(box, 'Anything.{Enter}');

  expect(await screen.findByText(/the mission was not added: type your name first/i)).toBeTruthy();
  expect((box as HTMLTextAreaElement).value).toBe('Anything.');
});
