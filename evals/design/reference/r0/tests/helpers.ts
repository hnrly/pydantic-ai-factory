import { type Db, openDatabase } from "@/db";
import { flights, seats } from "@/db/schema";

/** A fresh database in memory with one flight and two seats, and nothing booked. */
export function oneFlight(): { db: Db; flightId: number; seatIds: [number, number] } {
  const db = openDatabase(":memory:", { seed: false });
  const flight = db
    .insert(flights)
    .values({
      number: "FB900",
      origin: "LHR",
      destination: "EDI",
      departsAt: "2030-06-01T09:00:00.000Z",
      arrivesAt: "2030-06-01T10:30:00.000Z",
      pricePence: 5000,
    })
    .returning()
    .get();
  const [first, second] = db
    .insert(seats)
    .values([
      { flightId: flight.id, label: "1A" },
      { flightId: flight.id, label: "1B" },
    ])
    .returning()
    .all();
  return { db, flightId: flight.id, seatIds: [first.id, second.id] };
}
