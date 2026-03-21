# CViche - Technical Documentation

**Version**: 15.0
**Last Updated**: 2025-12-03
**Status**: Production

CViche is a multi-stage LLM pipeline for extracting structured data from faculty CVs and converting them to Weill Cornell Medicine (WCM) template format.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Pipeline Stages](#pipeline-stages)
4. [Taxonomy System](#taxonomy-system)
5. [Technical Requirements](#technical-requirements)
6. [Installation](#installation)
7. [Usage](#usage)
8. [Web Interface](#web-interface)
9. [Configuration](#configuration)
10. [Output Formats](#output-formats)
11. [External API Integrations](#external-api-integrations)
12. [Cost Management](#cost-management)
13. [Validators & Post-Processing](#validators--post-processing)
14. [Development](#development)

---

## Overview

### Purpose

This pipeline automates the conversion of faculty CVs (Word/PDF) into:
1. Structured JSON data with standardized taxonomy codes
2. WCM-formatted Word documents following institutional template standards

### Key Capabilities

- **Document Parsing**: Extracts hierarchical structure from Word (.docx) and PDF files
- **LLM-Powered Segmentation**: Uses GPT models to identify section boundaries and headers
- **Taxonomy Classification**: Maps CV sections to 60+ standardized WCM taxonomy codes
- **Field Extraction**: Extracts structured fields (authors, dates, institutions) per entry type
- **Data Enrichment**: PubMed lookup for publications, ROR API for institution locations
- **Template Generation**: Produces formatted WCM Word documents with Vancouver citations

### Processing Statistics

- **Cost**: ~$0.05-0.20 per CV (varies by length and model)
- **Speed**: 2-5 minutes per CV (full pipeline)
- **Accuracy**: Designed for faculty CVs with standard academic formatting
- **Scale**: Tested on 1,000+ CVs

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CVICHE PIPELINE                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  INPUT LAYER                                                                │
│  ├── Word Documents (.docx) ─── Preferred, best results                     │
│  └── PDF Documents (.pdf) ───── Vision-based, higher cost                   │
│                                                                             │
│  PROCESSING LAYER (Stages 1-4)                                              │
│  ├── Stage 1a: Document Segmentation (LLM)                                  │
│  ├── Stage 1b: Hierarchy Mapping (deterministic)                            │
│  ├── Stage 2: Entry Extraction (LLM)                                        │
│  ├── Stage 3a: Header Taxonomy Mapping (LLM)                                │
│  ├── Stage 3b: Entry Classification (LLM + validators)                      │
│  └── Stage 4: Field Extraction (LLM)                                        │
│                                                                             │
│  ENRICHMENT LAYER (Stages 5-5d)                                             │
│  ├── Stage 5: PubMed Enrichment (NCBI API)                                  │
│  ├── Stage 5b: Institution Enrichment (ROR API)                             │
│  ├── Stage 5c: Teaching Formatter (LLM)                                     │
│  └── Stage 5d: Citation Formatter (LLM)                                     │
│                                                                             │
│  OUTPUT LAYER (Stage 6)                                                     │
│  └── Stage 6: WCM Word Template Generation                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Access Methods

| Method | Description | Use Case |
|--------|-------------|----------|
| **CLI** | `run_full_pipeline.py` | Batch processing, automation |
| **Web App** | React + FastAPI | Interactive use, real-time feedback |
| **Python API** | Direct module import | Integration into other systems |

### Directory Structure

```
CViche/
├── run_full_pipeline.py              # Main CLI entry point
├── config.yaml                       # Legacy configuration
├── taxonomy_reference.md             # Complete taxonomy documentation
│
├── src/
│   └── unified_pipeline/             # Main pipeline code
│       ├── config.py                 # Centralized configuration
│       ├── stage_1b_hierarchy_mapper.py
│       ├── stage_2_entry_extraction.py
│       ├── stage_3a_header_taxonomy_mapper.py
│       ├── stage_3b_entry_classifier.py
│       ├── stage_4_field_extractor.py
│       ├── stage_4_5_research_summary.py
│       ├── stage_5_pubmed_enrichment.py
│       ├── stage_5b_institution_enrichment.py
│       ├── stage_5c_teaching_formatter.py
│       ├── stage_5d_citation_formatter.py
│       ├── stage_6_word_template.py
│       │
│       ├── config/                   # Schema and taxonomy configs
│       │   ├── field_schemas_v1.1.json
│       │   └── taxonomy_v7.json
│       │
│       ├── core/                     # Core components
│       │   ├── taxonomy_mapper_v2.py
│       │   ├── classification_rules/
│       │   └── validators/
│       │
│       ├── segmentation/             # Document parsing
│       │   ├── chunked_chat_hierarchy_extractor.py  # Primary segmenter
│       │   ├── word_chunked.py
│       │   └── pdf_vision.py
│       │
│       ├── parsers/                  # Section-specific parsers
│       │   ├── publications_parser.py
│       │   ├── education_parser.py
│       │   └── grants_parser.py
│       │
│       ├── cv_parser/                # Supporting libraries
│       │   └── cv_taxonomy_wcm.py
│       │
│       └── outputs/                  # Pipeline outputs
│           ├── stage_1a_segmentation/
│           ├── stage_1b_hierarchy_mapping/
│           ├── stage_2_entry_extraction/
│           ├── stage_3a_header_mappings/
│           ├── stage_3b_classified_entries/
│           ├── stage_4_field_extraction/
│           ├── stage_5_enrichment/
│           ├── stage_5b_institution_enrichment/
│           ├── stage_5c_teaching_formatted/
│           ├── stage_5d_citation_formatted/
│           └── stage_6_wcm_documents/
│
├── data/
│   ├── sample_cvs/
│   │   ├── word/                     # Word CV inputs
│   │   └── pdf/                      # PDF CV inputs
│   └── templates/                    # WCM templates
│
├── web_interface/
│   ├── backend/                      # FastAPI server
│   └── frontend/                     # React application
│
├── docs/                             # Documentation
├── tests/                            # Test suite
└── archive/                          # Legacy/deprecated code
```

---

## Pipeline Stages

### Stage 1a: Document Segmentation

**Purpose**: Parse document structure and identify hierarchical headers

**Input**: Word (.docx) or PDF file
**Output**: `*_segmented.json` - Hierarchical JSON with section headers

**Process**:
1. Extract document elements with formatting metadata (bold, font size, indentation)
2. Send chunks to LLM for header identification
3. Build hierarchical tree structure
4. Preserve element indices for downstream mapping

**Key Features**:
- Chunked processing to handle large documents
- Visual formatting cues (bold, ALL CAPS, underlines)
- Recursive hierarchy detection (H1 → H2 → H3)

### Stage 1b: Hierarchy Mapping

**Purpose**: Map section hierarchy to document element indices

**Input**: Stage 1a output + original document
**Output**: `*_hierarchy_mapped.json`

**Process**:
- Deterministic (no LLM)
- Maps each header to start/end element indices
- Identifies leaf sections (sections with content, no children)

### Stage 2: Entry Extraction

**Purpose**: Identify individual entries within sections

**Input**: Stage 1b output + original document
**Output**: `*_entries.json`

**Process**:
1. For each leaf section, extract text content
2. LLM identifies entry boundaries using blank line markers
3. Groups multi-line entries (e.g., multi-line citations)
4. Handles table rows and structured content

**Key Features**:
- Blank line markers (`[BLANK_LINE_X]`) for boundary detection
- Multi-line entry grouping
- Table row handling

### Stage 3a: Header Taxonomy Mapping

**Purpose**: Map CV section headers to WCM taxonomy codes

**Input**: Stage 1a segmented hierarchy
**Output**: `*_header_taxonomy.json`

**Process**:
1. Extract all header texts from hierarchy
2. LLM maps each header to most appropriate taxonomy code
3. Considers header context and parent hierarchy

**Example Mappings**:
- "EDUCATION" → B1
- "PEER-REVIEWED PUBLICATIONS" → S1
- "CURRENT GRANTS" → M2A

### Stage 3b: Entry Classification

**Purpose**: Classify each entry to specific taxonomy code

**Input**: Stage 2 entries + Stage 3a header mappings
**Output**: `*_classified.json`

**Process**:
1. For each entry, consider:
   - Parent header's taxonomy code
   - Entry content
   - Disambiguation rules
2. LLM classifies to specific code (S1, S2, M2A, etc.)
3. Post-processing validators correct common errors

**Key Features**:
- Header context inheritance
- Disambiguation rules for ambiguous entries
- Validator pipeline for corrections

### Stage 4: Field Extraction

**Purpose**: Extract structured fields from classified entries

**Input**: Stage 3b classified entries
**Output**: `*_fields.json`

**Process**:
1. Group entries by taxonomy code
2. For each code, use appropriate schema
3. LLM extracts fields per schema definition
4. Identifies CV owner's name in author lists (`target_name`)

**Extraction Modes**:
- **Minimal** (40 codes): Extract only WCM template columns
- **Comprehensive** (15 codes): Full extraction for citations, grants

**Example Fields for S1 (Publications)**:
```json
{
  "authors": "Smith JA, Jones MB, et al",
  "year": "2023",
  "title": "Article Title",
  "journal": "Nature",
  "volume": "45",
  "pages": "123-130",
  "pmid": "12345678",
  "target_name": "Smith JA"
}
```

### Stage 4.5: Research Summary Generation

**Purpose**: Generate biosketch-style M1 research summary

**Input**: Stage 4 field extraction output
**Output**: `*_research_summary.json`

**Process**:
- Synthesizes research themes from publications and grants
- Creates narrative research statement

### Stage 5: PubMed Enrichment

**Purpose**: Enrich publications with authoritative PubMed metadata

**Input**: Stage 4 output
**Output**: `*_enriched.json`

**Lookup Strategy**:

| Identifier | Strategy |
|------------|----------|
| PMID | Direct efetch lookup |
| PMCID | ID Converter → efetch |
| DOI | esearch → efetch |
| None | Skip enrichment |

**Enriched Fields**:
- Full author list in Vancouver format
- Official journal name
- Volume, issue, pages
- PMID/PMCID discovery
- Publication types (Journal Article, Review, etc.)
- MeSH terms

### Stage 5b: Institution Enrichment

**Purpose**: Add location data to education and position entries

**Input**: Stage 5 output
**Output**: `*_institution_enriched.json`

**Process**:
- Query ROR (Research Organization Registry) API
- Add city, state, country for institutions

**Eligible Codes**: B1, B2, C, D1, D2, D3

### Stage 5c: Teaching Formatter

**Purpose**: LLM-polish teaching entries (K-codes) for readability

**Input**: Stage 5b output
**Output**: `*_teaching_formatted.json`

**Process**:
- Standardize date formats
- Format role descriptions
- Handle sub-bullet items

### Stage 5d: Citation Formatter

**Purpose**: Format non-enriched citations to Vancouver style

**Input**: Stage 5c output
**Output**: `*_citation_formatted.json`

**Process**:
- Normalize author names
- Format book chapters with "In:" prefix
- Standardize citation components

### Stage 6: WCM Word Template Generation

**Purpose**: Generate formatted WCM Word document

**Input**: Best available enriched output
**Output**: `*_wcm.docx`

**Features**:
- Fills all WCM template sections
- Bolds CV owner's name in citations
- Vancouver citation formatting
- Table population for structured sections
- Professional typography (Arial, proper spacing)

---

## Taxonomy System

### Overview

The WCM CV taxonomy consists of **60 valid codes** organized into **20 top-level categories** (A-T).

### Code Categories

| Category | Codes | Description |
|----------|-------|-------------|
| **A** | A | Personal/Contact Information |
| **B** | B1, B2 | Education (Academic Degrees, Other) |
| **C** | C | Postdoctoral Training |
| **D** | D1, D2, D3 | Professional Positions (Academic, Hospital, Other) |
| **E** | E | Employment Status |
| **F** | F1, F2 | Licensure, Board Certification |
| **G** | G | Institutional Affiliations |
| **H** | H | Honors & Awards |
| **I** | I | Professional Organizations |
| **J** | J | Percent Effort |
| **K** | K1-K5 | Educational Contributions (Didactic, Clinical, Admin, CME, Community) |
| **L** | L1-L3 | Clinical Practice, Innovation, Leadership |
| **M** | M1, M2A-D | Research Activities, Grants, Patents |
| **N** | N1-N4, N3A-B | Mentoring & Advising |
| **O** | O | Institutional Leadership |
| **P** | P | Institutional Administrative Activities |
| **Q** | Q1-Q4, Q4A-D | Extramural Service (Leadership, Committees, Grant Review, Editorial) |
| **R** | R | Invited Presentations |
| **S** | S0-S9 | Bibliography (Publications, Books, Abstracts) |
| **T** | T | Appendix/Other |

### Key Disambiguation Rules

1. **H (Honors) vs I (Professional Organizations)**
   - Post-nominal abbreviations (FACP, FAHA) → I
   - One-time recognition → H

2. **H (Honors) vs M2 (Research Funding)**
   - >$100K + PI role → M2A/M2B/M2C
   - Small awards → H

3. **D (Positions) vs O (Leadership)**
   - Budget/personnel authority → O
   - Job title only → D

4. **Clinical Trials**
   - Now classified under M2A/M2B/M2C based on status (not M4)

---

## Technical Requirements

### System Requirements

- **Python**: 3.8+ (CLI), 3.10+ (Web Interface)
- **Node.js**: 18+ (Web Interface only)
- **Operating System**: macOS, Linux, Windows
- **Memory**: Minimum 4GB RAM (8GB recommended)
- **Disk**: ~500MB for installation + output space

### Python Dependencies (CLI & Backend)

**Core Dependencies**:
```
openai>=1.0.0          # OpenAI API client
python-docx>=0.8.11    # Word document processing
pdfplumber             # PDF parsing
pdf2image              # PDF to image conversion
requests               # HTTP requests for APIs
```

**Processing Dependencies**:
```
numpy                  # Numerical operations
scipy                  # Scientific computing
rapidfuzz              # Fuzzy string matching
regex                  # Advanced regex
```

**Utilities**:
```
PyYAML                 # YAML configuration
rich                   # Terminal output formatting
jinja2                 # Template rendering
```

**Web Backend (FastAPI)**:
```
fastapi                # Web framework
uvicorn                # ASGI server
sqlalchemy             # ORM for SQLite
python-multipart       # File upload handling
websockets             # Real-time communication
```

### Frontend Dependencies (Node.js)

```
react@18               # UI framework
react-dom@18           # React DOM
react-router-dom@6     # Client-side routing
typescript@5           # Type safety
vite@5                 # Build tool
tailwindcss@3          # CSS framework
zustand@4              # State management
@tanstack/react-table  # Data tables
```

### External Services

| Service | Required | Purpose | Cost |
|---------|----------|---------|------|
| OpenAI API | Yes | LLM processing | Pay-per-token |
| NCBI E-utilities | No | PubMed enrichment | Free (with API key: 10 req/s) |
| ROR API | No | Institution lookup | Free |

### Environment Variables

#### Required

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key for LLM processing. Used by all stages that make LLM calls. |

#### Optional - OpenAI Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY_WORK` | Alternative OpenAI API key. Used as fallback if `OPENAI_API_KEY` is not set. | None |
| `SEGMENTATION_MODEL` | Override the default segmentation model for Stage 1b. | `gpt-4.1-mini` |
| `CV_HIERARCHY_ASSISTANT_ID` | OpenAI Assistants API ID for CV hierarchy processing. Required only if using Assistants API mode. | None |
| `CV_DIRECT_FILE_ASSISTANT_ID` | OpenAI Assistants API ID for direct file processing. Required only if using Assistants API mode. | None |

#### Optional - External Services

| Variable | Description | Default |
|----------|-------------|---------|
| `NCBI_API_KEY` | NCBI E-utilities API key. Increases rate limit from 3 to 10 requests/second for PubMed enrichment. | None |
| `PUBMED_API_KEY` | Alternative name for NCBI API key. Either variable works. | None |

#### Optional - Development/Debugging

| Variable | Description | Default |
|----------|-------------|---------|
| `PROMPT_LOG_DIR` | Directory for saving LLM prompt/response logs. Useful for debugging and cost analysis. | None (logging disabled) |
| `ENVIRONMENT` | Web backend environment mode. Set to `development` for debug features. | `production` |

#### Example `.env` File

```bash
# Required
OPENAI_API_KEY=sk-...

# Optional - improves PubMed rate limits
NCBI_API_KEY=...

# Optional - for debugging LLM calls
PROMPT_LOG_DIR=./prompt_logs

# Optional - override default model
# SEGMENTATION_MODEL=gpt-4.1

# Optional - for Assistants API mode
# CV_HIERARCHY_ASSISTANT_ID=asst_...
# CV_DIRECT_FILE_ASSISTANT_ID=asst_...
```

---

## Installation

### 1. Clone Repository

```bash
git clone <repository_url>
cd cv_parsing_project
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install openai python-docx pdfplumber pdf2image requests
pip install numpy scipy rapidfuzz regex PyYAML rich jinja2
```

### 4. Set Environment Variables

```bash
export OPENAI_API_KEY="sk-your-key-here"
# Optional:
export NCBI_API_KEY="your-ncbi-key"
```

### 5. Validate Installation

```bash
python src/unified_pipeline/config.py
```

---

## Usage

### Command-Line Interface

```bash
# Full pipeline
python3 run_full_pipeline.py data/sample_cvs/word/2097_Upton_Cv.docx

# Full pipeline (by UID only - looks in data/sample_cvs/word/)
python3 run_full_pipeline.py 2097_Upton_Cv

# Run specific stage only
python3 run_full_pipeline.py 2097_Upton_Cv --stage 2
python3 run_full_pipeline.py 2097_Upton_Cv --stage 3a
python3 run_full_pipeline.py 2097_Upton_Cv --stage 4

# Run with different model
python3 run_full_pipeline.py 2097_Upton_Cv --model gpt-4o-mini
```

### Stage Options

| Stage | Description | Prerequisites |
|-------|-------------|---------------|
| `1a` | Segmentation | None |
| `1b` | Hierarchy Mapping | 1a |
| `2` | Entry Extraction | 1b |
| `3a` | Header Taxonomy | 1a |
| `3b` | Entry Classification | 2, 3a |
| `3` | Both 3a and 3b | 1a, 2 |
| `4` | Field Extraction | 3b |
| `4.5` | Research Summary | 4 |
| `5` | PubMed Enrichment | 4 |
| `5b` | Institution Enrichment | 5 or 4 |
| `5c` | Teaching Formatter | 5b or earlier |
| `5d` | Citation Formatter | 5c or earlier |
| `6` | Word Template | 4+ |

### Web Application

```bash
# Start backend
cd web_interface/backend
uvicorn app.main:app --reload --port 8000

# Start frontend (new terminal)
cd web_interface/frontend
npm install  # First time only
npm start

# Open http://localhost:3000
```

### Python API

```python
import sys
sys.path.insert(0, 'src')

from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import get_cv_hierarchy_chunked
from unified_pipeline.stage_2_entry_extraction import run_stage_2

# Stage 1a: Segmentation
hierarchy, stats = get_cv_hierarchy_chunked(
    cv_path='data/sample_cvs/word/cv.docx',
    model='gpt-4o-mini'
)

# Stage 2: Entry Extraction
entries, output_path = run_stage_2(
    docx_path='data/sample_cvs/word/cv.docx',
    hierarchy_json_path='outputs/stage_1b_hierarchy_mapping/cv_hierarchy_mapped.json'
)
```

---

## Web Interface

The pipeline includes a modern web application ("CViche Pipeline Viewer") for interactive CV processing with real-time progress tracking.

### Features

- **File Upload**: Drag-and-drop or click-to-select .docx/.pdf files
- **Real-time Progress**: WebSocket-based live updates during processing
- **Step-by-step Visualization**: 9-step pipeline with status icons (pending/running/complete/error)
- **Cost & Token Tracking**: Live LLM usage monitoring
- **Log Streaming**: Real-time log output for each step
- **Output Downloads**: Download JSON/DOCX outputs for each stage
- **Responsive UI**: Modern design with Tailwind CSS

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WEB INTERFACE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  FRONTEND (React + TypeScript + Vite)                                       │
│  ├── React 18 with TypeScript                                               │
│  ├── Tailwind CSS for styling                                               │
│  ├── Vite for development/bundling                                          │
│  ├── Zustand for state management                                           │
│  ├── TanStack Table for data viewing                                        │
│  └── WebSocket client for real-time updates                                 │
│                                                                             │
│  BACKEND (FastAPI + SQLite)                                                 │
│  ├── FastAPI server (port 8000)                                             │
│  ├── SQLite database for run/step tracking                                  │
│  ├── WebSocket server for real-time events                                  │
│  ├── Pipeline orchestrator                                                  │
│  └── RESTful API endpoints                                                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
web_interface/
├── backend/
│   ├── app/
│   │   ├── api/                      # API endpoints
│   │   │   ├── upload.py             # File upload handling
│   │   │   ├── runs.py               # Run management
│   │   │   ├── steps.py              # Step details
│   │   │   └── websocket.py          # Real-time streaming
│   │   ├── pipeline/                 # Pipeline orchestration
│   │   │   ├── orchestrator.py       # Main executor
│   │   │   ├── step_registry.py      # Step definitions
│   │   │   └── event_emitter.py      # WebSocket events
│   │   ├── database.py               # SQLAlchemy setup
│   │   ├── models.py                 # DB models (runs, steps, logs)
│   │   ├── schemas.py                # Pydantic schemas
│   │   └── main.py                   # FastAPI application
│   ├── cviche.db                     # SQLite database
│   ├── requirements.txt              # Python dependencies
│   └── prompt_logs/                  # LLM prompt logging
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── UploadPage.tsx        # Upload interface
│   │   │   └── PipelineViewer.tsx    # Pipeline progress viewer
│   │   ├── App.tsx                   # Main application
│   │   ├── main.tsx                  # Entry point
│   │   └── index.css                 # Tailwind styles
│   ├── package.json                  # Node dependencies
│   ├── vite.config.ts                # Vite configuration
│   └── tailwind.config.js            # Tailwind configuration
│
├── uploads/                          # Uploaded CV files
├── outputs/                          # Pipeline outputs (by run_id)
├── start.sh                          # Startup script
└── README.md                         # Web interface documentation
```

### Installation & Setup

**Prerequisites**:
- Python 3.10+
- Node.js 18+ and npm
- OpenAI API key

**Backend Setup**:
```bash
cd web_interface/backend
pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env and add: OPENAI_API_KEY_WORK=sk-...
```

**Frontend Setup**:
```bash
cd web_interface/frontend
npm install
```

### Running the Web Interface

**Option A: Separate Terminals**
```bash
# Terminal 1: Start backend
cd web_interface/backend
uvicorn app.main:app --reload --port 8000

# Terminal 2: Start frontend
cd web_interface/frontend
npm run dev
```

**Option B: Startup Script**
```bash
cd web_interface
./start.sh
```

**Access Points**:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- API Documentation: http://localhost:8000/docs (Swagger UI)

### Pipeline Steps (Web UI)

The web interface visualizes 9 processing steps:

| Step | Name | Description |
|------|------|-------------|
| 1 | Identify Sections | Detects major CV sections (headers) |
| 2 | Preserve Formatting | Retains paragraph structure |
| 3 | Break Into Items | Splits sections into individual entries |
| 4 | Categorize Entries | Classifies entries to taxonomy codes |
| 5 | Fix Unknowns | Re-examines unclassified entries |
| 6 | Extract Structured Data | Pulls out specific fields per entry type |
| 7 | AI Assist | LLM fallback for tricky entries |
| 8 | Add Organization Data | ROR API lookup for institutions (optional) |
| 9 | Generate Final Template | Produces WCM .docx output |

### API Endpoints

**Upload**:
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload CV file (.docx/.pdf) |

**Runs**:
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/run/{run_id}/status` | Get run status and progress |
| POST | `/api/run/{run_id}/start` | Start pipeline execution |
| POST | `/api/run/{run_id}/pause` | Pause execution |
| POST | `/api/run/{run_id}/retry/{step_number}` | Retry failed step |

**Steps**:
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/run/{run_id}/step/{step_number}` | Get step details and logs |
| GET | `/api/run/{run_id}/data/{filename}` | Download output file |

**WebSocket**:
| Protocol | Endpoint | Description |
|----------|----------|-------------|
| WS | `/ws/run/{run_id}/stream` | Real-time pipeline events |

### Database Schema

The SQLite database (`cviche.db`) tracks:

| Table | Purpose |
|-------|---------|
| `runs` | Pipeline run metadata (status, timestamps, costs) |
| `steps` | Individual step execution (status, duration, outputs) |
| `logs` | Execution logs per step |
| `llm_usage` | LLM API usage and token costs |

### Frontend Dependencies

```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "zustand": "^4.4.7",
    "@tanstack/react-table": "^8.11.2"
  },
  "devDependencies": {
    "typescript": "^5.3.3",
    "vite": "^5.0.7",
    "tailwindcss": "^3.3.6"
  }
}
```

### Troubleshooting

**Backend won't start**:
- Check port 8000 availability: `lsof -i :8000`
- Verify Python dependencies: `pip install -r requirements.txt`
- Check `.env` file has `OPENAI_API_KEY_WORK` set

**Frontend won't start**:
- Check port 3000 availability
- Reinstall dependencies: `rm -rf node_modules && npm install`

**WebSocket connection fails**:
- Ensure backend is running on port 8000
- Check browser console for CORS errors
- Verify `vite.config.ts` proxy settings

---

## Configuration

### Central Configuration (`src/unified_pipeline/config.py`)

```python
# LLM Settings
DEFAULT_MODEL = "gpt-4o-mini"
SEGMENTATION_MODEL = "gpt-4o-mini"
TAXONOMY_MODEL = "gpt-4o-mini"
PARSING_MODEL = "gpt-4o-mini"

# Feature Flags
USE_LEGACY_HANDLERS = True           # Professional WCM formatting
ENABLE_PMCID_ENRICHMENT = True       # PubMed API lookup
ENABLE_AUTHOR_BOLDING = True         # Bold CV owner's name
ENABLE_AUTHOR_ABBREVIATION = True    # "Smith J" not "John Smith"
ENABLE_SUBSECTION_CATEGORIZATION = True
USE_TAXONOMY_MAPPING = True          # Use LLM mappings
```

### Model Pricing (per 1M tokens)

| Model | Input | Output |
|-------|-------|--------|
| gpt-4o-mini | $0.15 | $0.60 |
| gpt-4o | $2.50 | $10.00 |
| gpt-5.1 | Varies | Varies |

---

## Output Formats

### Stage 4 Output (`*_fields.json`)

```json
{
  "document_uid": "2015_Wende",
  "stage": "4",
  "cv_owner": {"last_name": "Wende"},
  "schema_version": "1.1",
  "stats": {
    "total_entries": 248,
    "extracted": 235,
    "avg_coverage_percent": 82.3
  },
  "entries": [
    {
      "element_idx_start": 42,
      "taxonomy_code": "S1",
      "text": "Wende ME, Smith J. Title. Journal. 2023;45:123.",
      "extracted_fields": {
        "authors": "Wende ME, Smith J",
        "year": "2023",
        "title": "Title",
        "journal": "Journal",
        "volume": "45",
        "pages": "123",
        "target_name": "Wende ME"
      }
    }
  ]
}
```

### Stage 5 Output (Enriched)

```json
{
  "enrichment_status": "enriched",
  "enrichment_source": "pmid",
  "enriched_fields": ["pmcid", "volume"],
  "enrichment_data": {
    "pubmed_authors": "Smith JA, Jones MB, et al",
    "pubmed_journal": "New England Journal of Medicine",
    "publication_types": ["Journal Article", "Randomized Controlled Trial"],
    "mesh_terms": ["COVID-19", "Vaccines"]
  }
}
```

### Stage 5b Output (Institution Enrichment)

```json
{
  "institution_enrichment": {
    "ror_id": "https://ror.org/00rs6vg23",
    "official_name": "Ohio State University",
    "city": "Columbus",
    "state": "OH",
    "country": "United States",
    "source": "ror_api"
  }
}
```

---

## External API Integrations

### OpenAI API

**Purpose**: All LLM processing (segmentation, classification, extraction)

**Authentication**:
```python
# Uses default client initialization
client = OpenAI()  # Reads OPENAI_API_KEY from environment
```

**Cost Management**:
- Use `gpt-4o-mini` for cost-sensitive operations
- Batch entries by taxonomy code to reduce API calls
- Minimal extraction mode for non-critical codes

### NCBI E-utilities (PubMed)

**Purpose**: Publication enrichment

**Endpoints**:
- `efetch`: Retrieve records by PMID
- `esearch`: Search by DOI
- `idconv`: Convert PMCID ↔ PMID

**Rate Limits**:
- Without API key: 3 requests/second
- With API key: 10 requests/second

### ROR API

**Purpose**: Institution location lookup

**Endpoint**: `https://api.ror.org/organizations`

**Features**:
- Free, no authentication required
- Returns city, state, country
- Cached to `config/ror_cache.json`

---

## Cost Management

### Typical Costs per CV

| Stage | Cost Range | Notes |
|-------|------------|-------|
| 1a (Segmentation) | $0.01-0.03 | Varies by document length |
| 2 (Entry Extraction) | $0.01-0.02 | Varies by section count |
| 3a-3b (Classification) | $0.01-0.02 | Varies by entry count |
| 4 (Field Extraction) | $0.02-0.05 | Comprehensive mode costs more |
| 5c-5d (Formatters) | $0.01-0.02 | LLM-based formatting |
| **Total** | **$0.05-0.20** | Full pipeline |

### Cost Optimization Strategies

1. **Use gpt-4o-mini** for all stages (default)
2. **Minimal extraction** for non-publication codes
3. **Batch processing** groups entries by code
4. **Skip enrichment** if not needed (run stages 1-4 only)

### Monitoring

Pipeline logs estimated costs per stage:
```
[S1] 15 entries | 2,450 tokens | $0.0073
[M2A] 8 entries | 1,890 tokens | $0.0057
Batch 1/5 complete | Running total: $0.0130
```

---

## Validators & Post-Processing

### Pre-Classification Validators

| Validator | Purpose |
|-----------|---------|
| `LabelContentConflictValidator` | Detects generic labels with specific content |
| `S7UnpublishedValidator` | Identifies unpublished work patterns |
| `URLDomainValidator` | URL-based classification hints |

### Post-Classification Correctors

| Corrector | Purpose |
|-----------|---------|
| `StructuralHeaderValidator` | CV titles, page numbers → T |
| `CommitteePositionCorrector` | Committee service (P/Q2) vs positions (D) |
| `GrantStatusCorrector` | M2A/M2B/M2C based on dates |
| `TeachingLeadershipCorrector` | Course Directors → K3 |
| `InvitedTalkCorrector` | Keynotes → R (not S8) |

---

## Development

### Running Tests

```bash
# Unit tests
pytest tests/

# Integration test for pipeline stages
python src/unified_pipeline/test_pipeline_stages.py
```

### Adding New Taxonomy Codes

1. Update `src/unified_pipeline/config/taxonomy_v7.json`
2. Update `taxonomy_reference.md`
3. Add field schema in `config/field_schemas_v1.1.json`
4. Update validators if needed

### Debugging

Enable prompt logging:
```bash
export PROMPT_LOG_DIR=./prompt_logs_debug
python3 run_full_pipeline.py 2097_Upton_Cv
```

Prompt logs saved to `src/unified_pipeline/prompt_logs/` with request/response details.

### Contributing

1. Place new scripts in appropriate `src/unified_pipeline/` subdirectory
2. Use relative imports (`from ..core.taxonomy_mapper import ...`)
3. Add docstrings explaining purpose
4. Update documentation for major functionality

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 15.0 | 2025-12-03 | Taxonomy v7.5: Clinical trials unified with grants (M2A/M2B/M2C) |
| 14.0 | 2025-12-01 | Stage 2 blank line markers, D3 for GRA/GTA |
| 13.0 | 2025-11-29 | Added Stages 5b (ROR), 5c (Teaching), 5d (Citations) |
| 12.0 | 2025-11-29 | Added Stage 5 (PubMed) and Stage 6 (Word Template) |
| 11.0 | 2025-11-29 | Field schema versioning, minimal/comprehensive modes |
| 10.0 | 2025-11-25 | Taxonomy v7 with 60 codes |

---

## Support

**Documentation**:
- `PIPELINE_README.md` - Pipeline-specific documentation
- `taxonomy_reference.md` - Complete taxonomy reference
- `MASTER_DOCUMENTATION_INDEX.md` - Full system overview

**Troubleshooting**:
1. Check environment variables are set
2. Verify OpenAI API key is valid
3. Run `python src/unified_pipeline/config.py` to validate setup
4. Check prompt logs for LLM errors

---

**Maintainer**: CV Pipeline Team
**License**: Proprietary
**Canonical Source**: `src/unified_pipeline/`
