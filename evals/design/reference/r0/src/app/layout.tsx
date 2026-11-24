import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flight booking",
  description: "Search flights, book a seat and view a booking.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en-GB">
      <body>
        <header>
          <h1>
            <Link href="/">Flight booking</Link>
          </h1>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
