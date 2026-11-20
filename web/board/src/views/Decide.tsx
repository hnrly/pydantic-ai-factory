// The one thing an opened run asks of a person: a decision, a reply, or what to do
// about a stalled run.
import { useState, type ReactNode } from 'react';
import type { Answer, RunDetail, ToolCall } from '../api';
import { useServices } from '../context';
import { clock, reason } from '../words';

export type Choice = Omit<Answer, 'revision'>;

/** Where the run's work is, or stopped. */
export function stationOf(run: RunDetail): string | null {
  return run.wait?.station ?? run.track.find((stop) => stop.state === 'here' || stop.state === 'stopped')?.station ?? run.station;
}

/** How many review rounds the current wait's evidence shows, and how the last one went. */
function reviewed(run: RunDetail): string | null {
  const rounds = run.history.filter((event) => event.kind === 'review' && event.station === run.wait?.station);
  const last = rounds[0];
  if (!last) return null;
  const outcome = last.title.split(': ').slice(1).join(': ');
  const counted = `Reviewed in ${rounds.length} ${rounds.length === 1 ? 'round' : 'rounds'}.`;
  return outcome ? `${counted} Last round: ${outcome}.` : counted;
}

/** Approve, ask for changes or reject. Asking for changes and rejecting each open a note box first. */
export function DecisionBox({
  run,
  name,
  blocked,
  onDecide,
}: {
  run: RunDetail;
  name: string;
  blocked: boolean;
  onDecide: (choice: Choice) => void;
}) {
  const [writing, setWriting] = useState<'changes' | 'reject' | null>(null);
  const [note, setNote] = useState('');
  const wait = run.wait;
  const station = wait?.station ?? 'this station';
  const heading =
    wait?.kind === 'escalation'
      ? wait.offers_work
        ? `${station} needs a person to decide`
        : `${station} could not finish, and needs a person`
      : `The ${station} work is ready for you`;
  const review = reviewed(run);
  const can = (action: RunDetail['actions'][number]) => run.actions.includes(action);

  return (
    <section className="ask-box" aria-labelledby="decide-title">
      <div className="ask-head">
        <h2 id="decide-title">{heading}</h2>
        {review && <span className="quiet">{review}</span>}
      </div>
      {wait && wait.reasons.length > 0 && (
        <ul className="reasons">
          {wait.reasons.map((text, index) => (
            <li key={index}>{text}</li>
          ))}
        </ul>
      )}
      {writing === null ? (
        <div className="actions">
          {can('approve') && (
            <button type="button" className="go" disabled={blocked} onClick={() => onDecide({ kind: 'approve' })}>
              Approve
            </button>
          )}
          {can('changes') && (
            <button type="button" className="plain" disabled={blocked} onClick={() => setWriting('changes')}>
              Ask for changes
            </button>
          )}
          {can('reject') && (
            <button type="button" className="ghost" disabled={blocked} onClick={() => setWriting('reject')}>
              Reject
            </button>
          )}
        </div>
      ) : (
        <form
          className="note-box"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) onDecide({ kind: writing, note: note.trim() });
          }}
        >
          <label htmlFor="note">{writing === 'changes' ? 'What should change?' : 'Why are you rejecting it?'}</label>
          <textarea id="note" rows={4} autoFocus value={note} onChange={(event) => setNote(event.target.value)} />
          <div className="actions">
            <button type="submit" className={writing === 'changes' ? 'go' : 'plain'} disabled={blocked || !note.trim()}>
              {writing === 'changes' ? 'Ask for changes' : 'Reject'}
            </button>
            <button type="button" className="ghost" onClick={() => setWriting(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      <p className="small quiet">Your name, {name}, is recorded with the answer.</p>
    </section>
  );
}

function args(call: ToolCall): string {
  const text = JSON.stringify(call.args);
  return text === '{}' ? `${call.tool}()` : `${call.tool}(${text})`;
}

/** An agent's questions, each with a reply box, and the tool calls it needs signed off, sent together. */
export function QuestionBox({
  run,
  blocked,
  onSend,
}: {
  run: RunDetail;
  blocked: boolean;
  onSend: (choice: Choice) => void;
}) {
  const [replies, setReplies] = useState<Record<string, string>>({});
  const [approvals, setApprovals] = useState<Record<string, boolean>>({});
  const wait = run.wait;
  if (!wait) return null;
  const { questions, sign_offs: signOffs } = wait;
  const agent = wait.agent ?? 'agent';
  const complete =
    questions.every((call) => (replies[call.id] ?? '').trim() !== '') && signOffs.every((call) => call.id in approvals);

  function send() {
    const choice: Choice = { kind: 'reply' };
    if (questions.length > 0) {
      choice.replies = Object.fromEntries(questions.map((call) => [call.id, (replies[call.id] ?? '').trim()]));
    }
    if (signOffs.length > 0) {
      choice.approvals = Object.fromEntries(signOffs.map((call) => [call.id, approvals[call.id] === true]));
    }
    onSend(choice);
  }

  return (
    <section className="question" aria-label={`The ${agent} asks you`}>
      <p className="lead">
        {questions.length > 0
          ? `The ${agent} has ${questions.length === 1 ? 'a question' : 'questions'} before it carries on. Nothing else happens until you reply.`
          : `The ${agent} wants to use a tool that needs your sign-off. Nothing else happens until you answer.`}
      </p>
      {questions.map((call, index) => (
        <div key={call.id} className="ask">
          <div className="said">
            <span className="avatar" aria-hidden="true">
              {agent.slice(0, 1).toUpperCase()}
            </span>
            <div className="said-body">
              <span className="quiet small">
                <strong>{agent}</strong> asked
              </span>
              <div className="bubble">{call.question ?? `It called ${call.tool} and waits for your answer.`}</div>
            </div>
          </div>
          <div className="reply">
            <label htmlFor={`reply-${call.id}`}>
              {questions.length === 1 ? 'Your reply' : `Your reply to question ${index + 1}`}
            </label>
            <textarea
              id={`reply-${call.id}`}
              rows={4}
              value={replies[call.id] ?? ''}
              onChange={(event) => setReplies({ ...replies, [call.id]: event.target.value })}
            />
          </div>
        </div>
      ))}
      {signOffs.length > 0 && (
        <div className="sign-offs">
          <h2 className="panel-label">{questions.length > 0 ? 'Also needs your sign-off' : 'Needs your sign-off'}</h2>
          {signOffs.map((call) => (
            <div key={call.id} className="sign-off" role="group" aria-label={`May the ${agent} run ${call.tool}?`}>
              <span className="sign-off-what">
                <span className="sign-off-tool">{call.tool}</span>
                <code>{args(call)}</code>
              </span>
              <button
                type="button"
                className="plain"
                aria-pressed={approvals[call.id] === true}
                onClick={() => setApprovals({ ...approvals, [call.id]: true })}
              >
                Allow
              </button>
              <button
                type="button"
                className="ghost"
                aria-pressed={approvals[call.id] === false}
                onClick={() => setApprovals({ ...approvals, [call.id]: false })}
              >
                Deny
              </button>
            </div>
          ))}
          {questions.length > 0 && (
            <p className="small quiet">Your reply and your sign-offs go together, when you press Send reply.</p>
          )}
        </div>
      )}
      <div className="actions">
        <button type="button" className="go" disabled={blocked || !complete} onClick={send}>
          {questions.length > 0 ? 'Send reply' : 'Send sign-offs'}
        </button>
      </div>
    </section>
  );
}

/** Why a run stopped without ending, and the choice: start again, or keep waiting. */
export function StalledBox({
  run,
  name,
  onRestarted,
  onKeepWaiting,
}: {
  run: RunDetail;
  name: string;
  onRestarted: () => void;
  onKeepWaiting: () => void;
}) {
  const station = stationOf(run);
  const job = run.job;
  let why = 'It may have crashed, or it may only be slow.';
  if (job?.state === 'failed') why = `Its job failed: ${job.error ?? 'the worker gave no reason.'}`;
  else if (job?.state === 'done') why = 'Its job ended with the work part-way through.';
  else if (job?.claimed_until) why = `Its claim on the job lapsed at ${clock(job.claimed_until)}. ${why}`;
  return (
    <section className="ask-box" aria-labelledby="stalled-title">
      <h2 id="stalled-title">
        {station ? `The worker stopped answering while ${station} was running` : 'The worker stopped before the run was saved'}
      </h2>
      <p>{why}</p>
      <div className="caution">
        <p>
          <strong>Start again</strong> ends this run and begins the mission from the first station. If the worker was
          only slow, anything it does from now on is thrown away.
        </p>
        <Restart run={run} name={name} className="amber" onRestarted={onRestarted}>
          <button type="button" className="plain" onClick={onKeepWaiting}>
            Keep waiting
          </button>
        </Restart>
      </div>
    </section>
  );
}

/** The "Start again" button: one press queues a new run of the same mission. */
export function Restart({
  run,
  name,
  className = 'plain',
  onRestarted,
  children,
}: {
  run: RunDetail;
  name: string;
  className?: string;
  onRestarted: () => void;
  children?: ReactNode;
}) {
  const { api } = useServices();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function restart() {
    setBusy(true);
    setError(null);
    try {
      await api.restart(run.run_id, name.trim());
      onRestarted();
    } catch (caught) {
      setError(reason(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="actions">
        <button type="button" className={className} disabled={busy || !name.trim()} onClick={restart}>
          Start again
        </button>
        {children}
      </div>
      {error && <p className="notice notice-error">{error}</p>}
    </>
  );
}
