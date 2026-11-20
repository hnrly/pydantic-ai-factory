import Link from "next/link";
import { redirect } from "next/navigation";
import { getDb } from "@/db";
import { myBookings } from "@/lib/bookings";
import { formatDay, formatTime } from "@/lib/format";
import { currentUser } from "@/lib/session";

export default async function MyBookingsPage() {
  const user = await currentUser();
  if (!user) {
    redirect("/login");
  }
  const bookings = myBookings(getDb(), user.id);
  return (
    <>
      <h2>My bookings</h2>
      {bookings.length === 0 ? (
        <p>
          You have no bookings yet. <Link href="/">Search flights</Link>.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Reference</th>
              <th>Flight</th>
              <th>Departs</th>
              <th>Seat</th>
              <th>Passenger</th>
            </tr>
          </thead>
          <tbody>
            {bookings.map((booking) => (
              <tr key={booking.id}>
                <td>
                  <Link href={`/bookings/${booking.reference}`}>{booking.reference}</Link>
                </td>
                <td>
                  {booking.flight.number}, {booking.flight.origin} to {booking.flight.destination}
                </td>
                <td>
                  {formatDay(booking.flight.departsAt)} {formatTime(booking.flight.departsAt)}
                </td>
                <td>{booking.seatLabel}</td>
                <td>{booking.passengerName}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
