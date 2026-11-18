import { useEffect, useRef } from 'react';

/**
 * Run `task` now, then again `every` milliseconds after each run ends, for as long as it
 * returns true. It starts again from scratch whenever `key` changes, and stops when the
 * component goes away.
 */
export function useRepeat(task: () => Promise<boolean>, every: number, key: unknown): void {
  const latest = useRef(task);
  latest.current = task;

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const again = await latest.current().catch(() => true);
      if (live && again) timer = setTimeout(tick, every);
    };
    void tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [every, key]);
}
