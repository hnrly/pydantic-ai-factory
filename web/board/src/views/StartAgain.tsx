import { useState } from 'react';
import type { Card } from '../api';
import { useServices } from '../context';
import { NEEDS_NAME, reason, STALLED_WARNING } from '../words';

/**
 * The "Start again" button, and the confirmation it opens. On a stalled card the
 * confirmation warns that the worker may only be slow. Starting again opens the new run.
 */
export function StartAgain({ card, name }: { card: Card; name: string }) {
  const { api } = useServices();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const named = name.trim() !== '';

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const queued = await api.restart(card.run_id, name.trim());
      window.location.hash = `#/runs/${queued.run_id}?job=${queued.job_id}`;
    } catch (caught) {
      setError(reason(caught));
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <div className="actions">
        <button type="button" disabled={!named} onClick={() => setAsking(true)}>
          Start again
        </button>
        {!named && <span className="quiet">{NEEDS_NAME}</span>}
      </div>
    );
  }
  return (
    <div className="confirm" role="group" aria-label="Start again?">
      <p>Start this mission again as a new run? This card stays in Stopped, linked to the new run.</p>
      {card.state === 'stalled' && <p className="warning">{STALLED_WARNING}</p>}
      {error && <p className="notice notice-error">{error}</p>}
      <div className="actions">
        <button type="button" className="primary" disabled={busy} onClick={confirm}>
          Yes, start again
        </button>
        <button type="button" disabled={busy} onClick={() => setAsking(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
