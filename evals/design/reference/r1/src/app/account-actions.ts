"use server";

import { redirect } from "next/navigation";
import { getDb } from "@/db";
import { EmailTakenError, InvalidAccountError, logIn, signUp } from "@/lib/accounts";
import { endCurrentSession, startSession } from "@/lib/session";

function field(form: FormData, name: string): string {
  return String(form.get(name) ?? "");
}

function back(page: string, message: string): never {
  redirect(`${page}?error=${encodeURIComponent(message)}`);
}

/** Make an account, sign it in and show its bookings. */
export async function signUpAction(form: FormData): Promise<void> {
  let userId: number;
  try {
    userId = signUp(getDb(), {
      name: field(form, "name"),
      email: field(form, "email"),
      password: field(form, "password"),
    }).id;
  } catch (error) {
    if (error instanceof EmailTakenError || error instanceof InvalidAccountError) {
      back("/signup", error.message);
    }
    throw error;
  }
  await startSession(userId);
  redirect("/my-bookings");
}

/** Sign in with an email and password. The message never says which of the two was wrong. */
export async function logInAction(form: FormData): Promise<void> {
  const user = logIn(getDb(), field(form, "email"), field(form, "password"));
  if (!user) {
    back("/login", "That email and password do not match an account.");
  }
  await startSession(user.id);
  redirect("/my-bookings");
}

export async function logOutAction(): Promise<void> {
  await endCurrentSession();
  redirect("/");
}
