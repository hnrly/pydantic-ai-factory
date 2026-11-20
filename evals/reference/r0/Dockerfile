FROM node:22-bookworm-slim

WORKDIR /app

# better-sqlite3 compiles from source when no prebuilt binary fits the platform.
RUN apt-get update \
  && apt-get install -y --no-install-recommends python3 make g++ \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ENV PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000

# The one start command: it builds the app, then serves it.
CMD ["npm", "start"]
