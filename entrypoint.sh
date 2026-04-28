#!/bin/sh
set -e

# Apply the current Prisma schema to the SQLite DB (idempotent — safe to run on every start).
# 'db push' only adds/alters tables; it never drops columns that still exist in the schema,
# so existing data on the persistent volume is preserved.
echo "[entrypoint] Running prisma db push..."
cd /app
node /app/node_modules/prisma/build/index.js db push --skip-generate
echo "[entrypoint] Database ready."

# Start the Next.js server in the foreground
echo "[entrypoint] Starting Next.js server..."
exec node server.js
