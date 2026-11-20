import { useEffect, useState } from 'react';
import type { ListColumn } from './api';
import { BoardView } from './views/BoardView';
import { Header, type Theme } from './views/Header';
import { ListView } from './views/ListView';
import { RunPage } from './views/RunPage';

type Route =
  | { page: 'board' }
  | { page: 'list'; column: ListColumn }
  | { page: 'run'; runId: string; jobId?: string };

const COLUMNS: readonly ListColumn[] = ['all', 'done', 'stopped'];

/** Read the page from the address: #/, #/list?column=done or #/runs/RUN_ID?job=JOB_ID. */
export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query);
  if (parts[0] === 'runs' && parts[1]) {
    return { page: 'run', runId: parts[1], jobId: params.get('job') ?? undefined };
  }
  if (parts[0] === 'list') {
    const asked = params.get('column') as ListColumn | null;
    return { page: 'list', column: asked && COLUMNS.includes(asked) ? asked : 'all' };
  }
  return { page: 'board' };
}

// The name and theme are kept in this browser only. Storage can be blocked, so it may fail.
function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Not kept: the value still works until the page is closed.
  }
}

export function App() {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  const [name, setName] = useState(() => remembered('factory.name') ?? '');
  const [theme, setTheme] = useState<Theme>(() => (remembered('factory.theme') === 'light' ? 'light' : 'dark'));

  useEffect(() => {
    const follow = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <>
      <Header
        name={name}
        onName={(value) => {
          setName(value);
          remember('factory.name', value);
        }}
        theme={theme}
        onTheme={(value) => {
          setTheme(value);
          remember('factory.theme', value);
        }}
        page={route.page}
      />
      <main className={route.page === 'board' ? 'wide' : undefined}>
        {route.page === 'board' && <BoardView name={name} />}
        {route.page === 'list' && <ListView column={route.column} name={name} />}
        {route.page === 'run' && (
          <RunPage key={`${route.runId}:${route.jobId ?? ''}`} runId={route.runId} jobId={route.jobId} name={name} />
        )}
      </main>
    </>
  );
}
