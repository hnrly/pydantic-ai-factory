import type { Card } from '../api';
import { ago, lasting, WAITING_LABEL } from '../words';
import { StartAgain } from './StartAgain';
import { StateBadge } from './StateBadge';

/** One card: its mission, its badges, and the only action a card offers, "Start again". */
export function CardView({ card, name }: { card: Card; name: string }) {
  return (
    <article className="card" aria-label={card.mission || card.run_id}>
      <a className="card-title" href={`#/runs/${card.run_id}`}>
        {card.mission || card.run_id}
      </a>
      <div className="badges">
        <StateBadge state={card.state} />
        {card.queued && card.state !== 'queued' && <span className="badge badge-queued">Queued</span>}
        {card.rework && (
          <span className="badge badge-rework">
            Rework {card.rework.count} of {card.rework.limit}
          </span>
        )}
        {card.restarted_as && (
          <a className="badge badge-link" href={`#/runs/${card.restarted_as}`}>
            Started again
          </a>
        )}
      </div>
      <p className="meta">{describe(card)}</p>
      {card.reason && <p className="meta reason">{card.reason}</p>}
      {card.actions.includes('start_again') && !card.restarted_as && <StartAgain card={card} name={name} />}
    </article>
  );
}

function describe(card: Card): string {
  if (card.waiting && card.state === 'waiting') {
    return `${WAITING_LABEL[card.waiting.kind]}, for ${lasting(card.waiting.since)}`;
  }
  if (card.state === 'stalled') return `Stalled, last changed ${ago(card.updated_at)}`;
  if (card.state === 'earlier_version') return 'Made by an earlier version of this factory';
  return `Last changed ${ago(card.updated_at)}`;
}
