import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { App } from '../src/App';
import { detail, fakeApi, renderWith } from './fake';

const BANNER = 'This is a name, not a sign-in. Anyone can type any name.';

test.each(['', '#/runs', '#/new', '#/runs/run-1'])(
  'the name banner is shown on the page at "%s"',
  async (hash) => {
    window.location.hash = hash;
    const api = fakeApi({
      listRuns: async () => ({ runs: [], more: false }),
      getRun: async () => detail(),
    });
    renderWith(<App />, api);

    expect(await screen.findByText(BANNER)).toBeTruthy();
  },
);

test('the name typed in the header is kept in the browser', async () => {
  const api = fakeApi({ listRuns: async () => ({ runs: [], more: false }) });
  const { unmount } = renderWith(<App />, api);

  await userEvent.type(screen.getByLabelText('Your name'), 'Ada');
  unmount();
  renderWith(<App />, api);

  expect((screen.getByLabelText('Your name') as HTMLInputElement).value).toBe('Ada');
});
