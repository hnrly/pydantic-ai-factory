import Link from "next/link";
import { getDb } from "@/db";
import { findBooking } from "@/lib/bookings";
import { formatDay, formatPrice, formatTime } from "@/lib/format";

type Props = { params: Promise<{ reference: string }> };

export default async function BookingPage({ params }: Props) {
  const { reference } = await params;
  const booking = findBooking(getDb(), decodeURIComponent(reference));
  if (!booking) {
    return (
      <>
        <h2>No booking found</h2>
        <p>
          No booking has the reference {reference}. <Link href="/">Try again</Link>.
        </p>
      </>
    );
  }
  const { flight } = booking;
  return (
    <>
      <h2>Booking {booking.reference}</h2>
      <dl>
        <dt>Passenger</dt>
        <dd>
          {booking.passengerName} ({booking.passengerEmail})
        </dd>
        <dt>Flight</dt>
        <dd>
          {flight.number}, {flight.origin} to {flight.destination}
        </dd>
        <dt>Departs</dt>
        <dd>
          {formatDay(flight.departsAt)} {formatTime(flight.departsAt)}
        </dd>
        <dt>Seat</dt>
        <dd>{booking.seatLabel}</dd>
        <dt>Price</dt>
        <dd>{formatPrice(flight.pricePence)}</dd>
      </dl>
    </>
  );
}
