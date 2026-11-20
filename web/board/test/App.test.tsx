import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { App } from '../src/App';
import { board, detail, fakeApi, renderWith } from './fake';

const BANNER = 'This is a name, not a sign-in. Anyone can type any name.';

function anyPage() {
  return fakeApi({
    board: async () => board(),
    listCards: async () => ({ cards: [], before: null }),
    getRun: async () => detail(),
  });
}

test.each(['', '#/list', '#/list?column=stopped', '#/runs/run-1'])(
  'the name banner and the New mission form are shown on the page at "%s"',
  async (hash) => {
    window.location.hash = hash;
    renderWith(<App />, anyPage());

    expect(await screen.findByText(BANNER)).toBeTruthy();
    expect(screen.getByLabelText('New mission')).toBeTruthy();
  },
);

test('the name typed in the header is kept in the browser', async () => {
  const api = anyPage();
  const { unmount } = renderWith(<App />, api);

  await userEvent.type(screen.getByLabelText('Your name'), 'Ada');
  unmount();
  renderWith(<App />, api);

  expect((screen.getByLabelText('Your name') as HTMLInputElement).value).toBe('Ada');
});

test('the list view replaces All runs behind a toggle in the header', async () => {
  window.location.hash = '#/list';
  const api = anyPage();
  renderWith(<App />, api);

  expect(await screen.findByRole('heading', { name: 'Every card' })).toBeTruthy();
  expect(screen.getByRole('link', { name: 'List' }).getAttribute('aria-current')).toBe('page');
  expect(api.listCards).toHaveBeenCalledWith('all', undefined);
});
