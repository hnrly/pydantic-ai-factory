# Flight booking

A small flight-booking web app: search flights, book a seat, view a booking. This file tells a coding agent, or a new contributor, how the code is laid out and how to change it.

## Stack

- Next.js App Router, TypeScript, Node 22.
- SQLite through Drizzle ORM, with the `better-sqlite3` driver.
- Pages are server components. Forms post to server actions, which redirect. There is no client-side state and no API layer.

## Commands

- `npm start`: build the app, then serve it on `PORT` (default 3000). This is the one start command.
- `npm run reset`: delete the database and recreate it from the seed data.
- `npm test`: the checks in `tests/`, on Node's test runner, against a database in memory.
- `npm run typecheck`: generate the route types, then run `tsc`.
- `npm run db:generate`: write a new migration in `drizzle/` after changing `src/db/schema.ts`.

The database is the file at `DATABASE_PATH`, by default `data/app.db`. It is created, migrated and seeded on first use.

## Layout

- `src/app/`: routes. A page reads data through `src/lib/` and never queries tables itself. A route's server actions sit beside it in `actions.ts`.
- `src/lib/`: the rules. Each function takes the database as its first argument, so tests can pass one in memory. Refusals are named errors, such as `SeatTakenError`, whose message is shown to the person.
- `src/db/schema.ts`: the data model. `src/db/index.ts` opens and migrates the database; `src/db/seed.ts` holds the seed data.
- `drizzle/`: migrations made by `drizzle-kit`. Never edit one by hand; change the schema and generate a new one.

## Rules the code keeps

- A seat is never sold twice. The unique index on `bookings.seat_id` holds it; keep it.
- Times are ISO 8601 strings in UTC. Prices are whole pence, shown as pounds.
- A booking reference is six characters from an alphabet without 0, O, 1 or I.

## Making a change

1. Change the schema, then run `npm run db:generate`.
2. Put the rule in `src/lib/`, with a check in `tests/` named as a sentence.
3. Add or change the page and its server action.
4. Run `npm run typecheck` and `npm test`.

Keep dependencies pinned to exact versions, and commit `package-lock.json`.
