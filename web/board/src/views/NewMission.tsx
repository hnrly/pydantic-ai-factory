import { useState, type FormEvent } from 'react';
import { useServices } from '../context';
import { NEEDS_NAME, reason } from '../words';

/** The header's form: one text box and a start button. Starting opens the new run's page. */
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
      setMission('');
      window.location.hash = `#/runs/${queued.run_id}?job=${queued.job_id}`;
    } catch (caught) {
      setError(reason(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={start} className="new-mission">
      <label htmlFor="mission">New mission</label>
      <textarea
        id="mission"
        rows={2}
        value={mission}
        onChange={(event) => setMission(event.target.value)}
        placeholder="What should the factory make?"
      />
      <button type="submit" className="primary" disabled={!ready}>
        Start
      </button>
      {!name.trim() && mission.trim() !== '' && <p className="quiet">{NEEDS_NAME}</p>}
      {error && <p className="notice notice-error">The run could not start: {error}</p>}
    </form>
  );
}
