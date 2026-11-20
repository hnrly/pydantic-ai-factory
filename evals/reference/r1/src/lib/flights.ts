import { and, asc, eq, gte, lt } from "drizzle-orm";
import type { Db } from "@/db";
import { bookings, type Flight, flights, seats } from "@/db/schema";

export type Search = {
  origin: string;
  destination: string;
  /** A day as YYYY-MM-DD, in UTC. */
  date: string;
};

export type SeatView = { id: number; label: string; taken: boolean };

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Flights on one route that depart on one day, earliest first. */
export function searchFlights(db: Db, search: Search): Flight[] {
  if (!DAY.test(search.date)) {
    return [];
  }
  const start = new Date(`${search.date}T00:00:00.000Z`);
  const end = new Date(start.getTime() + 86_400_000);
  return db
    .select()
    .from(flights)
    .where(
      and(
        eq(flights.origin, search.origin.trim().toUpperCase()),
        eq(flights.destination, search.destination.trim().toUpperCase()),
        gte(flights.departsAt, start.toISOString()),
        lt(flights.departsAt, end.toISOString()),
      ),
    )
    .orderBy(asc(flights.departsAt))
    .all();
}

export function getFlight(db: Db, id: number): Flight | undefined {
  return db.select().from(flights).where(eq(flights.id, id)).get();
}

/** Every seat on a flight, in seat order, and whether it is already booked. */
export function seatMap(db: Db, flightId: number): SeatView[] {
  return db
    .select({ id: seats.id, label: seats.label, bookingId: bookings.id })
    .from(seats)
    .leftJoin(bookings, eq(bookings.seatId, seats.id))
    .where(eq(seats.flightId, flightId))
    .orderBy(asc(seats.id))
    .all()
    .map((row) => ({ id: row.id, label: row.label, taken: row.bookingId !== null }));
}

/** The airports flights leave from, for the search form. */
export function airports(db: Db): string[] {
  return db
    .selectDistinct({ code: flights.origin })
    .from(flights)
    .orderBy(asc(flights.origin))
    .all()
    .map((row) => row.code);
}
