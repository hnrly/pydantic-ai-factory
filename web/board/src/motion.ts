import { useLayoutEffect, useRef, type RefObject } from 'react';

/** Whether the reader asked for less motion. Unknown counts as no. */
export function lessMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

const GLIDE = { duration: 560, easing: 'cubic-bezier(0.22, 1, 0.36, 1)' };
const ARRIVE = { duration: 360, easing: 'ease-out' };

interface Place {
  x: number;
  y: number;
  column: string;
}

/**
 * Make a card glide to its new column when it changes column, and nothing else move.
 *
 * After each render it notes where every element with a `data-run` attribute is, and
 * which column its `data-column` names, against the board rather than the window, so
 * scrolling moves nothing. Only a card whose column changed slides from its old place.
 * A card nudged by a neighbour, a new badge or a poll stays still. A card fades in only
 * the first time this tab shows it, once the board's first load is on screen: one added
 * here, or one a poll brings that the board never showed. Nothing moves when the reader
 * asked for less motion, or where the browser cannot animate.
 */
export function useGlide(root: RefObject<HTMLElement | null>): void {
  const places = useRef(new Map<string, Place>());
  const known = useRef(new Set<string>());
  const loaded = useRef(false);

  useLayoutEffect(() => {
    const board = root.current;
    if (!board) return;
    const still = lessMotion();
    const origin = board.getBoundingClientRect();
    const next = new Map<string, Place>();
    for (const element of board.querySelectorAll<HTMLElement>('[data-run]')) {
      const id = element.dataset.run ?? '';
      const column = element.dataset.column ?? '';
      const rect = element.getBoundingClientRect();
      const place = { x: rect.left - origin.left + board.scrollLeft, y: rect.top - origin.top, column };
      next.set(id, place);
      const before = places.current.get(id);
      const fresh = !known.current.has(id);
      known.current.add(id);
      if (still || typeof element.animate !== 'function') continue;
      if (before && before.column !== column) {
        const dx = before.x - place.x;
        const dy = before.y - place.y;
        element.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], GLIDE);
      } else if (!before && fresh && loaded.current) {
        element.animate([{ opacity: 0, transform: 'translateY(-6px) scale(0.98)' }, { opacity: 1, transform: 'none' }], ARRIVE);
      }
    }
    places.current = next;
    loaded.current = true;
  });
}
