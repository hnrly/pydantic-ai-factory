import type { DisplayState } from '../api';
import { STATE_LABEL } from '../words';

export function StateBadge({ state }: { state: DisplayState }) {
  return <span className={`badge badge-${state}`}>{STATE_LABEL[state]}</span>;
}
