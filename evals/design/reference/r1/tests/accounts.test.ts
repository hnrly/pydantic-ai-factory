import assert from "node:assert/strict";
import { test } from "node:test";
import { openDatabase } from "@/db";
import {
  createSession,
  EmailTakenError,
  endSession,
  InvalidAccountError,
  logIn,
  signUp,
  userForSession,
} from "@/lib/accounts";
import { bookSeat, myBookings } from "@/lib/bookings";
import { oneFlight } from "./helpers";

const ada = { name: "Ada Lovelace", email: "Ada@Example.com", password: "correct horse" };

test("a new account can log in with its password", () => {
  const db = openDatabase(":memory:", { seed: false });
  const user = signUp(db, ada);

  assert.equal(user.email, "ada@example.com");
  assert.equal(logIn(db, "ada@example.com ", "correct horse")?.id, user.id);
});

test("a wrong password or an unknown email does not log in", () => {
  const db = openDatabase(":memory:", { seed: false });
  signUp(db, ada);

  assert.equal(logIn(db, "ada@example.com", "wrong horse"), undefined);
  assert.equal(logIn(db, "bob@example.com", "correct horse"), undefined);
});

test("the password is never stored as written", () => {
  const db = openDatabase(":memory:", { seed: false });
  const user = signUp(db, ada);

  assert.ok(!user.passwordHash.includes("correct horse"));
  assert.match(user.passwordHash, /^scrypt:[0-9a-f]+:[0-9a-f]+$/);
});

test("one email address has at most one account, whatever its case", () => {
  const db = openDatabase(":memory:", { seed: false });
  signUp(db, ada);

  assert.throws(() => signUp(db, { ...ada, email: "ADA@example.com" }), EmailTakenError);
});

test("sign-up needs a name, an email address and a long enough password", () => {
  const db = openDatabase(":memory:", { seed: false });

  assert.throws(() => signUp(db, { ...ada, name: " " }), InvalidAccountError);
  assert.throws(() => signUp(db, { ...ada, email: "nope" }), InvalidAccountError);
  assert.throws(() => signUp(db, { ...ada, password: "short" }), InvalidAccountError);
});

test("a session finds its account until it expires or ends", () => {
  const db = openDatabase(":memory:", { seed: false });
  const user = signUp(db, ada);
  const now = new Date("2030-01-01T00:00:00.000Z");
  const { token, expiresAt } = createSession(db, user.id, now);

  assert.equal(userForSession(db, token, now)?.id, user.id);
  assert.equal(userForSession(db, token, expiresAt), undefined);
  endSession(db, token);
  assert.equal(userForSession(db, token, now), undefined);
});

test("my bookings lists only the bookings that account made", () => {
  const { db, flightId, seatIds } = oneFlight();
  const user = signUp(db, ada);
  const other = signUp(db, { ...ada, email: "bob@example.com" });
  const mine = bookSeat(db, {
    flightId,
    seatId: seatIds[0],
    passengerName: "Ada Lovelace",
    passengerEmail: "ada@example.com",
    userId: user.id,
  });
  bookSeat(db, {
    flightId,
    seatId: seatIds[1],
    passengerName: "Bob",
    passengerEmail: "bob@example.com",
    userId: other.id,
  });

  assert.deepEqual(
    myBookings(db, user.id).map((booking) => booking.reference),
    [mine.reference],
  );
});

test("a booking made while signed out belongs to no account", () => {
  const { db, flightId, seatIds } = oneFlight();
  const user = signUp(db, ada);
  const booking = bookSeat(db, {
    flightId,
    seatId: seatIds[0],
    passengerName: "Ada Lovelace",
    passengerEmail: "ada@example.com",
  });

  assert.equal(booking.userId, null);
  assert.deepEqual(myBookings(db, user.id), []);
});
