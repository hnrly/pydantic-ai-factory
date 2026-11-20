import { getDb } from "@/db";
import { airports } from "@/lib/flights";

export const dynamic = "force-dynamic";

export default function Home() {
  const codes = airports(getDb());
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
  return (
    <>
      <h2>Search flights</h2>
      <form action="/flights">
        <label>
          From
          <select name="origin" required>
            {codes.map((code) => (
              <option key={code}>{code}</option>
            ))}
          </select>
        </label>
        <label>
          To
          <input name="destination" required maxLength={3} placeholder="EDI" />
        </label>
        <label>
          Date
          <input name="date" type="date" required defaultValue={tomorrow} />
        </label>
        <button type="submit">Search</button>
      </form>

      <h2>View a booking</h2>
      <form action="/bookings">
        <label>
          Booking reference
          <input name="reference" required maxLength={6} />
        </label>
        <button type="submit">View</button>
      </form>
    </>
  );
}
