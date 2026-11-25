import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";
import { type BetterSQLite3Database, drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import * as schema from "./schema";
import { seed } from "./seed";

export type Db = BetterSQLite3Database<typeof schema>;

const MIGRATIONS = path.join(process.cwd(), "drizzle");

/** Where the app keeps its database: `DATABASE_PATH`, or `data/app.db`. */
export function databasePath(): string {
  return process.env.DATABASE_PATH ?? path.join(process.cwd(), "data", "app.db");
}

/**
 * Open a database, bring its tables up to date and, unless told not to, add the
 * seed data when it has no flights. Pass ":memory:" for a throwaway database.
 */
export function openDatabase(file: string, options: { seed?: boolean } = {}): Db {
  if (file !== ":memory:") {
    fs.mkdirSync(path.dirname(file), { recursive: true });
  }
  const sqlite = new Database(file);
  sqlite.pragma("journal_mode = WAL");
  sqlite.pragma("foreign_keys = ON");
  const db = drizzle(sqlite, { schema });
  migrate(db, { migrationsFolder: MIGRATIONS });
  if (options.seed ?? true) {
    seed(db);
  }
  return db;
}

let shared: Db | undefined;

/** The app's one database, opened on first use. */
export function getDb(): Db {
  shared ??= openDatabase(databasePath());
  return shared;
}
