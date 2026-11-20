import { screen, waitFor } from '@testing-library/react';
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

test('the first screen asks for a name, marked as not a sign-in, before the board shows', async () => {
  const api = anyPage();
  renderWith(<App />, api);

  expect(screen.getByRole('heading', { name: 'What’s your name?' })).toBeTruthy();
  expect(screen.getByText(BANNER)).toBeTruthy();
  expect(screen.queryByRole('region', { name: 'Missions' })).toBeNull();
  expect(api.board).not.toHaveBeenCalled();
  expect((screen.getByRole('button', { name: 'Open the board' }) as HTMLButtonElement).disabled).toBe(true);

  await userEvent.type(screen.getByLabelText('Your name'), 'Ada');
  await userEvent.click(screen.getByRole('button', { name: 'Open the board' }));

  expect(await screen.findByRole('region', { name: 'Missions' })).toBeTruthy();
  expect(screen.getByText('Ada')).toBeTruthy();
  expect(localStorage.getItem('factory.name')).toBe('Ada');
});

test('a name kept in the browser opens the board at once', async () => {
  localStorage.setItem('factory.name', 'Ada');
  renderWith(<App />, anyPage());

  expect(await screen.findByRole('region', { name: 'Missions' })).toBeTruthy();
  expect(screen.queryByRole('heading', { name: 'What’s your name?' })).toBeNull();
});

test('a link to a run asks for the name first, then opens the run', async () => {
  window.location.hash = '#/runs/run-1';
  renderWith(<App />, anyPage());

  await userEvent.type(screen.getByLabelText('Your name'), 'Ada');
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }));

  expect(await screen.findByRole('heading', { name: 'A to-do list web page.' })).toBeTruthy();
});

test('the header shows the name and changing it asks again, keeping the new one', async () => {
  localStorage.setItem('factory.name', 'Ada');
  renderWith(<App />, anyPage());

  await userEvent.click(await screen.findByRole('button', { name: 'Change name' }));
  const field = screen.getByLabelText('Your name') as HTMLInputElement;
  expect(field.value).toBe('Ada');
  await userEvent.clear(field);
  await userEvent.type(field, 'Ben');
  await userEvent.click(screen.getByRole('button', { name: 'Open the board' }));

  await waitFor(() => expect(screen.getByText('Ben')).toBeTruthy());
  expect(localStorage.getItem('factory.name')).toBe('Ben');
});

test('there is no theme switch and no list view toggle', async () => {
  localStorage.setItem('factory.name', 'Ada');
  renderWith(<App />, anyPage());

  await screen.findByRole('region', { name: 'Missions' });
  expect(screen.queryByRole('button', { name: /theme/i })).toBeNull();
  expect(screen.queryByRole('link', { name: 'List' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Columns' })).toBeNull();
  expect(screen.queryByLabelText('New mission')).toBeNull();
});

test('"See all" opens Done or Stopped as a list, with a way back to the board', async () => {
  localStorage.setItem('factory.name', 'Ada');
  window.location.hash = '#/list?column=stopped';
  const api = anyPage();
  renderWith(<App />, api);

  expect(await screen.findByRole('heading', { name: 'Stopped' })).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Back to the board' }).getAttribute('href')).toBe('#/');
  expect(api.listCards).toHaveBeenCalledWith('stopped', undefined);
});
