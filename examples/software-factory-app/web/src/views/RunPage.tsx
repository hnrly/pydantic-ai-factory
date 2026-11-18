import { useState } from 'react';
import Markdown from 'react-markdown';
import { Conflict, type Answer, type Job, type RunDetail, type ToolCall } from '../api';
import { useServices } from '../context';
import { useRepeat } from '../poll';
import { NEEDS_NAME, reason, WAITING_LABEL, when } from '../words';
import { StateBadge } from './StateBadge';

type Choice = Omit<Answer, 'by' | 'revision'>;

const MOVING = new Set(['queued', 'running']);
const JOB_WORDS: Record<Job['state'], string> = {
  queued: 'Your request is queued for the worker.',
  claimed: 'The worker is on it.',
  done: 'Done.',
  failed: 'It failed.',
};

/** The newest of a run's latest jobs, when it failed. */
function lastFailure(run: RunDetail): Job | null {
  const newest = Object.values(run.jobs).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return newest?.state === 'failed' ? newest : null;
}

/**
 * One run: its mission, state, actions, output and steps.
 *
 * A queued or running run is checked every two seconds until it waits or ends. After a
 * person acts, the page follows their job every two seconds until it is done or failed,
 * then reloads the run. `jobId` is a job to follow from the start, such as a new run's.
 */
