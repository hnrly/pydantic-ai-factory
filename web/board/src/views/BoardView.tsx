import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import type { Card, FactoryShape, Queued } from '../api';
import { enter, inMissions, inStation, latest, merge, needsYou, STATION, type Entries } from '../board';
import { useServices } from '../context';
import { lessMotion, useGlide } from '../motion';
import { useRepeat } from '../poll';
import { reason } from '../words';
import { AddMission } from './AddMission';
import { CardView } from './CardView';
import type { BoardSummary } from './Header';

/** How long a card flashes after "needs you" scrolls to it, in milliseconds. */
const FLASH = 1_800;

/** A mission added here, shown in Missions until the board's next poll returns its card. */
function added(queued: Queued, mission: string): Card {
  return {
    run_id: queued.run_id,
    mission: mission.split('\n')[0] ?? mission,
    column: 'missions',
    station: null,
    state: 'queued',
    queued: true,
    needs_you: false,
    waiting: null,
    rework: null,
    revision: null,
    updated_at: new Date().toISOString(),
    restarted_as: null,
    reason: null,
    actions: [],
  };
}

/**
 * The board: Missions, then one column per station, in the factory's order, then Done
 * and Stopped.
 *
 * It loads every card once, then asks every few seconds, while the tab is visible, for
 * the cards that changed since the last answer. A card glides only when it changes
 * column; inside a column, cards keep the order they arrived in. It tells the header how many cards need a person, and how to jump to the one that has
 * waited longest.
 */
export function BoardView({
  name,
  onSummary,
  under = false,
}: {
  name: string;
  onSummary?: (summary: BoardSummary | null) => void;
  /** An opened run covers the board, so the board is dimmed. */
  under?: boolean;
}) {
  const { api, every } = useServices();
  const [shape, setShape] = useState<FactoryShape | null>(null);
  const [{ cards, entries }, setHeld] = useState<{ cards: Map<string, Card>; entries: Entries }>({
    cards: new Map(),
    entries: new Map(),
  });
  const [pending, setPending] = useState<Card[]>([]);
  const [since, setSince] = useState<string | undefined>(undefined);
  const [more, setMore] = useState({ done: false, stopped: false });
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const held = useRef(cards);
  held.current = cards;
  useGlide(root);

  useRepeat(
    async () => {
      try {
        const board = await api.board(since);
        setShape(board.shape);
        setHeld((mine) => {
          const next = merge(mine.cards, board.cards);
          return { cards: next, entries: enter(mine.entries, next.values()) };
        });
        const arrived = new Set(board.cards.map((card) => card.run_id));
        setPending((mine) => mine.filter((card) => !arrived.has(card.run_id)));
        if (board.more) setMore(board.more);
        setSince(board.since);
        setError(null);
      } catch (caught) {
        setError(reason(caught));
      }
      return true;
    },
    every.board,
    api,
    { whileVisible: true },
  );

  const jump = useCallback(() => {
    const first = needsYou(held.current.values())[0];
    const element = [...(root.current?.querySelectorAll<HTMLElement>('[data-run]') ?? [])].find(
      (candidate) => candidate.dataset.run === first?.run_id,
    );
    if (!element) return;
    element.scrollIntoView?.({ behavior: lessMotion() ? 'auto' : 'smooth', block: 'center', inline: 'center' });
    element.querySelector<HTMLElement>('.card-title')?.focus({ preventScroll: true });
    element.classList.remove('flash');
    void element.offsetWidth; // restart the animation when asked twice in a row
    element.classList.add('flash');
    window.setTimeout(() => element.classList.remove('flash'), FLASH);
  }, []);

  const all = [...cards.values()];
  const needed = needsYou(all).length;
  useEffect(() => {
    if (shape) onSummary?.({ factory: shape.name, needed, jump });
  }, [onSummary, shape?.name, needed, jump]);
  useEffect(() => () => onSummary?.(null), [onSummary]);

  if (!shape) {
    return (
      <section className="board-loading">
        {error ? (
          <p className="notice notice-error">The board could not load: {error}</p>
        ) : (
          <p className="quiet">Loading the board…</p>
        )}
      </section>
    );
  }

  const missions = inMissions([...all, ...pending.filter((card) => !cards.has(card.run_id))], entries);
  return (
    <section className={under ? 'board board-under' : 'board'} aria-label={`The ${shape.name} board`}>
      {error && <p className="notice notice-error board-error">The board could not reload: {error}</p>}
      <div className="columns" ref={root}>
        <Column title="Missions" strip="var(--strip-missions)" cards={missions} name={name}>
          <AddMission name={name} onAdded={(queued, mission) => setPending((mine) => [...mine, added(queued, mission)])} />
        </Column>
        {shape.stations.map((station) => (
          <Column
            key={`station:${station.name}`}
            title={station.name}
            strip={STATION}
            cards={inStation(all, station.name, entries)}
            name={name}
          />
        ))}
        <Column
          title="Done"
          strip="var(--strip-done)"
          cards={latest(all, 'done', undefined, entries)}
          name={name}
          seeAll={more.done ? 'done' : undefined}
        />
        <Column
          title="Stopped"
          strip="var(--strip-stopped)"
          cards={latest(all, 'stopped', undefined, entries)}
          name={name}
          seeAll={more.stopped ? 'stopped' : undefined}
        />
      </div>
    </section>
  );
}

function Column({
  title,
  strip,
  cards,
  name,
  seeAll,
  children,
}: {
  title: string;
  strip: string;
  cards: Card[];
  name: string;
  seeAll?: 'done' | 'stopped';
  children?: ReactNode;
}) {
  return (
    <section className="column" aria-label={title} style={{ '--strip': strip } as CSSProperties}>
      <h2 className="column-title">
        <span className="column-mark" aria-hidden="true" />
        <span className="column-name">{title}</span> <span className="count">{cards.length}</span>
      </h2>
      {children}
      <div className="column-cards">
        {cards.map((card) => (
          <CardView key={card.run_id} card={card} name={name} />
        ))}
        {cards.length === 0 && !children && <p className="column-empty">No cards here yet.</p>}
      </div>
      {seeAll && (
        <a className="see-all" href={`#/list?column=${seeAll}`}>
          See all
        </a>
      )}
    </section>
  );
}
