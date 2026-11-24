import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { NewMission } from '../src/views/NewMission';
import { fakeApi, renderWith } from './fake';

test('a new mission is started under the name in the header, and opens its run', async () => {
  const api = fakeApi({ startRun: async () => ({ run_id: 'run-9', job_id: 'job-9' }) });
  renderWith(<NewMission name="Ada" />, api);

  await userEvent.type(screen.getByLabelText(/mission/i), 'A to-do list web page.');
  await userEvent.click(screen.getByRole('button', { name: 'Start' }));

  expect(api.startRun).toHaveBeenCalledWith('A to-do list web page.', 'Ada');
  expect(window.location.hash).toBe('#/runs/run-9?job=job-9');
});

test('a new mission cannot start without a name', async () => {
  const api = fakeApi();
  renderWith(<NewMission name="" />, api);

  await userEvent.type(screen.getByLabelText(/mission/i), 'Anything.');
  expect((screen.getByRole('button', { name: 'Start' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/type your name at the top first/i)).toBeTruthy();
});
