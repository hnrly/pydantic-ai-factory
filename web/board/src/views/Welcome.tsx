import { useState, type FormEvent } from 'react';
import { BANNER } from '../words';

/**
 * The first screen: it asks for the person's name before anything else shows. The name
 * goes on every mission they add and every answer they give. It is not a sign-in.
 */
export function Welcome({
  initial,
  action,
  onName,
  onCancel,
}: {
  initial: string;
  action: string;
  onName: (name: string) => void;
  onCancel?: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ready = value.trim() !== '';

  function done(event: FormEvent) {
    event.preventDefault();
    if (ready) onName(value.trim());
  }

  return (
    <main className="welcome">
      <form className="welcome-form" onSubmit={done}>
        <p className="mark">
          Factory board<i>.</i>
        </p>
        <h1 id="welcome-title">What’s your name?</h1>
        <p className="welcome-why">
          The board puts it on every mission you add and every answer you give, so the team can see who did what.
        </p>
        <label htmlFor="name">Your name</label>
        <input
          id="name"
          value={value}
          autoComplete="name"
          autoFocus
          maxLength={200}
          onChange={(event) => setValue(event.target.value)}
        />
        <p className="banner">{BANNER}</p>
        <div className="actions">
          <button type="submit" className="primary" disabled={!ready}>
            {action}
          </button>
          {onCancel && (
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </main>
  );
}
