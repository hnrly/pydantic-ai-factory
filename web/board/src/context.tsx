import { createContext, useContext, type ReactNode } from 'react';
import { httpApi, type Api } from './api';

/** How often pages check for changes, in milliseconds. */
export interface Intervals {
  /** A queued or running run, or a job a person is following. */
  moving: number;
  /** The board, while its tab is visible. */
  board: number;
}

export const INTERVALS: Intervals = { moving: 2_000, board: 3_000 };

interface Services {
  api: Api;
  every: Intervals;
}

const Context = createContext<Services>({ api: httpApi, every: INTERVALS });

/** Give the views an API and check intervals. Tests pass a fake API and short intervals. */
export function Services({ api, every = INTERVALS, children }: {
  api: Api;
  every?: Intervals;
  children: ReactNode;
}) {
  return <Context.Provider value={{ api, every }}>{children}</Context.Provider>;
}

export function useServices(): Services {
  return useContext(Context);
}
