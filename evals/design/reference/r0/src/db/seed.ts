import { count } from "drizzle-orm";
import type { Db } from "./index";
import { flights, seats } from "./schema";

type Route = {
  number: string;
  origin: string;
  destination: string;
  departs: string;
  minutes: number;
  pricePence: number;
};

const ROUTES: Route[] = [
  { number: "FB101", origin: "LHR", destination: "EDI", departs: "07:30", minutes: 85, pricePence: 6900 },
  { number: "FB102", origin: "EDI", destination: "LHR", departs: "10:15", minutes: 90, pricePence: 6900 },
  { number: "FB201", origin: "MAN", destination: "AMS", departs: "08:45", minutes: 75, pricePence: 8400 },
  { number: "FB202", origin: "AMS", destination: "MAN", departs: "12:05", minutes: 80, pricePence: 8400 },
  { number: "FB301", origin: "LGW", destination: "BCN", departs: "06:50", minutes: 130, pricePence: 11200 },
  { number: "FB302", origin: "BCN", destination: "LGW", departs: "11:20", minutes: 135, pricePence: 11200 },
  { number: "FB401", origin: "LHR", destination: "CDG", departs: "17:40", minutes: 70, pricePence: 9500 },
  { number: "FB402", origin: "CDG", destination: "LHR", departs: "20:10", minutes: 70, pricePence: 9500 },
];

const DAYS = 7;
const ROWS = 10;
const LETTERS = ["A", "B", "C", "D"];

/** Add a week of flights, starting tomorrow, each with every seat free. Does nothing if flights exist. */
export function seed(db: Db, today: Date = new Date()): void {
  const [{ value: existing }] = db.select({ value: count() }).from(flights).all();
  if (existing > 0) {
    return;
  }
  const midnight = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  db.transaction((tx) => {
    for (let day = 1; day <= DAYS; day += 1) {
      for (const route of ROUTES) {
        const [hours, minutes] = route.departs.split(":").map(Number);
        const departs = new Date(midnight + day * 86_400_000 + (hours * 60 + minutes) * 60_000);
        const arrives = new Date(departs.getTime() + route.minutes * 60_000);
        const [flight] = tx
          .insert(flights)
          .values({
            number: route.number,
            origin: route.origin,
            destination: route.destination,
            departsAt: departs.toISOString(),
            arrivesAt: arrives.toISOString(),
            pricePence: route.pricePence,
          })
          .returning()
          .all();
        const rows = [];
        for (let row = 1; row <= ROWS; row += 1) {
          for (const letter of LETTERS) {
            rows.push({ flightId: flight.id, label: `${row}${letter}` });
          }
        }
        tx.insert(seats).values(rows).run();
      }
    }
  });
}
