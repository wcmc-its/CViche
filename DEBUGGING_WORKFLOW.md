# CViche Debugging & Iteration Workflow

This guide explains how to efficiently debug and improve CV processing output without expensive full pipeline runs.

---

## Starting the Application

### Quick Start (Backend + Frontend)

```bash
cd /Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/LLM\ Projects/CViche/web_interface
./start.sh
```

This starts the backend on http://localhost:8000. Then in a separate terminal:

```bash
cd /Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/LLM\ Projects/CViche/web_interface/frontend
npm run dev
```

This starts the frontend on http://localhost:3000.

### Access Points
- **Web UI**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs

---

## ⚠️ IMPORTANT: Restart Backend After Code Changes

Python caches modules in memory. If you modify any pipeline code (especially `stage_6_word_template.py`), you **must restart the servers** for changes to take effect.

### Easy Restart (Double-Click)

**Double-click `restart.command`** in Finder:
```
web_interface/restart.command
```

This kills any existing servers and restarts both backend and frontend.

### Manual Restart

```bash
# Stop the servers (Ctrl+C in the terminal running ./start.sh)
# Then restart:
cd /Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/LLM\ Projects/CViche/web_interface
./start.sh
# And in a separate terminal:
cd web_interface/frontend && npm run dev
```

### Command-Line Testing (Fastest for Stage 6)

Test your changes by running Stage 6 directly (always uses the latest code, no server restart needed):

```bash
python3 -c "
from src.unified_pipeline.stage_6_word_template import run_stage6
run_stage6('src/unified_pipeline/outputs/stage_4_field_extraction/YOUR_UID_fields.json', verbose=True)
"
```

---

## Key Principle: Stage Isolation

The CViche pipeline saves intermediate JSON outputs at each stage. This means you can:
1. **Inspect** where data went wrong
2. **Re-run** only the affected stage
3. **Iterate** on fixes without re-running expensive LLM stages

### Cost by Stage

| Stage | Type | Cost to Re-run | Iteration Speed |
|-------|------|----------------|-----------------|
| 1a Segmentation | LLM | ~$0.05-0.10 | Slow |
| 1b Hierarchy Mapping | Deterministic | Free | Fast |
| 2 Entry Extraction | LLM | ~$0.05-0.10 | Slow |
| 3a Header Taxonomy | LLM | ~$0.02-0.05 | Medium |
| 3b Entry Classification | LLM | ~$0.05-0.10 | Slow |
| 4 Field Extraction | LLM | ~$0.02-0.05 | Medium |
| 4.5 Research Summary | LLM | ~$0.01-0.02 | Fast |
| 5 PubMed Enrichment | API | Free | Fast |
| 5b Institution Enrichment | API | Free | Fast |
| 5c Teaching Formatter | LLM | ~$0.01 | Fast |
| 5d Citation Formatter | LLM | ~$0.01 | Fast |
| **6 Word Template** | **Deterministic** | **Free** | **Instant** |

**Stage 6 is your best friend** - you can iterate on output formatting unlimited times for free.

---

## Step 1: Identify the Problem Stage

When you see incorrect output, first determine WHERE the data went wrong.

### Find the CV's intermediate outputs

```bash
# List all outputs for a specific CV
ls -la src/unified_pipeline/outputs/*/NG3YXA_*
```

### Inspect Stage 4 (Field Extraction) output

This is usually your starting point - it shows what the LLM extracted:

```bash
python3 -c "
import json
with open('src/unified_pipeline/outputs/stage_4_field_extraction/YOUR_UID_fields.json') as f:
    data = json.load(f)

# Look at specific taxonomy codes
codes_of_interest = ['C', 'D1', 'F1', 'F2', 'K2', 'K3']  # Adjust as needed
for entry in data.get('entries', []):
    if entry.get('taxonomy_code') in codes_of_interest:
        print(f\"Code: {entry['taxonomy_code']}\")
        print(f\"Text: {entry.get('text', '')[:100]}...\")
        print(f\"Fields: {json.dumps(entry.get('extracted_fields', {}), indent=2)}\")
        print()
"
```

### Decision Tree

| If the JSON shows... | Problem is in... | Fix approach |
|---------------------|------------------|--------------|
| Correct data, bad Word output | Stage 6 | Fix template code (free) |
| Wrong/missing extracted fields | Stage 4 | Fix schema or prompts (~$0.02-0.05) |
| Wrong taxonomy classification | Stage 3b | Fix classification logic (~$0.05-0.10) |
| Multiple items merged into one entry | Stage 2 | Fix entry extraction (~$0.05-0.10) |

---

## Step 2: Fix Stage 6 Issues (Free Iteration)

Most output formatting issues can be fixed in Stage 6 without touching LLM stages.

### Common Stage 6 Fix Patterns

#### Pattern A: Cleaning dirty extracted data
```python
# Tab-separated values → proper formatting
if '\t' in institution:
    parts = [p.strip() for p in institution.split('\t') if p.strip()]
    institution = ' / '.join(parts)
```

#### Pattern B: Splitting merged entries
```python
# Multiple items on separate lines → individual bullets
lines = original_text.split('\n')
for line in lines:
    if line.strip():
        self._insert_bulleted_entry(insert_idx, line.strip())
```

#### Pattern C: Detecting and routing special values
```python
# Detect NPI/DEA patterns and route to correct fields
if 'NPI' in original_text.upper() or re.match(r'^\d{10,11}$', license_number):
    npi_number = license_number
elif 'DEA' in original_text.upper() or re.match(r'^[A-Za-z]{2}[A-Za-z0-9]{7}$', license_number):
    dea_number = license_number
```

