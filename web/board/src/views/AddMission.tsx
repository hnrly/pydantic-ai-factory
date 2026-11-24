import { useLayoutEffect, useRef, useState } from 'react';
import type { Queued } from '../api';
import { useServices } from '../context';
import { reason } from '../words';

/**
 * The box at the top of Missions. Enter adds the mission, Shift+Enter starts a new line,
 * and the box grows with what is typed. The new card stays on the board.
 */
export function AddMission({ name, onAdded }: { name: string; onAdded: (queued: Queued, mission: string) => void }) {
  const { api } = useServices();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const ready = text.trim() !== '' && name.trim() !== '' && !busy;

  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    element.style.height = 'auto';
    if (element.scrollHeight > 0) element.style.height = `${element.scrollHeight}px`;
  }, [text]);

  async function add(event?: { preventDefault(): void }) {
    event?.preventDefault();
    if (!ready) return;
    const mission = text.trim();
    setBusy(true);
    setError(null);
    try {
      const queued = await api.startRun(mission, name.trim());
      setText('');
      onAdded(queued, mission);
    } catch (caught) {
      setError(reason(caught));
    } finally {
      setBusy(false);
      box.current?.focus();
    }
  }

  return (
    <form className="add" onSubmit={add}>
      <label htmlFor="mission">Add a mission</label>
      <textarea
        id="mission"
        ref={box}
        rows={1}
        value={text}
        placeholder="What should the factory make?"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) void add(event);
        }}
      />
      <div className="add-foot">
        <span className="add-hint">Shift+Enter for a new line</span>
        <button type="submit" className="primary" disabled={!ready}>
          Add
        </button>
      </div>
      {error && <p className="notice notice-error">The mission was not added: {error}</p>}
    </form>
  );
}
