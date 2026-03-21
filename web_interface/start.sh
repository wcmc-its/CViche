#!/bin/bash

echo "🚀 Starting CViche Pipeline Viewer..."
echo ""

# Start backend
echo "📡 Starting backend on http://localhost:8000..."
cd backend
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "Backend started with PID $BACKEND_PID"
echo ""
echo "✅ Backend running at: http://localhost:8000"
echo "📚 API docs available at: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all servers"
echo ""

# Wait for Ctrl+C
trap "echo ''; echo '👋 Shutting down...'; kill $BACKEND_PID 2>/dev/null; exit" INT TERM

# Keep script running
wait
