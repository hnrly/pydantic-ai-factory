import type { HistoryEvent, RunDetail } from '../api';
import { clock } from '../words';

function meta(event: HistoryEvent): string {
  // An attempt's own title already names its station and attempt.
  const where =
    event.kind === 'attempt' || !event.station
      ? null
      : event.attempt
        ? `${event.station}, attempt ${event.attempt}`
        : event.station;
  return [event.at ? clock(event.at) : null, where].filter(Boolean).join(' · ');
}

/**
 * What happened to the run, newest first, as a timeline, and a folded technical detail
 * with the run id, its revision and the factory's version.
 */
export function History({ run }: { run: RunDetail }) {
  return (
    <aside className="history" aria-label="History">
      <h2 className="panel-label">History</h2>
      <ol className="timeline">
        {run.history.map((event, index) => {
          const said = meta(event);
          return (
            <li key={index} className={`event event-${event.kind}`}>
              <span className="event-dot" aria-hidden="true" />
              <span className="event-body">
                <span className="event-title">{event.title}</span>
                {event.detail && <span className="event-detail">{event.detail}</span>}
                {event.run_id && <a href={`#/runs/${encodeURIComponent(event.run_id)}`}>Open the new run</a>}
                {said && <span className="event-meta">{said}</span>}
              </span>
            </li>
          );
        })}
      </ol>
      <details className="technical">
        <summary>Technical detail</summary>
        <p>
          run <code>{run.run_id}</code>
          {run.revision !== null && <> · revision {run.revision}</>}
          {run.factory_version && <> · factory version {run.factory_version}</>}
        </p>
      </details>
    </aside>
  );
}
