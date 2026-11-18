import { screen, within } from '@testing-library/react';
import { expect, test } from 'vitest';
import { AllRuns } from '../src/views/AllRuns';
import { fakeApi, renderWith, summary } from './fake';

test('all runs lists every run newest change first, each with its state', async () => {
  const api = fakeApi({
    listRuns: async () => ({
      runs: [
        summary({ run_id: 'n', mission: 'Newest', state: 'running', outcome: 'running' }),
        summary({ run_id: 'o', mission: 'Oldest', state: 'passed' }),
      ],
      more: false,
    }),
  });
  renderWith(<AllRuns />, api);

  const items = await screen.findAllByRole('listitem');
  expect(within(items[0]!).getByRole('link').textContent).toBe('Newest');
  expect(within(items[0]!).getByText('Running')).toBeTruthy();
  expect(within(items[1]!).getByText('Passed')).toBeTruthy();
  expect(api.listRuns).toHaveBeenCalledWith(expect.objectContaining({ order: 'newest' }));
});

test('a stalled run in all runs has a badge and its last change time', async () => {
  const api = fakeApi({
    listRuns: async () => ({
      runs: [
        summary({
          state: 'stalled',
          outcome: 'running',
          last_change: '2026-11-18T09:30:00Z',
          actions: ['start_again'],
        }),
      ],
      more: false,
    }),
  });
  renderWith(<AllRuns />, api);

  const item = (await screen.findAllByRole('listitem'))[0]!;
  expect(within(item).getByText('Stalled')).toBeTruthy();
  expect(within(item).getByText(/no change since/i)).toBeTruthy();
});

test('all runs says when more runs matched than are shown', async () => {
  const api = fakeApi({ listRuns: async () => ({ runs: [summary()], more: true }) });
  renderWith(<AllRuns />, api);

  expect(await screen.findByText(/more runs than are shown here/i)).toBeTruthy();
});
