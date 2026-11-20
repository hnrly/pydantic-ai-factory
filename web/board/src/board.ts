// The board's cards, kept between polls, and the rules for showing them.
import type { Card } from './api';

/** How many cards Done and Stopped show before "See all". */
export const SHOWN = 20;

/** How many cards Done and Stopped each keep between polls, so a long-open board stays small. */
export const KEPT = 60;

/**
 * Take each polled card in place of the one held, when its `updated_at` is the same or
 * newer. A card can move from running to stalled with no new save, so a strictly newer
 * rule would miss that. An older copy, from a poll that overlapped a later one, is ignored.
 *
 * Every card in a station's column is kept. Done and Stopped keep only their newest
 * `keep` cards, since the board shows 20 of each and "See all" pages through the rest.
 */
export function merge(
  held: ReadonlyMap<string, Card>,
  polled: readonly Card[],
  keep = KEPT,
): Map<string, Card> {
  const next = new Map(held);
  for (const card of polled) {
    const mine = next.get(card.run_id);
    if (!mine || Date.parse(card.updated_at) >= Date.parse(mine.updated_at)) next.set(card.run_id, card);
  }
  for (const column of ['done', 'stopped'] as const) {
    const all = latest(next.values(), column, Infinity);
    for (const card of all.slice(keep)) next.delete(card.run_id);
  }
  return next;
}

/** The cards in one station's column, the longest unchanged first. */
export function inStation(cards: Iterable<Card>, station: string): Card[] {
  return [...cards]
    .filter((card) => card.column === 'station' && card.station === station)
    .sort((a, b) => Date.parse(a.updated_at) - Date.parse(b.updated_at));
}

/** The latest cards in Done or Stopped, newest change first, ties by run id. */
export function latest(cards: Iterable<Card>, column: 'done' | 'stopped', count = SHOWN): Card[] {
  return [...cards]
    .filter((card) => card.column === column)
    .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at) || (a.run_id < b.run_id ? 1 : -1))
    .slice(0, count);
}

/** The cards that wait for a person with no answer queued, the longest wait first. */
export function needsYou(cards: Iterable<Card>): Card[] {
  const since = (card: Card) => Date.parse(card.waiting?.since ?? card.updated_at);
  return [...cards].filter((card) => card.needs_you).sort((a, b) => since(a) - since(b));
}
