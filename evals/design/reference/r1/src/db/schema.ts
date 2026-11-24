import { index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

/** A scheduled flight. Times are ISO 8601 strings in UTC; prices are whole pence. */
export const flights = sqliteTable(
  "flights",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    number: text("number").notNull(),
    origin: text("origin").notNull(),
    destination: text("destination").notNull(),
    departsAt: text("departs_at").notNull(),
    arrivesAt: text("arrives_at").notNull(),
    pricePence: integer("price_pence").notNull(),
  },
  (table) => [index("flights_route_idx").on(table.origin, table.destination, table.departsAt)],
);

/** One seat on one flight, such as "4C". */
export const seats = sqliteTable(
  "seats",
  {
    id: integer("id").primaryKey({ autoIncrement: true }),
    flightId: integer("flight_id")
      .notNull()
      .references(() => flights.id),
    label: text("label").notNull(),
  },
  (table) => [uniqueIndex("seats_flight_label_idx").on(table.flightId, table.label)],
);

/** A person with an account. Emails are stored in lower case, and are unique. */
export const users = sqliteTable("users", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  email: text("email").notNull().unique(),
  name: text("name").notNull(),
  /** "scrypt:<salt>:<hash>", both hex. The password itself is never stored. */
  passwordHash: text("password_hash").notNull(),
  createdAt: text("created_at").notNull(),
});

/** A signed-in browser. The id is the random token in the session cookie. */
export const sessions = sqliteTable(
  "sessions",
  {
    id: text("id").primaryKey(),
    userId: integer("user_id")
      .notNull()
      .references(() => users.id),
    expiresAt: text("expires_at").notNull(),
  },
  (table) => [index("sessions_user_idx").on(table.userId)],
);

/**
 * A booked seat. The unique seat id is the rule "a seat is never sold twice",
 * held by the database itself.
 */
export const bookings = sqliteTable("bookings", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  reference: text("reference").notNull().unique(),
  flightId: integer("flight_id")
    .notNull()
    .references(() => flights.id),
  seatId: integer("seat_id")
    .notNull()
    .unique()
    .references(() => seats.id),
  /** The account that made the booking; empty for a booking made while signed out. */
  userId: integer("user_id").references(() => users.id),
  passengerName: text("passenger_name").notNull(),
  passengerEmail: text("passenger_email").notNull(),
  createdAt: text("created_at").notNull(),
});

export type Flight = typeof flights.$inferSelect;
export type Seat = typeof seats.$inferSelect;
export type Booking = typeof bookings.$inferSelect;
export type User = typeof users.$inferSelect;
