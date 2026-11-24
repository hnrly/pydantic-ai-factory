// Delete the database and start again from the seed data: `npm run reset`.
import fs from "node:fs";
import { databasePath, openDatabase } from "../src/db";

const file = databasePath();
for (const suffix of ["", "-wal", "-shm"]) {
  fs.rmSync(file + suffix, { force: true });
}
openDatabase(file);
console.log(`Reset ${file} to the seed data.`);
