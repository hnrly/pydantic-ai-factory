CREATE TABLE `bookings` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`reference` text NOT NULL,
	`flight_id` integer NOT NULL,
	`seat_id` integer NOT NULL,
	`passenger_name` text NOT NULL,
	`passenger_email` text NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`flight_id`) REFERENCES `flights`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`seat_id`) REFERENCES `seats`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `bookings_reference_unique` ON `bookings` (`reference`);--> statement-breakpoint
CREATE UNIQUE INDEX `bookings_seat_id_unique` ON `bookings` (`seat_id`);--> statement-breakpoint
CREATE TABLE `flights` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`number` text NOT NULL,
	`origin` text NOT NULL,
	`destination` text NOT NULL,
	`departs_at` text NOT NULL,
	`arrives_at` text NOT NULL,
	`price_pence` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `flights_route_idx` ON `flights` (`origin`,`destination`,`departs_at`);--> statement-breakpoint
CREATE TABLE `seats` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`flight_id` integer NOT NULL,
	`label` text NOT NULL,
	FOREIGN KEY (`flight_id`) REFERENCES `flights`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `seats_flight_label_idx` ON `seats` (`flight_id`,`label`);