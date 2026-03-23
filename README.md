# CViche

AI-powered CV parsing pipeline that transforms unstructured academic CVs into standardized WCM format.

## What It Does

CViche takes unstructured academic CVs in Word (.docx) or PDF format and runs them through a 12-stage AI pipeline. The output is a standardized Word document formatted to the Weill Cornell Medicine (WCM) curriculum vitae template. Stages range from document segmentation and entry extraction to PubMed enrichment and citation formatting -- all orchestrated through a single CLI command or web interface.

```bash
python3 run_full_pipeline.py sample_vasquez_cv
```

## Visual Overview

<!-- Screenshot: Pipeline viewer showing real-time stage progress -- TODO: capture when dev server is running -->

<!-- Screenshot: Upload page with file upload area -- TODO: capture when dev server is running -->

<!-- Screenshot: Side-by-side input CV vs WCM output comparison -- TODO: capture when dev server is running -->

## Pipeline Stages

1. **Stage 1a -- Segmentation**: LLM-powered hierarchical segmentation of CV document structure
2. **Stage 1b -- Hierarchy Mapping**: Maps extracted headers to document element indices (no LLM)
3. **Stage 2 -- Entry Extraction**: Detects individual entries and extracts text using LLM
4. **Stage 3a -- Header Taxonomy Mapping**: Maps CV section headers to WCM taxonomy codes
5. **Stage 3b -- Entry Classification**: Classifies entries using header context and content analysis
6. **Stage 4 -- Field Extraction**: Extracts structured fields from classified entries using domain-specific parsers
7. **Stage 4.5 -- Research Summary**: Generates a biosketch-style research summary (NIH M1 format)
8. **Stage 5 -- PubMed Enrichment**: Enriches publications with PubMed metadata via NCBI E-utilities
9. **Stage 5b -- Institution Enrichment**: Adds city/state to institutional affiliations via ROR API
10. **Stage 5c -- Teaching Formatter**: Reformats teaching entries for consistent presentation
11. **Stage 5d -- Citation Formatter**: Reformats non-enriched citations to Vancouver style
12. **Stage 6 -- Word Output**: Generates the final formatted Word document using the WCM template

## Getting Started

### Prerequisites

- Python 3.11+
- `poppler-utils` system package (required for PDF processing)
- OpenAI API key

### Installation

```bash
git clone https://github.com/wcm-its/CViche.git
cd CViche
pip install -r requirements.txt
```

### Configuration

Set your OpenAI API key as an environment variable:

```bash
export OPENAI_API_KEY=your-key-here
```

Get an API key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys).

Pipeline behavior can be tuned via `config.yaml`, which controls taxonomy settings, PDF processing parameters, and LLM model selection.

## Running the Pipeline (CLI)

```bash
# Run full pipeline on a CV
python3 run_full_pipeline.py path/to/cv.docx

# Run full pipeline using document UID
python3 run_full_pipeline.py 2097_Upton_Cv

# Run a single stage
python3 run_full_pipeline.py 2097_Upton_Cv --stage 3b

# Specify LLM model
python3 run_full_pipeline.py 2097_Upton_Cv --model gpt-5.1
```

Stage outputs are written to `src/unified_pipeline/outputs/stage_*/`, with each stage producing a JSON file named by the document UID.

## Web Interface

### Docker (Recommended)

```bash
cd web_interface
docker compose up --build
```

This starts three services:

| Service | Port | Description |
|---------|------|-------------|
| MariaDB | 3306 | Database |
| Backend | 8000 | FastAPI API server |
| Frontend | 3000 | React web application |

Set the `OPENAI_API_KEY_WORK` environment variable before running `docker compose` so the backend can access the OpenAI API.

### Development Mode (without Docker)

The backend is a FastAPI application served by Uvicorn, and the frontend is a Vite dev server (React + Tailwind CSS). Install backend dependencies separately:

```bash
pip install -r web_interface/backend/requirements.txt
```

## Sample CV

A synthetic sample CV is included for demonstration purposes:

```bash
python3 run_full_pipeline.py sample_vasquez_cv
```

The sample CV belongs to Dr. Elena M. Vasquez, a fabricated mid-career physician-scientist at Weill Cornell Medicine. It exercises all 12 pipeline stages, including PubMed enrichment (uses real journal names with fabricated articles). The file is located at `data/sample_cvs/word/sample_vasquez_cv.docx`.

## Versioning

This project follows [Semantic Versioning](https://semver.org/):

- **Major**: Architecture changes (e.g., new pipeline framework, database migration)
- **Minor**: Model switches or new processing stages
- **Patch**: Prompt tuning and bug fixes

See [CHANGELOG.md](CHANGELOG.md) for version history.

## License

This project is licensed under the Apache License 2.0 -- see the [LICENSE](LICENSE) file for details.
