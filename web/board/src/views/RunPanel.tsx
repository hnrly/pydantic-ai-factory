import { useEffect, useRef, useState, type CSSProperties } from 'react';
import Markdown from 'react-markdown';
import { ApiError, Conflict, type Job, type RunDetail } from '../api';
import { STATION } from '../board';
import { useServices } from '../context';
import { useRepeat } from '../poll';
import { clock, lasting, NEEDS_NAME, pillOf, reason, STATE_LABEL } from '../words';
import { DecisionBox, QuestionBox, Restart, StalledBox, stationOf, type Choice } from './Decide';
import { History } from './History';
import { StatePill } from './StatePill';
import { Track } from './Track';

const MOVING = new Set(['queued', 'starting', 'running']);
const JOB_WORDS: Record<Job['state'], string> = {
  queued: 'Your answer is queued for the worker.',
  claimed: 'The worker is on it.',
  done: 'Done.',
  failed: 'It failed.',
};

/** How long the run has waited, or when it ended, then who started it and when. */
function timing(run: RunDetail): string {
  const parts: string[] = [];
  if (run.state === 'waiting' && run.waiting_since) parts.push(`Waiting ${lasting(run.waiting_since)}`);
  else if (run.state === 'stalled' && run.job) {
    parts.push(`No word from its worker for ${lasting(run.job.claimed_until ?? run.job.updated_at)}`);
  } else if (run.column === 'done' || run.column === 'stopped') parts.push(`Ended ${clock(run.updated_at)}`);
  if (run.started_by && run.created_at) parts.push(`started by ${run.started_by} at ${clock(run.created_at)}`);
  else if (run.created_at) parts.push(`started at ${clock(run.created_at)}`);
  const text = parts.join(' · ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Whether a key press is in a text field that holds text, which Escape should leave first. */
function typing(target: EventTarget | null): target is HTMLInputElement | HTMLTextAreaElement {
  return (target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement) && target.value !== '';
}

/** Copy the run's address, where the browser allows it. */
function CopyLink() {
  const [copied, setCopied] = useState(false);
  if (typeof navigator === 'undefined' || !navigator.clipboard) return null;
  return (
    <button
      type="button"
      className="link-button"
      onClick={() => {
        void navigator.clipboard.writeText(window.location.href).then(
          () => setCopied(true),
          () => setCopied(false),
        );
      }}
    >
      {copied ? 'Link copied' : 'Copy link'}
    </button>
  );
}

/**
 * One run, opened as a panel over the board: its state, mission and track, the one
 * thing it asks of a person, the work itself, and its history.
 *
 * The run is checked for as long as the panel is open, while the tab is visible: every
 * two seconds while it is queued or running, and at the board's pace once it waits or
 * ends, so an answer from someone else, or a slow worker coming back, still shows. A run
 * that does not exist is not asked for again. After a person answers, the panel follows
 * their job until it is done or failed, then reloads the run. `jobId` is a job to follow
 * from the start, such as a new run's. Escape, the close button and "Keep waiting" call
 * `onClose`; Escape in a text field that holds text leaves the field first, so a note
 * being written is not lost.
 */
export function RunPanel({
  runId,
  jobId,
  name,
  onClose,
}: {
  runId: string;
  jobId?: string;
  name: string;
  onClose: () => void;
}) {
  const { api, every } = useServices();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const moving = useRef(true);
  const [reloads, setReloads] = useState(0);
  const [following, setFollowing] = useState<string | null>(jobId ?? null);
  const [jobState, setJobState] = useState<Job['state'] | null>(null);
  const [jobFailure, setJobFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  const reload = () => setReloads((count) => count + 1);

  // Focus moves into the panel when it opens, and Escape closes it.
  useEffect(() => {
    panel.current?.focus({ preventScroll: true });
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (typing(event.target)) {
        event.target.blur();
        panel.current?.focus({ preventScroll: true });
        return;
      }
      close.current();
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, []);

  useRepeat(
    async () => {
      try {
        const fresh = await api.getRun(runId);
        setRun(fresh);
        setLoadError(null);
        moving.current = MOVING.has(fresh.state) || fresh.queued;
        return true;
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 404) {
          setMissing(true);
          return false;
        }
        setLoadError(reason(caught));
        return true;
      }
    },
    () => (moving.current ? every.moving : every.board),
    `${runId}:${reloads}`,
    { whileVisible: true },
  );

  useRepeat(
    async () => {
      if (!following) return false;
      const job = await api.getJob(following);
      setJobState(job.state);
      if (job.state === 'done' || job.state === 'failed') {
        if (job.state === 'failed') setJobFailure(job.error ?? 'The worker gave no reason.');
        setFollowing(null);
        reload();
        return false;
      }
      return true;
    },
    every.moving,
    following,
  );

  async function act(choice: Choice) {
    if (!run || run.revision === null) return;
    setBusy(true);
    setNotice(null);
    setJobFailure(null);
    try {
      const queued = await api.answer(run.run_id, { ...choice, revision: run.revision }, name.trim());
      setJobState('queued');
      setFollowing(queued.job_id);
    } catch (caught) {
      setNotice(reason(caught));
      if (caught instanceof Conflict) {
        setRun(caught.run);
        reload();
      }
    } finally {
      setBusy(false);
    }
  }

  const station = run ? stationOf(run) : null;
  const index = run && station ? run.track.findIndex((stop) => stop.station === station) : -1;
  const failure = run ? (jobFailure ?? (following ? null : run.job?.state === 'failed' ? run.job.error : null)) : null;
  const blocked = busy || following !== null || !name.trim();
  const decides = run?.actions.some((action) => action === 'approve' || action === 'changes' || action === 'reject');
  const restarted = run?.history.find((event) => event.kind === 'restarted');

  return (
    <div className="panel-layer">
      <div className="scrim" aria-hidden="true" onClick={onClose} />
      <section
        className="panel"
        role="dialog"
        aria-modal="true"
        aria-label={run?.mission || 'Run'}
        tabIndex={-1}
        ref={panel}
        style={{ '--station': index >= 0 ? STATION : 'var(--hn-gray-3)' } as CSSProperties}
      >
        <header className="panel-head">
          <div className="panel-bar">
            <div className="panel-state">
              {run && <StatePill pill={pillOf(run.state, run.wait?.kind ?? null)} />}
              {run && <span className="quiet small">{timing(run)}</span>}
            </div>
            {run && <CopyLink />}
            <button type="button" className="close" aria-label="Close and return to the board" onClick={onClose}>
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          {run ? (
            <>
              <h1 className="mission">{run.mission || 'A run with no mission'}</h1>
              <Track run={run} />
            </>
          ) : missing ? (
            <div className="notice">
              <p>There is no run {runId}. The address may be wrong, or the run may belong to another board.</p>
              <a className="button-link" href="#/">
                Back to the board
              </a>
            </div>
          ) : loadError ? (
            <p className="notice notice-error">The run could not load: {loadError}</p>
          ) : (
            <p className="quiet">Loading the run…</p>
          )}
        </header>

        {run && (
          <div className="panel-body">
            <div className="panel-main">
              {following && jobState && <p className="notice">{JOB_WORDS[jobState]}</p>}
              {failure && <p className="notice notice-error">The last job failed: {failure}</p>}
              {notice && <p className="notice notice-error">{notice}</p>}
              {loadError && <p className="notice notice-error">The run could not reload: {loadError}</p>}
              {run.actions.length > 0 && !name.trim() && <p className="quiet">{NEEDS_NAME}</p>}

              {run.restarted_as && (
                <div className="restarted">
                  <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
                    <path
                      d="M4 10a6 6 0 1 0 2-4.5M4 3v3h3"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  <span>
                    {restarted?.by ? `Started again by ${restarted.by}` : 'Started again'}
                    {restarted?.at ? ` at ${clock(restarted.at)}` : ''}. This run ended as{' '}
                    {STATE_LABEL[run.state].toLowerCase()}.
                  </span>
                  <a className="button-link" href={`#/runs/${encodeURIComponent(run.restarted_as)}`}>
                    Open the new run
                  </a>
                </div>
              )}

              {run.state === 'waiting' && decides && (
                <DecisionBox key={run.revision} run={run} name={name.trim()} blocked={blocked} onDecide={act} />
              )}
              {run.state === 'waiting' && run.actions.includes('reply') && (
                <QuestionBox key={run.revision} run={run} blocked={blocked} onSend={act} />
              )}
              {run.state === 'stalled' && run.actions.includes('start_again') && !run.restarted_as && (
                <StalledBox run={run} name={name} onRestarted={reload} onKeepWaiting={onClose} />
              )}
              {run.state !== 'stalled' && run.actions.includes('start_again') && !run.restarted_as && (
                <div className="ended">
                  <p>
                    {run.state === 'earlier_version'
                      ? 'An earlier version of this factory made this run, so it cannot be answered here.'
                      : (run.reason ?? 'This run ended before the work was done.')}{' '}
                    Start again begins the mission from the first station as a new run. This run stays in Stopped.
                  </p>
                  <Restart run={run} name={name} onRestarted={reload} />
                </div>
              )}
              {run.state === 'earlier_version' && !run.actions.includes('start_again') && (
                <p className="quiet">An earlier version of this factory made this run, so it cannot be answered here.</p>
              )}
              {run.state === 'queued' && <p className="quiet">Queued. A worker will pick it up soon.</p>}
              {(run.state === 'running' || run.state === 'starting') && (
                <p className="quiet">A worker is on it. This panel checks every two seconds.</p>
              )}

              {run.output && (
                <article className="work" aria-label="The work">
                  <p className="work-label">
                    <span className="work-swatch" aria-hidden="true" />
                    {run.state === 'waiting' ? `${run.wait?.station ?? station ?? 'The work'}, on offer` : 'Output'}
                  </p>
                  {run.output_format === 'json' ? (
                    <pre className="json">{run.output}</pre>
                  ) : run.output_format === 'text' ? (
                    <pre className="text">{run.output}</pre>
                  ) : (
                    <div className="doc">
                      <Markdown>{run.output}</Markdown>
                    </div>
                  )}
                </article>
              )}
            </div>
            <History run={run} />
          </div>
        )}
      </section>
    </div>
  );
}
