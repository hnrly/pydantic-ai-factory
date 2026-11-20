import assert from "node:assert/strict";
import { test } from "node:test";
import { bookSeat, findBooking, InvalidBookingError, SeatTakenError } from "@/lib/bookings";
import { seatMap } from "@/lib/flights";
import { oneFlight } from "./helpers";

const passenger = { passengerName: "Ada Lovelace", passengerEmail: "ada@example.com" };

test("a booked seat can be viewed by its reference", () => {
  const { db, flightId, seatIds } = oneFlight();
  const booking = bookSeat(db, { flightId, seatId: seatIds[0], ...passenger });

  assert.match(booking.reference, /^[A-Z2-9]{6}$/);
  const found = findBooking(db, booking.reference.toLowerCase());
  assert.equal(found?.seatLabel, "1A");
  assert.equal(found?.flight.number, "FB900");
  assert.equal(found?.passengerName, "Ada Lovelace");
});

test("a seat is never sold twice", () => {
  const { db, flightId, seatIds } = oneFlight();
  bookSeat(db, { flightId, seatId: seatIds[0], ...passenger });

  assert.throws(
    () => bookSeat(db, { flightId, seatId: seatIds[0], passengerName: "Bob", passengerEmail: "bob@example.com" }),
    SeatTakenError,
  );
});

test("a booked seat shows as taken and the others stay free", () => {
  const { db, flightId, seatIds } = oneFlight();
  bookSeat(db, { flightId, seatId: seatIds[1], ...passenger });

  assert.deepEqual(
    seatMap(db, flightId).map((seat) => [seat.label, seat.taken]),
    [
      ["1A", false],
      ["1B", true],
    ],
  );
});

test("a seat from another flight cannot be booked", () => {
  const { db, flightId, seatIds } = oneFlight();

  assert.throws(() => bookSeat(db, { flightId: flightId + 1, seatId: seatIds[0], ...passenger }), InvalidBookingError);
});

test("a booking needs a name and an email address", () => {
  const { db, flightId, seatIds } = oneFlight();

  assert.throws(() => bookSeat(db, { flightId, seatId: seatIds[0], passengerName: " ", passengerEmail: "a@b.c" }), InvalidBookingError);
  assert.throws(() => bookSeat(db, { flightId, seatId: seatIds[0], passengerName: "Ada", passengerEmail: "nope" }), InvalidBookingError);
});

test("an unknown reference finds no booking", () => {
  const { db } = oneFlight();

  assert.equal(findBooking(db, "ZZZZZZ"), undefined);
});
