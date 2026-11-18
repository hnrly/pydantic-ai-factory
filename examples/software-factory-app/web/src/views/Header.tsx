export type Theme = 'dark' | 'light';

export const BANNER = 'This is a name, not a sign-in. Anyone can type any name.';

/** The name field, the banner that says what it is, the pages and the theme switch. */
export function Header({
  name,
  onName,
  theme,
  onTheme,
  page,
}: {
  name: string;
  onName: (name: string) => void;
  theme: Theme;
  onTheme: (theme: Theme) => void;
  page: string;
}) {
  const link = (href: string, label: string, current: boolean) => (
    <a href={href} aria-current={current ? 'page' : undefined}>
      {label}
    </a>
  );
  return (
    <header className="header">
      <div className="bar">
        <a className="mark" href="#/">
          Software factory<i>.</i>
        </a>
        <nav>
          {link('#/', 'Inbox', page === 'inbox')}
          {link('#/runs', 'All runs', page === 'runs')}
          {link('#/new', 'New mission', page === 'new')}
        </nav>
        <button
          type="button"
          className="quiet-button"
          onClick={() => onTheme(theme === 'dark' ? 'light' : 'dark')}
        >
          {theme === 'dark' ? 'Light theme' : 'Dark theme'}
        </button>
      </div>
      <div className="who">
        <label htmlFor="name">Your name</label>
        <input
          id="name"
          value={name}
          autoComplete="name"
          maxLength={200}
          onChange={(event) => onName(event.target.value)}
        />
        <p className="banner">{BANNER}</p>
      </div>
    </header>
  );
}
