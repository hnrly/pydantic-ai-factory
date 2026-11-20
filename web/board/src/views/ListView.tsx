import { useEffect, useState } from 'react';
import type { Card } from '../api';
import { useServices } from '../context';
import { reason } from '../words';
import { CardView } from './CardView';

/** The columns "See all" opens. */
export type Shown = 'done' | 'stopped';

const TITLES: Record<Shown, string> = { done: 'Done', stopped: 'Stopped' };

/** Every card in Done or Stopped, as "See all" shows them: newest change first, 50 at a time. */
export function ListView({ column, name }: { column: Shown; name: string }) {
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
      <a className="back" href="#/">
        Back to the board
      </a>
      <h1>{TITLES[column]}</h1>
      <nav className="list-columns" aria-label="Which cards">
        {(['done', 'stopped'] as const).map((which) => (
          <a key={which} href={`#/list?column=${which}`} aria-current={which === column ? 'page' : undefined}>
            {TITLES[which]}
          </a>
        ))}
      </nav>
      {error && <p className="notice notice-error">The cards could not load: {error}</p>}
      {loaded && cards.length === 0 && !error && <p className="quiet">No cards here yet.</p>}
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
