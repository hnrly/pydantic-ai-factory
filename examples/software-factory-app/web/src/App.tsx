import { useEffect, useState } from 'react';
import { AllRuns } from './views/AllRuns';
import { Header, type Theme } from './views/Header';
import { Inbox } from './views/Inbox';
import { NewMission } from './views/NewMission';
import { RunPage } from './views/RunPage';

type Route =
  | { page: 'inbox' }
  | { page: 'runs' }
  | { page: 'new' }
  | { page: 'run'; runId: string; jobId?: string };

/** Read the page from the address: #/, #/runs, #/new or #/runs/RUN_ID?job=JOB_ID. */
export function parseRoute(hash: string): Route {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
  if (parts[0] === 'new') return { page: 'new' };
  if (parts[0] === 'runs' && parts[1]) {
    const jobId = new URLSearchParams(query).get('job') ?? undefined;
    return { page: 'run', runId: parts[1], jobId };
  }
  if (parts[0] === 'runs') return { page: 'runs' };
  return { page: 'inbox' };
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
      <main>
        {route.page === 'inbox' && <Inbox />}
        {route.page === 'runs' && <AllRuns />}
        {route.page === 'new' && <NewMission name={name} />}
        {route.page === 'run' && (
          <RunPage key={`${route.runId}:${route.jobId ?? ''}`} runId={route.runId} jobId={route.jobId} name={name} />
        )}
      </main>
    </>
  );
}
