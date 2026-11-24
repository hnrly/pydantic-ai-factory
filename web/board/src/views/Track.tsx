import type { CSSProperties } from 'react';
import type { RunDetail, TrackStop } from '../api';
import { STATION } from '../board';

/** What the work is doing at the station it is at. */
function doing(run: RunDetail): string {
  if (run.state === 'waiting') return run.queued ? 'your answer is queued' : 'waits for you';
  if (run.state === 'queued') return 'queued';
  return 'working';
}

function words(stop: TrackStop, run: RunDetail): string {
  const attempt = stop.attempt ? `attempt ${stop.attempt}` : null;
  if (stop.state === 'here') return [stop.station, attempt, doing(run)].filter(Boolean).join(' · ');
  if (stop.state === 'stopped') return [stop.station, attempt, 'stopped here'].filter(Boolean).join(' · ');
  if (stop.state === 'done' && stop.attempt && stop.attempt > 1) return `${stop.station} · ${attempt}`;
  return stop.station;
}

function Mark({ state }: { state: TrackStop['state'] }) {
  return (
    <svg className="track-mark" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      {state === 'done' && (
        <>
          <circle cx="8" cy="8" r="6" className="track-fill" />
          <path d="M5 8.2l2 2 4-4.2" stroke="#000" strokeWidth="1.6" fill="none" strokeLinecap="round" />
        </>
      )}
      {state === 'here' && (
        <>
          <circle cx="8" cy="8" r="6" fill="none" className="track-line" strokeWidth="2" />
          <circle cx="8" cy="8" r="2.5" className="track-fill" />
        </>
      )}
      {state === 'stopped' && <path d="M8 2.2l6 10.6H2z" fill="none" className="track-line" strokeWidth="1.6" strokeLinejoin="round" />}
      {state === 'ahead' && <circle cx="8" cy="8" r="6" fill="none" className="track-line" strokeWidth="1.5" strokeDasharray="2 2" />}
    </svg>
  );
}

/**
 * Where the work is: the mission, every station in the factory's order, then Done. The
 * station the work is at, or stopped at, is marked as the current step, with its attempt.
 */
export function Track({ run }: { run: RunDetail }) {
  const finished = run.state === 'passed';
  const stops: { key: string; label: string; state: TrackStop['state']; colour: string; current: boolean }[] = [
    { key: 'mission', label: 'Mission', state: 'done', colour: 'var(--strip-missions)', current: false },
    ...run.track.map((stop) => ({
      key: `station:${stop.station}`,
      label: words(stop, run),
      state: stop.state,
      colour: STATION,
      current: stop.state === 'here' || stop.state === 'stopped',
    })),
    { key: 'done', label: 'Done', state: finished ? 'done' : 'ahead', colour: 'var(--strip-done)', current: false },
  ];
  return (
    <nav aria-label="Where the work is">
      <ol className="track">
        {stops.map((stop, index) => {
          const reached = stop.state === 'done' || stop.state === 'here' || stop.state === 'stopped';
          return (
            <li
              key={stop.key}
              className={`track-stop track-${stop.state}`}
              style={{ '--station': stop.colour } as CSSProperties}
              aria-current={stop.current ? 'step' : undefined}
            >
              {index > 0 && <span className={reached ? 'track-join track-join-on' : 'track-join'} aria-hidden="true" />}
              <span className="track-name">
                <Mark state={stop.state} />
                {stop.label}
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
