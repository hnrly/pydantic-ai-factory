"use server";

import { redirect } from "next/navigation";
import { getDb } from "@/db";
import { bookSeat, InvalidBookingError, SeatTakenError } from "@/lib/bookings";

/** Book the chosen seat, then show the booking; on a refusal, show the flight again with why. */
export async function book(flightId: number, form: FormData): Promise<void> {
  let reference: string;
  try {
    const booking = bookSeat(getDb(), {
      flightId,
      seatId: Number(form.get("seatId")),
      passengerName: String(form.get("passengerName") ?? ""),
      passengerEmail: String(form.get("passengerEmail") ?? ""),
    });
    reference = booking.reference;
  } catch (error) {
    if (error instanceof SeatTakenError || error instanceof InvalidBookingError) {
      redirect(`/flights/${flightId}?error=${encodeURIComponent(error.message)}`);
    }
    throw error;
  }
  redirect(`/bookings/${reference}`);
}
