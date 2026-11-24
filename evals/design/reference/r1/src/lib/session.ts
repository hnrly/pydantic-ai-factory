import { cookies } from "next/headers";
import { getDb } from "@/db";
import type { User } from "@/db/schema";
import { createSession, endSession, userForSession } from "./accounts";

const COOKIE = "session";

/** The person signed in on this request, if anyone. */
export async function currentUser(): Promise<User | undefined> {
  const token = (await cookies()).get(COOKIE)?.value;
  return token ? userForSession(getDb(), token) : undefined;
}

/** Sign this browser in. Call only from a server action. */
export async function startSession(userId: number): Promise<void> {
  const { token, expiresAt } = createSession(getDb(), userId);
  (await cookies()).set(COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires: expiresAt,
  });
}

/** Sign this browser out. Call only from a server action. */
export async function endCurrentSession(): Promise<void> {
  const jar = await cookies();
  const token = jar.get(COOKIE)?.value;
  if (token) {
    endSession(getDb(), token);
  }
  jar.delete(COOKIE);
}
