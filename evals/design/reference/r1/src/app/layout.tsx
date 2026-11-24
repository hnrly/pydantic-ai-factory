import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { currentUser } from "@/lib/session";
import { logOutAction } from "./account-actions";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flight booking",
  description: "Search flights, book a seat and view a booking, with an account or without.",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const user = await currentUser();
  return (
    <html lang="en-GB">
      <body>
        <header>
          <h1>
            <Link href="/">Flight booking</Link>
          </h1>
          <nav>
            {user ? (
              <form action={logOutAction}>
                <span>{user.name}</span>
                <Link href="/my-bookings">My bookings</Link>
                <button type="submit">Log out</button>
              </form>
            ) : (
              <>
                <Link href="/login">Log in</Link> · <Link href="/signup">Sign up</Link>
              </>
            )}
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
