/** What the board tells the header: the factory's name, and the cards that need a person. */
export interface BoardSummary {
  factory: string;
  needed: number;
  /** Scroll to the card that has waited longest for a person, and flash it. */
  jump: () => void;
}

/** The mark, the factory, the "needs you" count on the board, and the person's name. */
export function Header({
  name,
  board,
  onChangeName,
}: {
  name: string;
  board: BoardSummary | null;
  onChangeName: () => void;
}) {
  return (
    <header className="top">
      <a className="mark" href="#/">
        Factory board<i>.</i>
      </a>
      {board && <span className="factory">{board.factory}</span>}
      <div className="top-end">
        {board &&
          (board.needed > 0 ? (
            <button type="button" className="needs" onClick={board.jump}>
              <span className="needs-dot" aria-hidden="true" />
              {board.needed} {board.needed === 1 ? 'needs' : 'need'} you
            </button>
          ) : (
            <span className="needs needs-none">Nothing needs you</span>
          ))}
        <span className="who">
          <span className="who-name" title="A name, not a sign-in">
            {name}
          </span>
          <button type="button" className="quiet-button" onClick={onChangeName}>
            Change name
          </button>
        </span>
      </div>
    </header>
  );
}
