import { randomInt } from "node:crypto";
import { and, desc, eq } from "drizzle-orm";
import type { Db } from "@/db";
import { type Booking, bookings, type Flight, flights, seats } from "@/db/schema";

/** The seat was booked by someone else first. */
export class SeatTakenError extends Error {
  constructor() {
    super("That seat has just been booked. Please choose another.");
    this.name = "SeatTakenError";
  }
}

/** The request cannot be a booking, such as a missing name or a seat on another flight. */
export class InvalidBookingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidBookingError";
  }
}

export type NewBooking = {
  flightId: number;
  seatId: number;
  passengerName: string;
  passengerEmail: string;
  /** The signed-in account making the booking, if any. */
  userId?: number;
};

export type BookingDetails = Booking & { flight: Flight; seatLabel: string };

// No 0, O, 1 or I, so a reference read aloud is not misheard.
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";

function newReference(): string {
  let reference = "";
  for (let i = 0; i < 6; i += 1) {
    reference += ALPHABET[randomInt(ALPHABET.length)];
  }
  return reference;
}

function isUniqueViolation(error: unknown, column: string): boolean {
  return (
    error instanceof Error &&
    "code" in error &&
    error.code === "SQLITE_CONSTRAINT_UNIQUE" &&
    error.message.includes(column)
  );
}

/** Book one seat for one passenger. A seat is never sold twice. */
export function bookSeat(db: Db, request: NewBooking): Booking {
  const passengerName = request.passengerName.trim();
  const passengerEmail = request.passengerEmail.trim().toLowerCase();
  if (!passengerName) {
    throw new InvalidBookingError("Please give the passenger's name.");
  }
  if (!/^[^\s@]+@[^\s@]+$/.test(passengerEmail)) {
    throw new InvalidBookingError("Please give an email address we can send the booking to.");
  }
  const seat = db
    .select()
    .from(seats)
    .where(and(eq(seats.id, request.seatId), eq(seats.flightId, request.flightId)))
    .get();
  if (!seat) {
    throw new InvalidBookingError("That seat is not on this flight.");
  }
  for (;;) {
    try {
      return db
        .insert(bookings)
        .values({
          reference: newReference(),
          flightId: request.flightId,
          seatId: seat.id,
          userId: request.userId ?? null,
          passengerName,
          passengerEmail,
          createdAt: new Date().toISOString(),
        })
        .returning()
        .get();
    } catch (error) {
      if (isUniqueViolation(error, "bookings.seat_id")) {
        throw new SeatTakenError();
      }
      if (!isUniqueViolation(error, "bookings.reference")) {
        throw error;
      }
      // Two bookings drew the same reference; draw again.
    }
  }
}

/** A booking with its flight and seat, found by its reference in any case. */
export function findBooking(db: Db, reference: string): BookingDetails | undefined {
  const row = db
    .select({ booking: bookings, flight: flights, seatLabel: seats.label })
    .from(bookings)
    .innerJoin(flights, eq(flights.id, bookings.flightId))
    .innerJoin(seats, eq(seats.id, bookings.seatId))
    .where(eq(bookings.reference, reference.trim().toUpperCase()))
    .get();
  return row && { ...row.booking, flight: row.flight, seatLabel: row.seatLabel };
}

/** The bookings an account made, newest first. Nobody sees another account's bookings here. */
export function myBookings(db: Db, userId: number): BookingDetails[] {
  return db
    .select({ booking: bookings, flight: flights, seatLabel: seats.label })
    .from(bookings)
    .innerJoin(flights, eq(flights.id, bookings.flightId))
    .innerJoin(seats, eq(seats.id, bookings.seatId))
    .where(eq(bookings.userId, userId))
    .orderBy(desc(bookings.createdAt), desc(bookings.id))
    .all()
    .map((row) => ({ ...row.booking, flight: row.flight, seatLabel: row.seatLabel }));
}
