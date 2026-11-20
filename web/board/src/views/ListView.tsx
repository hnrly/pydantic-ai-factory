import { useEffect, useState } from 'react';
import type { Card, ListColumn } from '../api';
import { useServices } from '../context';
import { reason } from '../words';
import { CardView } from './CardView';

const TITLES: Record<ListColumn, string> = { all: 'Every card', done: 'Done', stopped: 'Stopped' };

/** The cards of one column, or of all of them, as a list, newest change first, 50 at a time. */
export function ListView({ column, name }: { column: ListColumn; name: string }) {
  const { api } = useServices();
  const [cards, setCards] = useState<Card[]>([]);
  const [before, setBefore] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(from?: string) {
    try {
      const page = await api.listCards(column, from);
      setCards((shown) => (from ? [...shown, ...page.cards] : page.cards));
      setBefore(page.before);
      setError(null);
    } catch (caught) {
      setError(reason(caught));
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    setCards([]);
    setBefore(null);
    setLoaded(false);
    void load();
  }, [api, column]);

  return (
    <section>
      <h1>{TITLES[column]}</h1>
      <nav className="list-columns" aria-label="Which cards">
        {(['all', 'done', 'stopped'] as const).map((which) => (
          <a key={which} href={`#/list?column=${which}`} aria-current={which === column ? 'page' : undefined}>
            {TITLES[which]}
          </a>
        ))}
      </nav>
      {error && <p className="notice notice-error">The cards could not load: {error}</p>}
      {loaded && cards.length === 0 && !error && <p className="quiet">No cards yet. Start a new mission.</p>}
      <div className="list">
        {cards.map((card) => (
          <CardView key={card.run_id} card={card} name={name} />
        ))}
      </div>
      {before && (
        <button type="button" onClick={() => void load(before)}>
          Show older cards
        </button>
      )}
    </section>
  );
}
