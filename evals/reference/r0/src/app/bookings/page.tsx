import { redirect } from "next/navigation";

type Props = { searchParams: Promise<{ reference?: string }> };

/** The "view a booking" form sends its reference here. */
export default async function FindBooking({ searchParams }: Props) {
  const { reference = "" } = await searchParams;
  redirect(`/bookings/${encodeURIComponent(reference.trim().toUpperCase())}`);
}
