// The board's cards, kept between polls, and the rules for showing them.
import type { Card } from './api';

/** The colour of every station's column: one grey, defined in app.css. */
export const STATION = 'var(--strip-station)';

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

/** Which column a card is in, as one key: "missions", "station:design", "done" or "stopped". */
export function columnOf(card: Card): string {
  return card.column === 'station' ? `station:${card.station ?? ''}` : card.column;
}

/** When each card entered the column it is in, in milliseconds, by run id. */
export type Entries = ReadonlyMap<string, { column: string; at: number }>;

/**
 * Note when each card entered its column: the `updated_at` of the save that put it
 * there. A card that stays in its column keeps the time it had, however often it
 * changes, so the order inside a column never follows the last change.
 */
export function enter(entries: Entries, cards: Iterable<Card>): Map<string, { column: string; at: number }> {
  const next = new Map<string, { column: string; at: number }>();
  for (const card of cards) {
    const column = columnOf(card);
    const held = entries.get(card.run_id);
    next.set(card.run_id, held && held.column === column ? held : { column, at: Date.parse(card.updated_at) });
  }
  return next;
}

const NONE: Entries = new Map();

function entered(entries: Entries, card: Card): number {
  return entries.get(card.run_id)?.at ?? Date.parse(card.updated_at);
}

/** Oldest entry first, ties by run id, so two cards never swap. */
function firstIn(entries: Entries) {
  return (a: Card, b: Card) => entered(entries, a) - entered(entries, b) || (a.run_id < b.run_id ? -1 : 1);
}

/** The missions whose run is not saved yet, the first added first: the order a worker takes them. */
export function inMissions(cards: Iterable<Card>, entries: Entries = NONE): Card[] {
  return [...cards].filter((card) => card.column === 'missions').sort(firstIn(entries));
}

/** The cards in one station's column, in the order they reached it. */
export function inStation(cards: Iterable<Card>, station: string, entries: Entries = NONE): Card[] {
  return [...cards]
    .filter((card) => card.column === 'station' && card.station === station)
    .sort(firstIn(entries));
}

/** The latest cards in Done or Stopped, the last to arrive first, ties by run id. */
export function latest(
  cards: Iterable<Card>,
  column: 'done' | 'stopped',
  count = SHOWN,
  entries: Entries = NONE,
): Card[] {
  const first = firstIn(entries);
  return [...cards]
    .filter((card) => card.column === column)
    .sort((a, b) => first(b, a))
    .slice(0, count);
}

/** The cards that wait for a person with no answer queued, the longest wait first. */
export function needsYou(cards: Iterable<Card>): Card[] {
  const since = (card: Card) => Date.parse(card.waiting?.since ?? card.updated_at);
  return [...cards].filter((card) => card.needs_you).sort((a, b) => since(a) - since(b));
}
