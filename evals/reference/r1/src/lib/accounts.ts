import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { and, eq, gt } from "drizzle-orm";
import type { Db } from "@/db";
import { sessions, type User, users } from "@/db/schema";

/** An account already uses that email address. */
export class EmailTakenError extends Error {
  constructor() {
    super("An account already uses that email address. Please log in instead.");
    this.name = "EmailTakenError";
  }
}

/** The sign-up form cannot make an account, such as a short password. */
export class InvalidAccountError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidAccountError";
  }
}

export const MIN_PASSWORD_LENGTH = 8;
const SESSION_DAYS = 30;
const KEY_LENGTH = 64;

export type NewAccount = { name: string; email: string; password: string };

function normaliseEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, KEY_LENGTH);
  return `scrypt:${salt.toString("hex")}:${hash.toString("hex")}`;
}

export function verifyPassword(password: string, stored: string): boolean {
  const [scheme, salt, hash] = stored.split(":");
  if (scheme !== "scrypt" || !salt || !hash) {
    return false;
  }
  const expected = Buffer.from(hash, "hex");
  const actual = scryptSync(password, Buffer.from(salt, "hex"), expected.length);
  return timingSafeEqual(actual, expected);
}

/** Make an account. One email address has at most one account. */
export function signUp(db: Db, account: NewAccount): User {
  const name = account.name.trim();
  const email = normaliseEmail(account.email);
  if (!name) {
    throw new InvalidAccountError("Please give your name.");
  }
  if (!/^[^\s@]+@[^\s@]+$/.test(email)) {
    throw new InvalidAccountError("Please give a valid email address.");
  }
  if (account.password.length < MIN_PASSWORD_LENGTH) {
    throw new InvalidAccountError(`Please choose a password of at least ${MIN_PASSWORD_LENGTH} characters.`);
  }
  try {
    return db
      .insert(users)
      .values({ name, email, passwordHash: hashPassword(account.password), createdAt: new Date().toISOString() })
      .returning()
      .get();
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "SQLITE_CONSTRAINT_UNIQUE") {
      throw new EmailTakenError();
    }
    throw error;
  }
}

/** The account for an email and password, or nothing when either is wrong. */
export function logIn(db: Db, email: string, password: string): User | undefined {
  const user = db.select().from(users).where(eq(users.email, normaliseEmail(email))).get();
  return user && verifyPassword(password, user.passwordHash) ? user : undefined;
}

export function createSession(db: Db, userId: number, now: Date = new Date()): { token: string; expiresAt: Date } {
  const token = randomBytes(32).toString("base64url");
  const expiresAt = new Date(now.getTime() + SESSION_DAYS * 86_400_000);
  db.insert(sessions).values({ id: token, userId, expiresAt: expiresAt.toISOString() }).run();
  return { token, expiresAt };
}

/** The account signed in with this token, or nothing when the session is unknown or has expired. */
export function userForSession(db: Db, token: string, now: Date = new Date()): User | undefined {
  const row = db
    .select({ user: users })
    .from(sessions)
    .innerJoin(users, eq(users.id, sessions.userId))
    .where(and(eq(sessions.id, token), gt(sessions.expiresAt, now.toISOString())))
    .get();
  return row?.user;
}

export function endSession(db: Db, token: string): void {
  db.delete(sessions).where(eq(sessions.id, token)).run();
}
