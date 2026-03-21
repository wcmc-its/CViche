#!/bin/bash
# =============================================================================
# CViche Server Restart Script
# Double-click this file in Finder to restart both backend and frontend servers
# =============================================================================

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================================="
echo "  CViche Server Restart"
echo "=============================================="
echo ""

# Kill existing processes on ports 8000 and 3000
echo "Stopping existing servers..."

if lsof -ti:8000 > /dev/null 2>&1; then
    echo "  Killing backend on port 8000..."
    lsof -ti:8000 | xargs kill -9 2>/dev/null
    sleep 1
else
    echo "  No backend running on port 8000"
fi

if lsof -ti:3000 > /dev/null 2>&1; then
    echo "  Killing frontend on port 3000..."
    lsof -ti:3000 | xargs kill -9 2>/dev/null
    sleep 1
else
    echo "  No frontend running on port 3000"
fi

echo ""
echo "Starting servers..."
echo ""

# Start backend
echo "Starting backend on http://localhost:8000..."
cd "$SCRIPT_DIR"
./start.sh &
BACKEND_PID=$!

# Wait for backend to initialize
sleep 3

# Start frontend
echo ""
echo "Starting frontend on http://localhost:3000..."
cd "$SCRIPT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "=============================================="
echo "  Servers Started!"
echo "=============================================="
echo ""
echo "  Backend:  http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo "  Frontend: http://localhost:3000"
echo ""
echo "  Press Ctrl+C to stop all servers"
echo "=============================================="
echo ""

# Wait for user to stop
wait
