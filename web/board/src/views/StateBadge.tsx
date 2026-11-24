import type { CardState } from '../api';
import { STATE_LABEL } from '../words';

export function StateBadge({ state }: { state: CardState }) {
  return <span className={`badge badge-${state}`}>{STATE_LABEL[state]}</span>;
}
