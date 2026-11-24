import { useEffect, useRef } from 'react';

/**
 * Run `task` now, then again `every` milliseconds after each run ends, for as long as it
 * returns true. It starts again from scratch whenever `key` changes, and stops when the
 * component goes away. `every` may be a function, asked each time a wait begins, so
 * the task can change the pace; a new `every` applies from the next wait, without a run
 * at once.
 *
 * With `whileVisible`, a tick is skipped while the tab is hidden, and the task runs at
 * once when the tab is shown again.
 */
export function useRepeat(
  task: () => Promise<boolean>,
  every: number | (() => number),
  key: unknown,
  { whileVisible = false }: { whileVisible?: boolean } = {},
): void {
  const latest = useRef(task);
  latest.current = task;
  const pace = useRef(every);
  pace.current = every;

  useEffect(() => {
    let live = true;
    let busy = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (busy) return; // the run in flight sets the next timer
      busy = true;
      clearTimeout(timer);
      let again = true;
      try {
        if (!(whileVisible && document.visibilityState === 'hidden')) {
          again = await latest.current().catch(() => true);
        }
      } finally {
        busy = false;
      }
      if (live && again) timer = setTimeout(tick, typeof pace.current === 'function' ? pace.current() : pace.current);
    };
    const shown = () => {
      if (document.visibilityState === 'visible') void tick();
    };
    if (whileVisible) document.addEventListener('visibilitychange', shown);
    void tick();
    return () => {
      live = false;
      clearTimeout(timer);
      if (whileVisible) document.removeEventListener('visibilitychange', shown);
    };
  }, [key, whileVisible]);
}
