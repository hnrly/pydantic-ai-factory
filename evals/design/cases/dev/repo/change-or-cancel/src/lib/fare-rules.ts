/**
 * The fare rules, as the revenue team set them. Nothing uses them yet.
 *
 * - A booking can move to another flight on the same route for a change fee of
 *   £37.40, plus any difference in fare.
 * - No booking can be changed or cancelled within 19 hours of departure.
 * - A cancelled booking is refunded in full.
 */

/** The fee for moving a booking to another flight, in whole pence. */
export const CHANGE_FEE_PENCE = 3740;

/** How close to departure a booking may still be changed or cancelled, in hours. */
export const CHANGE_CUT_OFF_HOURS = 19;
