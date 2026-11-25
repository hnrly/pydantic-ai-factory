# The reference flight-booking app

This is the design the benchmark's reference apps are built from. Software Factory Evals asks a system under test to design changes to a flight-booking app, one rung at a time. Each rung after the first starts from the benchmark's own reference app at the rung before, never from what a system built. These apps are those starting points:

| Folder | Rung it completes | It is the starting code of |
|---|---|---|
| `r0/` | Core booking: search flights, book a seat, view a booking | The accounts rung |
| `r1/` | Accounts: sign-up, login, my bookings, on top of `r0/` | The changes rung |

The apps are fixtures of the benchmark. The author owns them, and they change only with a new `BENCHMARK_VERSION`. They hold no value from any fact sheet: a case places a value in its repository only through its overlay, and `tests/test_evals_reference.py` checks it.

## Stack

- Next.js with the App Router, in TypeScript, on Node 22.
- SQLite through Drizzle ORM, with the `better-sqlite3` driver.
- Pages are server components. Forms post to server actions, which redirect. There is no client-side state and no API layer.
- Migrations are SQL files made by `drizzle-kit generate` from `src/db/schema.ts`. The app applies them when it opens the database.
- Checks run with Node's own test runner, through `tsx`, against a database in memory.

Every package is pinned to an exact version, and `package-lock.json` is committed.

## Folder layout

```text
AGENTS.md            the app's conventions, for a coding agent or a new contributor
CLAUDE.md            points to AGENTS.md
Dockerfile           runs the one start command in a container
drizzle/             SQL migrations, made by drizzle-kit; never edited by hand
drizzle.config.ts
scripts/reset.ts     the reset command
src/app/             routes: pages and their server actions
src/db/schema.ts     the data model
src/db/index.ts      opens the database, migrates it and seeds it
src/db/seed.ts       the seed data
src/lib/             the rules of the domain, each a function that takes the database
tests/               the app's own checks
```

The rules live in `src/lib/`, not in pages, so the checks can test them without a server.

## Commands

| Command | What it does |
|---|---|
| `npm start` | The one start command. Builds the app, then serves it on `PORT`, by default 3000. The database is created and seeded on first use |
| `npm run reset` | The one reset command. Deletes the database and recreates it from the seed data |
| `npm test` | The app's own checks |
| `npm run typecheck` | Generates Next.js's route types, then runs the TypeScript compiler |

The database is the file at `DATABASE_PATH`, by default `data/app.db`. It is never committed.

## Data model

Core booking (`r0/`):

| Table | Columns | Rules |
|---|---|---|
| `flights` | id, number, origin, destination, departs_at, arrives_at, price_pence | Times are ISO 8601 in UTC. Prices are whole pence. Airports are three-letter codes |
| `seats` | id, flight_id, label | A label such as "4C" is unique on its flight |
| `bookings` | id, reference, flight_id, seat_id, passenger_name, passenger_email, created_at | The reference is six characters and unique. The seat id is unique |

Accounts (`r1/`) adds:

| Table | Columns | Rules |
|---|---|---|
| `users` | id, email, name, password_hash, created_at | The email is stored in lower case and is unique. The hash is scrypt with a random salt |
| `sessions` | id, user_id, expires_at | The id is a random token, held in an http-only cookie |
| `bookings` | gains user_id | Empty for a booking made while signed out |

The seed data is a week of flights on eight routes between seven airports, starting the day after the database is made. Every seat is free. There are no seed accounts.

## Core booking (`r0/`)

Features:

- Search flights by origin, destination and day.
- Choose a free seat on a flight and book it for a named passenger with an email address.
- View a booking by its reference, typed in any case.

Invariants:

- A seat is never sold twice. The database's unique index on the seat id holds it, and a second booking of a seat is refused with a message.
- A seat can be booked only on its own flight.
- Every booking has a unique reference, which leaves out characters that are easy to misread.

## Accounts (`r1/`)

`r1/` is `r0/` with accounts added the way the rest of the code is built: two tables and a column in the schema, one new migration, a module of rules in `src/lib/accounts.ts`, the cookie in `src/lib/session.ts`, and pages for each feature. Everything `r0/` does still works, signed in or not.

Features:

- Sign up with a name, an email address and a password.
- Log in and log out.
- My bookings: the bookings made while signed in to this account.
- While signed in, the booking form is filled in with the account's name and email.

Invariants:

- One email address has at most one account, whatever its case.
- A password is never stored as written.
- A failed login never says whether the email or the password was wrong.
- My bookings shows only the bookings that account made. A booking made while signed out belongs to no account.
- A session ends when it expires or when its person logs out.
