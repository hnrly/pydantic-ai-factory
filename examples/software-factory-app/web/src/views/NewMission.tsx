import { useState, type FormEvent } from 'react';
import { useServices } from '../context';
import { NEEDS_NAME, reason } from '../words';

/** One text box and a start button. Starting opens the new run's page. */
export function NewMission({ name }: { name: string }) {
  const { api } = useServices();
  const [mission, setMission] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = name.trim() !== '' && mission.trim() !== '' && !busy;

  async function start(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      const queued = await api.startRun(mission.trim(), name.trim());
      window.location.hash = `#/runs/${queued.run_id}?job=${queued.job_id}`;
    } catch (caught) {
      setError(reason(caught));
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>New mission</h1>
      <form onSubmit={start} className="stack">
        <label htmlFor="mission">Mission: what should the factory make?</label>
        <textarea
          id="mission"
          rows={5}
          value={mission}
          onChange={(event) => setMission(event.target.value)}
          placeholder="A to-do list web page where I can add tasks and tick them off."
        />
        {!name.trim() && <p className="quiet">{NEEDS_NAME}</p>}
        {error && <p className="notice notice-error">The run could not start: {error}</p>}
        <div>
          <button type="submit" className="primary" disabled={!ready}>
            Start
          </button>
        </div>
      </form>
    </section>
  );
}