### Re-run Stage 6 only

```bash
python3 -c "
from src.unified_pipeline.stage_6_word_template import run_stage6
output = run_stage6('src/unified_pipeline/outputs/stage_4_field_extraction/YOUR_UID_fields.json', verbose=True)
print(f'Generated: {output}')
"
```

### Verify the fix

```bash
python3 -c "
from docx import Document
doc = Document('src/unified_pipeline/outputs/stage_6_wcm_documents/YOUR_UID_wcm.docx')

# Check specific section
for table in doc.tables:
    # Inspect table contents
    for row in table.rows[:3]:
        print([c.text.strip()[:30] for c in row.cells])
"
```

---

## Step 3: Fix Stage 4 Issues (Cheap Iteration)

If the extracted fields themselves are wrong, you need to modify Stage 4.

### Option A: Fix the field schema

Edit `src/unified_pipeline/config/field_schemas_v1.1.json`:

```json
{
  "F1": {
    "fields": {
      "state_country": {"extract": true, "category": "template"},
      "license_number": {"extract": true, "category": "template"},
      "npi_number": {"extract": true, "category": "template"},  // Add new field
      "dea_number": {"extract": true, "category": "template"}   // Add new field
    }
  }
}
```

### Option B: Use a cheaper model for testing

```bash
python3 -c "
from src.unified_pipeline.stage_4_field_extractor import process_cv
result = process_cv('YOUR_UID.docx', model='gpt-4o-mini')  # 10-50x cheaper than gpt-4
"
```

### Re-run Stage 4 → Stage 6

```bash
# Re-run Stage 4
python3 run_full_pipeline.py YOUR_UID --stage 4

# Then re-run Stage 6 (free)
python3 -c "
from src.unified_pipeline.stage_6_word_template import run_stage6
run_stage6('src/unified_pipeline/outputs/stage_4_field_extraction/YOUR_UID_fields.json')
"
```

---

## Step 4: Fix Earlier Stage Issues (More Expensive)

For Stage 2 (entry extraction) or Stage 3 (classification) issues, the approach is similar but more costly.

### When to fix earlier stages

- **Stage 2**: Multiple CV items grouped into one entry (e.g., all teaching activities as one block)
- **Stage 3a/3b**: Items classified with wrong taxonomy codes

### Cost-saving tip

Use existing cached outputs as test fixtures:
```bash
# Copy a known-good Stage 3b output to test Stage 4 changes
cp src/unified_pipeline/outputs/stage_3b_classified_entries/GOOD_CV_classified.json test_fixtures/
```

---

## Quick Reference: Common Issues & Fixes

| Symptom | Root Cause | Fix Location | Cost |
|---------|------------|--------------|------|
| Items semicolon-concatenated instead of bulleted | Stage 6 not splitting | `_fill_teaching()` in stage_6 | Free |
| Tab characters in output | Dirty extraction, not cleaned | Add `.replace('\t', ', ')` in stage_6 | Free |
| City/state in wrong column | Merged institution field | `_clean_institution_field()` in stage_6 | Free |
| DEA/NPI in licensure table | Not detected as special | Add pattern detection in stage_6 | Free |
| Multiple certs shown as one | Merged extraction | Split logic in stage_6 | Free |
| Wrong fields extracted | Schema missing fields | Edit field_schemas_v1.1.json | ~$0.02-0.05 |
| Wrong taxonomy code | Classification error | Stage 3b prompts | ~$0.05-0.10 |
| Multiple items in one entry | Entry boundaries wrong | Stage 2 logic | ~$0.05-0.10 |

---

## Debugging Commands Cheat Sheet

```bash
# List recent Stage 4 outputs
ls -lt src/unified_pipeline/outputs/stage_4_field_extraction/ | head -10

# Inspect specific taxonomy codes in a CV
python3 -c "
import json
with open('src/unified_pipeline/outputs/stage_4_field_extraction/YOUR_UID_fields.json') as f:
    data = json.load(f)
for e in data['entries']:
    if e.get('taxonomy_code') in ['F1', 'F2']:
        print(e['taxonomy_code'], e.get('extracted_fields', {}))
"

# Re-run Stage 6 only (free)
python3 -c "
from src.unified_pipeline.stage_6_word_template import run_stage6
run_stage6('src/unified_pipeline/outputs/stage_4_field_extraction/YOUR_UID_fields.json', verbose=True)
"

# Check Word output structure
python3 -c "
from docx import Document
doc = Document('src/unified_pipeline/outputs/stage_6_wcm_documents/YOUR_UID_wcm.docx')
for i, table in enumerate(doc.tables[:15]):
    header = ' | '.join([c.text.strip()[:20] for c in table.rows[0].cells])
    print(f'Table {i}: {header}')
"

# Check specific section content
python3 -c "
from docx import Document
doc = Document('src/unified_pipeline/outputs/stage_6_wcm_documents/YOUR_UID_wcm.docx')
for para in doc.paragraphs:
    if 'YOUR_SECTION_KEYWORD' in para.text:
        print(para.text)
"
```

---

## Summary: The Cost-Effective Workflow

1. **Run full pipeline once** on a CV (~$0.20)
2. **Identify issues** in the Word output
3. **Inspect Stage 4 JSON** to determine if data is correct
4. **If data correct but output wrong** → Fix Stage 6 (free, unlimited iterations)
5. **If data wrong** → Fix Stage 4 schema, test with `gpt-4o-mini` (~$0.002-0.005)
6. **Once fix works** → Run on full model for production

**Goal**: Do 90% of debugging in Stage 6 (free) before touching expensive LLM stages.
