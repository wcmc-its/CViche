# CViche Pipeline Viewer - Quick Start Guide

## 🚀 Get Started in 3 Minutes

### Step 1: Start the Backend (Terminal 1)

```bash
cd "/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/LLM Projects/CViche/web_interface/backend"

# Start the server
uvicorn app.main:app --reload --port 8000
```

**You should see:**
```
🚀 Starting CViche Pipeline Viewer...
✅ Database initialized
INFO:     Uvicorn running on http://127.0.0.1:8000
```

### Step 2: Start the Frontend (Terminal 2)

```bash
cd "/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/LLM Projects/CViche/web_interface/frontend"

# Start the dev server
npm run dev
```

**You should see:**
```
➜  Local:   http://localhost:3000/
```

### Step 3: Upload a CV

1. **Open your browser:** http://localhost:3000
2. **Click** "Click to select a file"
3. **Choose a CV** from:
   ```
   ../data/sample_cvs/word/2025_Denckla_Cv.docx
   ```
4. **Click** "Start Pipeline"
5. **Watch the magic!** ✨

---

## 🎯 What You'll See

### Upload Page
- Clean, modern interface
- Drag-and-drop area
- File type validation
- Estimated cost and time

### Pipeline Viewer
- **Sidebar** (left): 9 steps with status icons
  - ⏳ Pending
  - ⚙️ Running
  - ✅ Complete
- **Top Bar**: Run ID, cost, tokens, status
- **Main Panel**: Step details, logs, outputs
- **Real-time updates**: Logs stream as they happen

---

## 📊 Demo the Features

### 1. Click Through Steps
- Click any step in the sidebar
- See logs for that step
- View output files

### 2. Watch Real-Time Updates
- Steps automatically progress
- Logs stream in live
- Cost accumulates
- Status icons update

### 3. Check Output Files
- Each completed step has output files
- Click to download JSON
- View structured data

### 4. Monitor Cost
- Top bar shows total cost
- Each step shows individual cost
- Token count displayed

---

## 🔍 Verify Everything Works

### Check Backend
```bash
# Open in browser
http://localhost:8000        # API info
http://localhost:8000/docs   # Interactive docs
http://localhost:8000/health # Health check
```

### Check Database
```bash
cd backend
sqlite3 cviche.db "SELECT * FROM runs;"
sqlite3 cviche.db "SELECT COUNT(*) FROM steps;"
```

### Check WebSocket
```javascript
// In browser console (http://localhost:3000)
const ws = new WebSocket('ws://localhost:8000/ws/run/YOUR_RUN_ID/stream')
ws.onmessage = (e) => console.log(JSON.parse(e.data))
```

---

## 🛑 Stop the Servers

**Terminal 1 (Backend):** Press `Ctrl+C`
**Terminal 2 (Frontend):** Press `Ctrl+C`

---

## 💡 Tips

- **Sample CVs** are in `../data/sample_cvs/word/`
- **120 Word docs** available for testing
- **260 PDFs** available (PDF support coming in Phase 2)
- Each run takes ~20 seconds with placeholder logic
- Real pipeline will take 1-2 minutes

---

## ❓ Troubleshooting

### Port Already in Use
```bash
# Kill process on port 8000
lsof -ti:8000 | xargs kill -9

# Kill process on port 3000
lsof -ti:3000 | xargs kill -9
```

### Frontend Won't Start
```bash
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run dev
```

### Backend Won't Start
```bash
cd backend
pip install -r requirements.txt
python3 -c "from app.main import app; print('✅ OK')"
```

### Database Issues
```bash
cd backend
rm cviche.db
# Restart backend - database will be recreated
```

---

## 📚 Next Steps

1. **Read** `README.md` for detailed documentation
2. **Review** `IMPLEMENTATION_SUMMARY.md` for architecture details
3. **Check** backend/README.md for API details
4. **Explore** the code in `backend/app/` and `frontend/src/`

---

## 🎉 You're All Set!

CViche is now running. Upload a CV and watch it transform through 9 automated steps with real-time progress tracking and cost monitoring.

**Phase 1 Complete!** 🚀

Next phase will integrate the actual CViche pipeline code to perform real CV processing.
