# Initialize the SQLite database
echo "Initializing Database..."
cd /app
npx prisma db push --accept-data-loss

# Start the Python agent in the background
echo "Starting Resume Agent..."
cd /app/agent && uv run main.py &

# Start the Next.js server in the foreground
echo "Starting Next.js Server..."
cd /app
node server.js
