import { useState } from 'react';
import type { RunList } from '../api';
import { useServices } from '../context';
import { useRepeat } from '../poll';
import { reason, WAITING_LABEL, when } from '../words';

/** Waiting runs, longest wait first. It reloads every ten seconds. */
export function Inbox() {
  const { api, every } = useServices();
  const [list, setList] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useRepeat(
    async () => {
      try {
        setList(await api.listRuns({ outcome: 'waiting', order: 'oldest' }));
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
      <h1>Inbox</h1>
      <p className="lead">Runs waiting for a person, longest wait first.</p>
      {error && <p className="notice notice-error">The inbox could not load: {error}</p>}
      {list && list.runs.length === 0 && <p className="quiet">Nothing is waiting for a person.</p>}
      {list && list.runs.length > 0 && (
        <ul className="runs">
          {list.runs.map((run) => (
            <li key={run.run_id}>
              <a href={`#/runs/${run.run_id}`}>{run.mission || run.run_id}</a>
              <span className="meta">
                {run.waiting ? `${WAITING_LABEL[run.waiting.kind]} at ${run.waiting.station}` : 'Waiting'}
                {' · '}waiting since {when(run.last_change)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {list?.more && <p className="quiet">More runs are waiting than are shown here.</p>}
    </section>
  );
}
