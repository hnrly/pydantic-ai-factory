import { useCallback, useEffect, useRef, useState } from 'react';
import { BoardView } from './views/BoardView';
import { Header, type BoardSummary } from './views/Header';
import { ListView, type Shown } from './views/ListView';
import { RunPanel } from './views/RunPanel';
import { Welcome } from './views/Welcome';

type Route = { page: 'board' } | { page: 'list'; column: Shown } | { page: 'run'; runId: string; jobId?: string };

/**
 * Read the page from the address: #/, #/list?column=stopped or #/runs/RUN_ID?job=JOB_ID.
 * A run opens as a panel over the board.
 */
export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query);
  if (parts[0] === 'runs' && parts[1]) {
    return { page: 'run', runId: parts[1], jobId: params.get('job') ?? undefined };
  }
  if (parts[0] === 'list') {
    return { page: 'list', column: params.get('column') === 'stopped' ? 'stopped' : 'done' };
  }
  return { page: 'board' };
}

const NAME = 'factory.name';

// The name is kept in this browser only. Storage can be blocked, so it may fail.
function remembered(): string {
  try {
    return localStorage.getItem(NAME)?.trim() ?? '';
  } catch {
    return '';
  }
}

function remember(value: string): void {
  try {
    localStorage.setItem(NAME, value);
  } catch {
    // Not kept: the name still works until the page is closed.
  }
}

/**
 * How many entries back the board, or the list, is from this history entry: 0 on the
 * board or the list, and null on a run that a link opened, with no board behind it.
 * Each entry keeps its own count in `history.state`, so back and forward read it again
 * instead of counting each step.
 */
function backOf(): number | null | undefined {
  const state: unknown = window.history.state;
  if (state && typeof state === 'object' && 'boardBack' in state) {
    const back = (state as { boardBack: unknown }).boardBack;
    if (back === null || typeof back === 'number') return back;
  }
  return undefined;
}

function markBack(back: number | null): void {
  const state: unknown = window.history.state;
  const kept = state && typeof state === 'object' ? state : {};
  window.history.replaceState({ ...kept, boardBack: back }, '', window.location.href);
}

/** The count for the entry the tab is on, written there the first time the tab sees it. */
function entryBack(route: Route, from: number | null): number | null {
  const known = backOf();
  if (known !== undefined) return known;
  const back = route.page !== 'run' ? 0 : from === null ? null : from + 1;
  markBack(back);
  return back;
}

/** Put focus back on a card's link, once the board shows it. */
function focusCard(runId: string): void {
  for (const element of document.querySelectorAll<HTMLElement>('[data-run]')) {
    if (element.dataset.run === runId) {
      element.querySelector<HTMLElement>('.card-title')?.focus({ preventScroll: false });
      return;
    }
  }
}

export function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  const [name, setName] = useState(remembered);
  const [renaming, setRenaming] = useState(false);
  const [summary, setSummary] = useState<BoardSummary | null>(null);
  // How far back the board is from the entry the tab is on. See backOf.
  const back = useRef<number | null>(null);
  const current = useRef(route);
  const returnTo = useRef<string | null>(null);

  useEffect(() => {
    back.current = entryBack(current.current, null);
    const follow = () => {
      const next = parseRoute(window.location.hash);
      if (next.page === 'run' && current.current.page !== 'run') returnTo.current = next.runId;
      back.current = entryBack(next, back.current);
      current.current = next;
      setRoute(next);
    };
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);

  useEffect(() => {
    if (route.page === 'run' || returnTo.current === null) return;
    const runId = returnTo.current;
    returnTo.current = null;
    focusCard(runId);
  }, [route]);

  const closeRun = useCallback(() => {
    if (back.current !== null && back.current > 0) {
      // Back, as the browser's own button would: the board, or the list, comes back.
      window.history.go(-back.current);
      return;
    }
    // A link opened the run: there is nothing to go back to, so the board replaces it.
    const board = { page: 'board' } as const;
    if (current.current.page === 'run') returnTo.current = current.current.runId;
    window.history.replaceState({ boardBack: 0 }, '', '#/');
    back.current = 0;
    current.current = board;
    setRoute(board);
  }, []);

  if (!name || renaming) {
    return (
      <Welcome
        initial={name}
        action={route.page === 'board' ? 'Open the board' : 'Continue'}
        onName={(value) => {
          setName(value);
          remember(value);
          setRenaming(false);
        }}
        onCancel={name ? () => setRenaming(false) : undefined}
      />
    );
  }

  const open = route.page === 'run';
  return (
    <>
      <div className={open ? 'behind' : undefined} inert={open} aria-hidden={open || undefined}>
        <Header name={name} board={route.page !== 'list' ? summary : null} onChangeName={() => setRenaming(true)} />
        <main className={route.page === 'list' ? 'page' : 'wide'}>
          {route.page === 'list' ? (
            <ListView column={route.column} name={name} />
          ) : (
            <BoardView name={name} onSummary={setSummary} under={open} />
          )}
        </main>
      </div>
      {route.page === 'run' && (
        <RunPanel
          key={`${route.runId}:${route.jobId ?? ''}`}
          runId={route.runId}
          jobId={route.jobId}
          name={name}
          onClose={closeRun}
        />
      )}
    </>
  );
}
