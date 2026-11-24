import { screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { Board, Card } from '../src/api';
import { BoardView } from '../src/views/BoardView';
import { board, card, fakeApi, renderWith } from './fake';

// jsdom lays nothing out and cannot animate, so give each card a place from where it
// sits in the page: its column sets x, its place in the column sets y. Record which
// cards are animated.
const animated: string[] = [];
const rect = Element.prototype.getBoundingClientRect;

beforeEach(() => {
  animated.length = 0;
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const column = this.closest('.column');
    const columns = [...document.querySelectorAll('.column')];
    const x = column ? columns.indexOf(column) * 300 : 0;
    const y = this.parentElement ? [...this.parentElement.children].indexOf(this) * 100 : 0;
    return { x, y, left: x, top: y, right: x + 280, bottom: y + 90, width: 280, height: 90, toJSON: () => ({}) } as DOMRect;
  };
  HTMLElement.prototype.animate = vi.fn(function (this: HTMLElement) {
    animated.push(this.dataset.run ?? '');
    return {} as Animation;
  });
});

afterEach(() => {
  Element.prototype.getBoundingClientRect = rect;
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
});

const at = (minute: number) => `2026-11-20T09:${String(minute).padStart(2, '0')}:00Z`;

function inDesign(run_id: string, mission: string, minute: number, fields: Partial<Card> = {}): Card {
  return card({ run_id, mission, column: 'station', station: 'design', state: 'running', updated_at: at(minute), ...fields });
}

/** A board that answers each poll with the next of `polls`, then with nothing new. */
function polling(polls: Board[]) {
  return fakeApi({ board: async () => polls.shift() ?? board({ cards: [], since: 'later', more: null }) });
}

const order = (region: string) =>
  within(screen.getByRole('region', { name: region }))
    .queryAllByRole('article')
    .map((article) => article.getAttribute('aria-label'));

test('a card that stays in its column is not animated when a poll changes its badge or a neighbour', async () => {
  const above = inDesign('above', 'Above', 1);
  const stays = inDesign('stays', 'Stays', 2);
  const api = polling([
    board({ cards: [above, stays], since: 'first' }),
    board({
      cards: [
        { ...above, column: 'done', station: null, state: 'passed', updated_at: at(5) },
        { ...stays, queued: true, rework: { count: 1, limit: 2 } },
      ],
      since: 'second',
      more: null,
    }),
  ]);
  renderWith(<BoardView name="Ada" />, api);

  await within(await screen.findByRole('region', { name: 'Done' })).findByText('Above');
  await waitFor(() => expect(api.board.mock.calls.length).toBeGreaterThanOrEqual(3));

  expect(order('design')).toEqual(['Stays']);
  expect(screen.getByText('Rework 1 of 2')).toBeTruthy();
  expect(animated).not.toContain('stays');
});

test('a card that changes column is animated', async () => {
  const moving = inDesign('moving', 'Moving', 1);
  const api = polling([
    board({ cards: [moving], since: 'first' }),
    board({ cards: [{ ...moving, column: 'done', station: null, state: 'passed', updated_at: at(5) }], since: 'second', more: null }),
  ]);
  renderWith(<BoardView name="Ada" />, api);

  await within(await screen.findByRole('region', { name: 'Done' })).findByText('Moving');

  expect(animated).toEqual(['moving']);
});

test("the order in a column does not change when a card's updated_at changes", async () => {
  const first = inDesign('first', 'First in', 1);
  const second = inDesign('second', 'Second in', 2);
  const api = polling([
    board({ cards: [first, second], since: 'first' }),
    board({ cards: [{ ...first, updated_at: at(9), queued: true }], since: 'second', more: null }),
  ]);
  renderWith(<BoardView name="Ada" />, api);

  await screen.findByText('First in');
  expect(order('design')).toEqual(['First in', 'Second in']);
  await within(screen.getByRole('region', { name: 'design' })).findByText('Queued');

  expect(order('design')).toEqual(['First in', 'Second in']);
  expect(animated).toEqual([]);
});

test('a card the first load shows does not fade in, and one a later poll brings does', async () => {
  const api = polling([
    board({ cards: [inDesign('old', 'Already here', 1)], since: 'first' }),
    board({ cards: [inDesign('new', 'Just arrived', 3)], since: 'second', more: null }),
  ]);
  renderWith(<BoardView name="Ada" />, api);

  await screen.findByText('Just arrived');

  expect(animated).toEqual(['new']);
});

test('a card that leaves the board and comes back does not fade in again', async () => {
  const slow = card({ run_id: 'slow', mission: 'Slow worker', column: 'stopped', state: 'stalled', updated_at: at(1) });
  const newer = Array.from({ length: 20 }, (_, index) =>
    card({ run_id: `n${index}`, mission: `Newer ${index}`, column: 'stopped', state: 'failed', updated_at: at(10 + index) }),
  );
  const api = polling([
    board({ cards: [slow], since: 'first' }),
    board({ cards: newer, since: 'second', more: null }),
  ]);
  renderWith(<BoardView name="Ada" />, api);

  await screen.findByText('Newer 19');
  expect(order('Stopped')).not.toContain('Slow worker');
  api.board.mockImplementationOnce(async () =>
    board({ cards: [inDesign('slow', 'Slow worker', 40)], since: 'third', more: null }),
  );
  await within(screen.getByRole('region', { name: 'design' })).findByText('Slow worker');

  expect(animated).not.toContain('slow');
});

test('when the first load is empty, the first card a poll brings fades in', async () => {
  const api = polling([
    board({ cards: [], since: 'first' }),
    board({ cards: [inDesign('first', 'First of all', 1)], since: 'second', more: null }),
  ]);
  renderWith(<BoardView name="Ada" />, api);

  await screen.findByText('First of all');

  expect(animated).toEqual(['first']);
});
