import { useState } from 'react';
import type { RunList } from '../api';
import { useServices } from '../context';
import { useRepeat } from '../poll';
import { reason, when } from '../words';
import { StateBadge } from './StateBadge';

/** Every run, newest change first. It reloads every ten seconds. */
export function AllRuns() {
  const { api, every } = useServices();
  const [list, setList] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useRepeat(
    async () => {
      try {
        setList(await api.listRuns({ order: 'newest' }));
        setError(null);
      } catch (caught) {
        setError(reason(caught));
      }
      return true;
    },
    every.lists,
    api,
  );

  return (
    <section>
      <h1>All runs</h1>
      <p className="lead">Every run, newest change first.</p>
      {error && <p className="notice notice-error">The runs could not load: {error}</p>}
      {list && list.runs.length === 0 && <p className="quiet">No runs yet. Start a new mission.</p>}
      {list && list.runs.length > 0 && (
        <ul className="runs">
          {list.runs.map((run) => (
            <li key={run.run_id}>
              <a href={`#/runs/${run.run_id}`}>{run.mission || run.run_id}</a>
              <span className="meta">
                <StateBadge state={run.state} />{' '}
                {run.state === 'stalled'
                  ? `No change since ${when(run.last_change)}`
                  : `Last change ${when(run.last_change)}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {list?.more && <p className="quiet">There are more runs than are shown here.</p>}
    </section>
  );
}
