import assert from "node:assert/strict";
import { test } from "node:test";
import { openDatabase } from "@/db";
import { seed } from "@/db/seed";
import { searchFlights } from "@/lib/flights";
import { oneFlight } from "./helpers";

test("a search finds the flights on that route and day", () => {
  const { db } = oneFlight();

  assert.deepEqual(
    searchFlights(db, { origin: "lhr", destination: "edi", date: "2030-06-01" }).map((f) => f.number),
    ["FB900"],
  );
});

test("a search on another day or route finds nothing", () => {
  const { db } = oneFlight();

  assert.equal(searchFlights(db, { origin: "LHR", destination: "EDI", date: "2030-06-02" }).length, 0);
  assert.equal(searchFlights(db, { origin: "EDI", destination: "LHR", date: "2030-06-01" }).length, 0);
  assert.equal(searchFlights(db, { origin: "LHR", destination: "EDI", date: "not a date" }).length, 0);
});

test("the seed data gives every route a flight on each of the next seven days", () => {
  const db = openDatabase(":memory:", { seed: false });
  seed(db, new Date("2030-01-01T12:00:00.000Z"));

  for (const date of ["2030-01-02", "2030-01-08"]) {
    assert.equal(searchFlights(db, { origin: "LHR", destination: "EDI", date }).length, 1);
  }
  assert.equal(searchFlights(db, { origin: "LHR", destination: "EDI", date: "2030-01-01" }).length, 0);
  assert.equal(searchFlights(db, { origin: "LHR", destination: "EDI", date: "2030-01-09" }).length, 0);
});

test("seeding twice adds nothing the second time", () => {
  const db = openDatabase(":memory:", { seed: false });
  seed(db, new Date("2030-01-01T12:00:00.000Z"));
  seed(db, new Date("2030-01-05T12:00:00.000Z"));

  assert.equal(searchFlights(db, { origin: "LHR", destination: "EDI", date: "2030-01-09" }).length, 0);
});
