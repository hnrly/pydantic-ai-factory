import type { Card } from '../api';
import { columnOf } from '../board';
import { ago, lasting, pillOf, WAITING_LABEL } from '../words';
import { StartAgain } from './StartAgain';
import { StateBadge } from './StateBadge';
import { StateIcon } from './StatePill';

/** A run id short enough to read on a card: the first eight characters. */
export const shortId = (runId: string) => runId.slice(0, 8);

/**
 * One card: its state's shape beside the mission, its badges as chips, then when it
 * last changed and the run's short id. The only action a card offers is "Start again".
 */
export function CardView({ card, name }: { card: Card; name: string }) {
  const classes = ['card', `card-${card.state}`, card.needs_you ? 'card-needs-you' : ''].filter(Boolean).join(' ');
  return (
    <article className={classes} aria-label={card.mission || card.run_id} data-run={card.run_id} data-column={columnOf(card)}>
      <div className="card-head">
        <StateIcon pill={pillOf(card.state, card.waiting?.kind ?? null)} />
        <a className="card-title" href={`#/runs/${card.run_id}`}>
          {card.mission || card.run_id}
        </a>
      </div>
      <div className="badges">
        <StateBadge state={card.state} />
        {card.queued && card.state !== 'queued' && card.state !== 'starting' && (
          <span className="badge badge-queued">Queued</span>
        )}
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
      <div className="card-foot">
        <p className="meta">{describe(card)}</p>
        <span className="card-id" title={card.run_id}>
          {shortId(card.run_id)}
        </span>
      </div>
      {card.reason && <p className="meta reason">{card.reason}</p>}
      {card.actions.includes('start_again') && !card.restarted_as && <StartAgain card={card} name={name} />}
    </article>
  );
}

function describe(card: Card): string {
  if (card.waiting && card.state === 'waiting') {
    return `${WAITING_LABEL[card.waiting.kind]}, for ${lasting(card.waiting.since)}`;
  }
  if (card.state === 'queued' && card.column === 'missions') return `Waiting for a worker, added ${ago(card.updated_at)}`;
  if (card.state === 'starting') return 'A worker is starting it';
  if (card.state === 'stalled') return `Stalled, last changed ${ago(card.updated_at)}`;
  if (card.state === 'earlier_version') return 'Made by an earlier version of this factory';
  return `Last changed ${ago(card.updated_at)}`;
}
