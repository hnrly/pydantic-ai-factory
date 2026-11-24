import { notFound } from "next/navigation";
import { getDb } from "@/db";
import { getFlight, seatMap } from "@/lib/flights";
import { formatDay, formatPrice, formatTime } from "@/lib/format";
import { currentUser } from "@/lib/session";
import { book } from "./actions";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string }>;
};

export default async function FlightPage({ params, searchParams }: Props) {
  const { id } = await params;
  const { error } = await searchParams;
  const db = getDb();
  const flight = getFlight(db, Number(id));
  if (!flight) {
    notFound();
  }
  const seats = seatMap(db, flight.id);
  const user = await currentUser();
  return (
    <>
      <h2>
        {flight.number}: {flight.origin} to {flight.destination}
      </h2>
      <p>
        {formatDay(flight.departsAt)}, departs {formatTime(flight.departsAt)}, arrives{" "}
        {formatTime(flight.arrivesAt)}. {formatPrice(flight.pricePence)} a seat.
      </p>
      {error && <p className="error">{error}</p>}
      <form action={book.bind(null, flight.id)}>
        <fieldset>
          <legend>Choose a seat</legend>
          <div className="seats">
            {seats.map((seat) => (
              <label key={seat.id}>
                <input type="radio" name="seatId" value={seat.id} disabled={seat.taken} required />
                {seat.label}
              </label>
            ))}
          </div>
        </fieldset>
        <label>
          Passenger name
          <input name="passengerName" required defaultValue={user?.name} />
        </label>
        <label>
          Email
          <input name="passengerEmail" type="email" required defaultValue={user?.email} />
        </label>
        <button type="submit">Book this seat</button>
      </form>
    </>
  );
}
