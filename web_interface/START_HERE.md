# 🎉 CViche Pipeline Viewer - Start Here!

## ✅ Integration Complete!

The CViche pipeline is now integrated into the web interface! The pipeline uses **REAL CV processing** for Steps 1-4.

---

## 🚀 Quick Start (3 Steps)

### 1. Start Backend (Terminal 1)
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

### 2. Start Frontend (Terminal 2)
```bash
cd frontend
npm run dev
```

### 3. Upload a CV
- Open http://localhost:3000
- Upload `../data/sample_cvs/word/2025_Denckla_Cv.docx`
- Watch the **REAL** pipeline process it!

---

## ✨ What Works NOW

### ✅ Real Processing (Steps 1 & 4)
- **Step 1**: Real CV segmentation using `cv_segmenter.py`
  - Detects sections automatically
  - Extracts entries hierarchically
  - Works with both .docx and .pdf
  - **Cost**: ~$0.15 per .docx CV

- **Step 4**: Real taxonomy mapping using `taxonomy_mapper.py`
  - Maps to WCM taxonomy
  - Uses GPT-4o-mini with Structured Outputs
  - Provides confidence scores
  - **Cost**: ~$0.05 per CV

### ⚠️ Placeholders (Steps 5-9)
- Steps 2-3: Included in Step 1 (no extra cost)
- Steps 5-9: Placeholder logic for now
- **Total current cost**: ~$0.20 per CV

---

## 📊 What You'll See

### Upload Page
```
┌─────────────────────────────────────┐
│         CViche                      │
│   CV Pipeline Viewer                │
│                                     │
│  ┌─────────────────────────────┐  │
│  │   📄 Click to upload         │  │
│  │   .docx or .pdf only         │  │
│  └─────────────────────────────┘  │
│                                     │
│  [      Start Pipeline      ]       │
└─────────────────────────────────────┘
```

### Pipeline Viewer
```
┌──────────────────────────────────────────────────────────┐
│  ← Back   Run #ABC123 • 2025_Denckla_Cv.docx            │
│  Cost: $0.20 • Tokens: 1000 • running                    │
├─────────────┬────────────────────────────────────────────┤
│ Sidebar     │  Step 1: Identify Sections                 │
│             │  ────────────────────────────────────────── │
│ ✅ Step 1   │  Status: complete • Duration: 15s • $0.15  │
│ ✅ Step 2   │                                             │
│ ✅ Step 3   │  Logs:                                      │
│ ⚙️  Step 4   │  ┌──────────────────────────────────────┐ │
│ ⏳ Step 5   │  │ Initializing CV segmenter...        │ │
│ ⏳ Step 6   │  │ Processing 2025_Denckla_Cv.docx...  │ │
│ ⏳ Step 7   │  │ Found 15 sections                   │ │
│ ⏳ Step 8   │  │ Extracted 237 entries               │ │
│ ⏳ Step 9   │  │ Approach: word-chunked-hierarchical │ │
│             │  └──────────────────────────────────────┘ │
│             │                                             │
│             │  Output Files:                              │
│             │  📄 CV_ABC123_segmented.json                │
└─────────────┴────────────────────────────────────────────┘
```

---

## 📁 Key Files

### 📖 Documentation
- **START_HERE.md** ← You are here!
- **INTEGRATION_COMPLETE.md** - Full integration details
- **QUICK_START.md** - 3-minute setup guide
- **README.md** - Complete documentation

### 🔧 Code
- **backend/app/pipeline/orchestrator.py** - Main orchestrator (INTEGRATED!)
- **backend/app/main.py** - FastAPI server
- **frontend/src/App.tsx** - React app

### 📦 Outputs
- **outputs/{run_id}/CV_{run_id}_segmented.json** - Real segmentation output
- **outputs/{run_id}/CV_{run_id}_classified.json** - Real taxonomy mapping
- **outputs/{run_id}/section_*.json** - Placeholder for Phase 2

---

## 🎯 Integration Status

| Step | Name | Status | Module |
|------|------|--------|--------|
| 1 | Identify Sections | ✅ **REAL** | `cv_segmenter.py` |
| 2 | Preserve Formatting | ✅ **INCLUDED** | (in Step 1) |
| 3 | Break Into Items | ✅ **INCLUDED** | (in Step 1) |
| 4 | Categorize Entries | ✅ **REAL** | `taxonomy_mapper.py` |
| 5 | Fix Unknowns | ⚠️ Placeholder | Phase 2 |
| 6 | Extract Data | ⚠️ Placeholder | Phase 2 |
| 7 | AI Assist | ⚠️ Placeholder | Phase 2 |
| 8 | Org Data | ⚠️ Optional | Phase 2 |
| 9 | Generate Template | ⚠️ Placeholder | Phase 2 |

---

## 🧪 Test It Now

### Test with Real CVs
```bash
# Start the servers (see Quick Start above)
# Then test with these CVs:
../data/sample_cvs/word/2025_Denckla_Cv.docx
../data/sample_cvs/word/2024_Dabelko_Schoeny.docx
../data/sample_cvs/word/2004_Almasri_Mahmoud.docx
```

### Check Real Outputs
```bash
# After running a CV
cd outputs/{run_id}
ls -lh

# View segmented output
cat CV_{run_id}_segmented.json | jq '.meta'

# View taxonomy mapping
cat CV_{run_id}_classified.json | jq '.stats'
```

### Monitor Costs
- Watch the top bar in the UI
- Real costs for Steps 1 & 4
- Placeholders for Steps 5-9

---

## 💡 What's Next?

### Phase 2: Complete Integration
1. **Section parsers** (Step 6)
   - Publications, Education, Positions, Grants
   - Estimated time: 2-3 hours

2. **LLM fallback** (Step 7)
   - Handle unparsed items
   - Estimated time: 2 hours

3. **Template filler** (Step 9)
   - Generate final .docx
   - Estimated time: 1-2 hours

4. **UI enhancements**
   - Data table viewers
   - JSON viewer toggle
   - Settings panel

**Total Phase 2 estimate**: 1-2 weeks

---

## 🐛 Troubleshooting

### Backend won't start
```bash
cd backend
pip install -r requirements.txt
python3 -c "from app.main import app; print('OK')"
```

### Frontend won't start
```bash
cd frontend
npm install
npm run dev
```

### Can't import CViche modules
```bash
# Make sure you're in the correct directory
cd backend
python3 -c "from pathlib import Path; print(Path.cwd().parent.parent)"
# Should show: /Users/.../CViche
```

### OpenAI API key missing
```bash
echo $OPENAI_API_KEY_WORK
# If empty, set it:
export OPENAI_API_KEY_WORK="your-key-here"
```

---

## 📚 Learn More

- **INTEGRATION_COMPLETE.md** - Detailed integration report
- **backend/README.md** - API documentation
- **IMPLEMENTATION_SUMMARY.md** - Architecture details
- **QUICK_START.md** - Fast setup guide

---

## ✅ Success Checklist

- [ ] Backend starts on port 8000
- [ ] Frontend starts on port 3000
- [ ] Can upload a .docx CV
- [ ] Step 1 shows real section counts
- [ ] Step 4 shows real taxonomy mapping
- [ ] Output files exist in `outputs/{run_id}/`
- [ ] WebSocket streams real-time logs
- [ ] Costs reflect actual usage (~$0.20)

---

## 🎊 You're Ready!

CViche is now **web-enabled** with **real processing** for the first 4 steps!

Upload a CV and watch the magic happen! ✨

Questions? Check the docs or the integration report.

---

**Phase 1.5 Complete!** 🚀
