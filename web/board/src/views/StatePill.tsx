import type { ReactNode } from 'react';
import { PILL_LABEL, type Pill } from '../words';

// Each state has its own shape as well as its colour, so colour is never the only sign.
const SHAPES: Record<Pill, ReactNode> = {
  approval: <circle cx="6" cy="6" r="4.2" fill="none" strokeWidth="2" />,
  asks: <path d="M2 2h8v6H5L2 10z" fill="none" strokeWidth="1.6" strokeLinejoin="round" />,
  stalled: <path d="M6 1.2l5 9H1z" fill="none" strokeWidth="1.6" strokeLinejoin="round" />,
  running: (
    <>
      <circle cx="6" cy="6" r="4.2" fill="none" strokeWidth="1.6" />
      <path d="M6 1.8a4.2 4.2 0 0 1 0 8.4z" stroke="none" fill="currentColor" />
    </>
  ),
  queued: <circle cx="6" cy="6" r="4.2" fill="none" strokeWidth="1.6" strokeDasharray="2 2" />,
  done: (
    <>
      <circle cx="6" cy="6" r="5" stroke="none" fill="currentColor" />
      <path d="M3.7 6.2l1.6 1.6 3-3.2" fill="none" stroke="#000" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),
  failed: (
    <>
      <circle cx="6" cy="6" r="4.6" fill="none" strokeWidth="1.5" />
      <path d="M4.2 4.2l3.6 3.6M7.8 4.2l-3.6 3.6" fill="none" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),
  did_not_run: (
    <>
      <circle cx="6" cy="6" r="4.6" fill="none" strokeWidth="1.5" />
      <path d="M3.8 6h4.4" fill="none" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),
  earlier_version: <rect x="2" y="2" width="8" height="8" rx="1.5" fill="none" strokeWidth="1.6" />,
};

/** A state's shape alone, as a card shows it beside its title. */
export function StateIcon({ pill, size = 12 }: { pill: Pill; size?: number }) {
  return (
    <svg className={`state-icon state-icon-${pill}`} width={size} height={size} viewBox="0 0 12 12" aria-hidden="true" stroke="currentColor">
      {SHAPES[pill]}
    </svg>
  );
}

/** What an opened run needs, as a word and a shape. */
export function StatePill({ pill }: { pill: Pill }) {
  return (
    <span className={`pill pill-${pill}`}>
      <StateIcon pill={pill} />
      {PILL_LABEL[pill]}
    </span>
  );
}
