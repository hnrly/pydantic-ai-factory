import { useState } from 'react';
import type { Card, FactoryShape } from '../api';
import { inStation, latest, merge, needsYou } from '../board';
import { useServices } from '../context';
import { useRepeat } from '../poll';
import { reason } from '../words';
import { CardView } from './CardView';

/**
 * The board: one column per station, in the factory's order, then Done and Stopped.
 *
 * It loads every card once, then asks every few seconds, while the tab is visible, for
 * the cards that changed since the last answer. "Needs you" shows only the cards that
 * wait for a person, the longest wait first.
 */
export function BoardView({ name }: { name: string }) {
  const { api, every } = useServices();
  const [shape, setShape] = useState<FactoryShape | null>(null);
  const [cards, setCards] = useState<Map<string, Card>>(new Map());
  const [since, setSince] = useState<string | undefined>(undefined);
  const [more, setMore] = useState({ done: false, stopped: false });
  const [filter, setFilter] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useRepeat(
    async () => {
      try {
        const board = await api.board(since);
        setShape(board.shape);
        setCards((held) => merge(held, board.cards));
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

  if (!shape) {
    return (
      <section>
        {error ? (
          <p className="notice notice-error">The board could not load: {error}</p>
        ) : (
          <p className="quiet">Loading…</p>
        )}
      </section>
    );
  }

  const all = [...cards.values()];
  const waiting = needsYou(all);
  return (
    <section>
      <div className="board-head">
        <h1>{shape.name}</h1>
        <label className="toggle">
          <input type="checkbox" checked={filter} onChange={(event) => setFilter(event.target.checked)} /> Needs
          you ({waiting.length})
        </label>
      </div>
      {error && <p className="notice notice-error">The board could not reload: {error}</p>}
      {filter ? (
        <section aria-label="Needs you" className="needs-you">
          {waiting.length === 0 && <p className="quiet">Nothing is waiting for a person.</p>}
          {waiting.map((card) => (
            <CardView key={card.run_id} card={card} name={name} />
          ))}
        </section>
      ) : (
        <div className="columns">
          {shape.stations.map((station) => (
            <Column key={`station:${station.name}`} title={station.name} cards={inStation(all, station.name)} name={name} />
          ))}
          <Column title="Done" cards={latest(all, 'done')} name={name} seeAll={more.done ? 'done' : undefined} />
          <Column
            title="Stopped"
            cards={latest(all, 'stopped')}
            name={name}
            seeAll={more.stopped ? 'stopped' : undefined}
          />
        </div>
      )}
    </section>
  );
}

function Column({
  title,
  cards,
  name,
  seeAll,
}: {
  title: string;
  cards: Card[];
  name: string;
  seeAll?: 'done' | 'stopped';
}) {
  return (
    <section className="column" aria-label={title}>
      <h2 className="column-title">
        {title} <span className="count">{cards.length}</span>
      </h2>
      {cards.map((card) => (
        <CardView key={card.run_id} card={card} name={name} />
      ))}
      {seeAll && (
        <a className="see-all" href={`#/list?column=${seeAll}`}>
          See all
        </a>
      )}
    </section>
  );
}
