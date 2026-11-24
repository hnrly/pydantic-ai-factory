import { screen, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { Inbox } from '../src/views/Inbox';
import { fakeApi, renderWith, summary, waiting } from './fake';

const wait = waiting();

test('the inbox asks for waiting runs, longest wait first, and shows them in that order', async () => {
  const api = fakeApi({
    listRuns: async () => ({
      runs: [
        summary({ run_id: 'b', mission: 'Second mission', state: 'waiting', waiting: wait }),
        summary({ run_id: 'a', mission: 'First mission', state: 'waiting', waiting: wait }),
      ],
      more: false,
    }),
  });
  renderWith(<Inbox />, api);

  const items = await screen.findAllByRole('listitem');
  expect(items.map((item) => within(item).getByRole('link').textContent)).toEqual([
    'Second mission',
    'First mission',
  ]);
  expect(api.listRuns).toHaveBeenCalledWith(expect.objectContaining({ outcome: 'waiting', order: 'oldest' }));
  expect(screen.queryByText(/more runs are waiting/i)).toBeNull();
});

test('the inbox says more runs are waiting when the API says more matched', async () => {
  const api = fakeApi({
    listRuns: async () => ({ runs: [summary({ state: 'waiting', waiting: wait })], more: true }),
  });
  renderWith(<Inbox />, api);

  expect(await screen.findByText(/more runs are waiting than are shown here/i)).toBeTruthy();
});

test('the inbox says so when nothing is waiting', async () => {
  const api = fakeApi({ listRuns: async () => ({ runs: [], more: false }) });
  renderWith(<Inbox />, api);

  expect(await screen.findByText(/nothing is waiting for a person/i)).toBeTruthy();
});

test('the inbox reloads by itself', async () => {
  const api = fakeApi({ listRuns: async () => ({ runs: [], more: false }) });
  renderWith(<Inbox />, api);

  await vi.waitFor(() => expect(api.listRuns.mock.calls.length).toBeGreaterThan(2));
});
