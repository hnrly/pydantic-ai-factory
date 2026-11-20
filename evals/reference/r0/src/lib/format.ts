const TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  hour: "2-digit",
  minute: "2-digit",
});
const DAY = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  weekday: "short",
  day: "numeric",
  month: "short",
});

export function formatPrice(pence: number): string {
  return `£${(pence / 100).toFixed(2)}`;
}

export function formatTime(iso: string): string {
  return TIME.format(new Date(iso));
}

export function formatDay(iso: string): string {
  return DAY.format(new Date(iso));
}
