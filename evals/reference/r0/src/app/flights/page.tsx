import Link from "next/link";
import { getDb } from "@/db";
import { searchFlights } from "@/lib/flights";
import { formatDay, formatPrice, formatTime } from "@/lib/format";

type Props = {
  searchParams: Promise<{ origin?: string; destination?: string; date?: string }>;
};

export default async function FlightsPage({ searchParams }: Props) {
  const { origin = "", destination = "", date = "" } = await searchParams;
  const results = searchFlights(getDb(), { origin, destination, date });
  return (
    <>
      <h2>
        Flights from {origin.toUpperCase()} to {destination.toUpperCase()} on {date}
      </h2>
      {results.length === 0 ? (
        <p>
          No flights match. <Link href="/">Search again</Link>.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Flight</th>
              <th>Departs</th>
              <th>Arrives</th>
              <th>Price</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {results.map((flight) => (
              <tr key={flight.id}>
                <td>{flight.number}</td>
                <td>
                  {formatDay(flight.departsAt)} {formatTime(flight.departsAt)}
                </td>
                <td>{formatTime(flight.arrivesAt)}</td>
                <td>{formatPrice(flight.pricePence)}</td>
                <td>
                  <Link href={`/flights/${flight.id}`}>Choose a seat</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