export function RunPage({ runId, jobId, name }: { runId: string; jobId?: string; name: string }) {
  const { api, every } = useServices();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloads, setReloads] = useState(0);
  const [following, setFollowing] = useState<string | null>(jobId ?? null);
  const [jobState, setJobState] = useState<Job['state'] | null>(null);
  const [jobFailure, setJobFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = () => setReloads((count) => count + 1);

  useRepeat(
    async () => {
      try {
        const fresh = await api.getRun(runId);
        setRun(fresh);
        setLoadError(null);
        return MOVING.has(fresh.state);
      } catch (caught) {
        setLoadError(reason(caught));
        return true;
      }
    },
    every.moving,
    `${runId}:${reloads}`,
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
      const queued = await api.answer(run.run_id, { ...choice, by: name.trim(), revision: run.revision });
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

  async function startAgain() {
    if (!run) return;
    setBusy(true);
    setNotice(null);
    try {
      const queued = await api.startRun(run.mission, name.trim());
      window.location.hash = `#/runs/${queued.run_id}?job=${queued.job_id}`;
    } catch (caught) {
      setNotice(reason(caught));
      setBusy(false);
    }
  }

  if (!run) {
    return (
      <section>
        <h1>Run</h1>
        {loadError ? (
          <p className="notice notice-error">The run could not load: {loadError}</p>
        ) : (
          <p className="quiet">Loading…</p>
        )}
      </section>
    );
  }

  const failure = jobFailure ?? (following ? null : lastFailure(run)?.error ?? null);
  const blocked = busy || following !== null || !name.trim();

  return (
    <article>
      <p className="eyebrow">
        Run <code>{run.run_id}</code>
      </p>
      <h1>{run.mission || 'A run with no mission'}</h1>
      <p className="meta">
        <StateBadge state={run.state} /> Last change {when(run.last_change)}
      </p>

      <Explanation run={run} />

      {following && jobState && <p className="notice">{JOB_WORDS[jobState]}</p>}
      {failure && <p className="notice notice-error">The last job failed: {failure}</p>}
      {notice && <p className="notice notice-error">{notice}</p>}
      {loadError && <p className="notice notice-error">The run could not reload: {loadError}</p>}

      {run.actions.length > 0 && !name.trim() && <p className="quiet">{NEEDS_NAME}</p>}
      {run.actions.includes('start_again') && (
        <div className="actions">
          <button type="button" className="primary" disabled={blocked} onClick={startAgain}>
            Start again
          </button>
        </div>
      )}
      {run.waiting && run.actions.includes('reply') && (
        <ReplyForm questions={run.waiting.questions} signOffs={run.waiting.sign_offs} blocked={blocked} onSend={act} />
      )}
      {run.actions.some((action) => action === 'approve' || action === 'changes' || action === 'reject') && (
        <DecisionForm actions={run.actions} blocked={blocked} onDecide={act} />
      )}

      {run.output && (
        <section className="output">
          <h2 className="section-label">Output</h2>
          <div className="markdown">
            <Markdown>{run.output}</Markdown>
          </div>
        </section>
      )}

      {run.steps.length > 0 && (
        <section>
          <h2 className="section-label">Steps</h2>
          <ol className="steps">
            {run.steps.map((step, index) => (
              <li key={index}>
                {step.station}, attempt {step.attempt}: {step.outcome}
                {step.reason ? ` (${step.reason})` : ''}
              </li>
            ))}
          </ol>
        </section>
      )}

      {run.approvals.length > 0 && (
        <section>
          <h2 className="section-label">Decisions</h2>
          <ul className="steps">
            {run.approvals.map((decision, index) => (
              <li key={index}>
                {decision.by}: {decision.decision} at {decision.station}, {when(decision.at)}
                {decision.note ? ` (“${decision.note}”)` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

function Explanation({ run }: { run: RunDetail }) {
  switch (run.state) {
    case 'waiting':
      return run.waiting ? (
        <div className="explain">
          <p>
            {WAITING_LABEL[run.waiting.kind]} at the <strong>{run.waiting.station}</strong> station
            {run.waiting.agent ? `, from the ${run.waiting.agent} agent` : ''}.
          </p>
          {run.waiting.reasons.length > 0 && (
            <ul>
              {run.waiting.reasons.map((text, index) => (
                <li key={index}>{text}</li>
              ))}
            </ul>
          )}
          {run.waiting.kind === 'escalation' && !run.waiting.offers_work && (
            <p className="quiet">There is no work to accept, so you can ask for changes or reject.</p>
          )}
        </div>
      ) : null;
    case 'stalled':
      return (
        <p className="explain">
          This run has stalled: nothing has changed for a long time, so its worker probably stopped
          part-way. It cannot be carried on. Start again queues a new run with the same mission; this
          run stays as it is.
        </p>
      );
    case 'earlier_version':
      return (
        <p className="explain">
          This run was made by an earlier version of this factory, so it cannot be answered here.
        </p>
      );
    case 'queued':
      return <p className="explain">Queued. A worker will pick it up soon. This page checks every two seconds.</p>;
    case 'running':
      return <p className="explain">Running. This page checks every two seconds.</p>;
    default:
      return null;
  }
}

function DecisionForm({
  actions,
  blocked,
  onDecide,
}: {
  actions: RunDetail['actions'];
  blocked: boolean;
  onDecide: (choice: Choice) => void;
}) {
  const [note, setNote] = useState('');
  const hasNote = note.trim() !== '';

  return (
    <div className="stack decide">
      <label htmlFor="note">Note (needed to ask for changes or to reject)</label>
      <textarea id="note" rows={3} value={note} onChange={(event) => setNote(event.target.value)} />
      <div className="actions">
        {actions.includes('approve') && (
          <button type="button" className="primary" disabled={blocked} onClick={() => onDecide({ choice: 'approve' })}>
            Approve
          </button>
        )}
        {actions.includes('changes') && (
          <button
            type="button"
            disabled={blocked || !hasNote}
            onClick={() => onDecide({ choice: 'changes', note: note.trim() })}
          >
            Ask for changes
          </button>
        )}
        {actions.includes('reject') && (
          <button
            type="button"
            disabled={blocked || !hasNote}
            onClick={() => onDecide({ choice: 'reject', note: note.trim() })}
          >
            Reject
          </button>
        )}
      </div>
    </div>
  );
}

function describe(call: ToolCall): string {
  const args = JSON.stringify(call.args);
  return args === '{}' ? call.tool : `${call.tool} with ${args}`;
}

function ReplyForm({
  questions,
  signOffs,
  blocked,
  onSend,
}: {
  questions: ToolCall[];
  signOffs: ToolCall[];
  blocked: boolean;
  onSend: (choice: Choice) => void;
}) {
  const [replies, setReplies] = useState<Record<string, string>>({});
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const complete =
    questions.every((call) => (replies[call.id] ?? '').trim() !== '') &&
    signOffs.every((call) => call.id in approvals);

  function send() {
    const choice: Choice = { choice: 'reply' };
    if (questions.length > 0) {
      choice.replies = Object.fromEntries(questions.map((call) => [call.id, (replies[call.id] ?? '').trim()]));
    }
    if (signOffs.length > 0) {
      choice.approvals = Object.fromEntries(signOffs.map((call) => [call.id, approvals[call.id] === true]));
    }
    onSend(choice);
  }

  return (
    <div className="stack decide">
      {questions.map((call) => (
        <div key={call.id} className="stack">
          <label htmlFor={`reply-${call.id}`}>{call.question ?? `Answer to ${call.tool}`}</label>
          <textarea
            id={`reply-${call.id}`}
            rows={3}
            value={replies[call.id] ?? ''}
            onChange={(event) => setReplies({ ...replies, [call.id]: event.target.value })}
          />
        </div>
      ))}
      {signOffs.map((call) => (
        <fieldset key={call.id}>
          <legend>
            May the agent run <code>{describe(call)}</code>?
          </legend>
          <label>
            <input
              type="radio"
              name={`sign-off-${call.id}`}
              checked={approvals[call.id] === true}
              onChange={() => setApprovals({ ...approvals, [call.id]: true })}
            />{' '}
            Approve
          </label>
          <label>
            <input
              type="radio"
              name={`sign-off-${call.id}`}
              checked={approvals[call.id] === false}
              onChange={() => setApprovals({ ...approvals, [call.id]: false })}
            />{' '}
            Deny
          </label>
        </fieldset>
      ))}
      <div className="actions">
        <button type="button" className="primary" disabled={blocked || !complete} onClick={send}>
          Send answers
        </button>
      </div>
    </div>
  );
}
