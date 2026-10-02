# Support & FAQ

## Frequently Asked Questions

### General

<details>
<summary><strong>What does CViche do?</strong></summary>

CViche is an AI-powered pipeline that transforms unstructured academic CVs into a standardized institutional format. It reads a Word document, identifies sections (education, publications, grants, etc.), classifies each entry against a 60-code taxonomy, extracts structured fields, and generates a formatted output document -- all automatically.

</details>

<details>
<summary><strong>Why "CViche"?</strong></summary>

Like the dish, it takes raw ingredients and transforms them through a structured process. Also, it's a CV tool. We thought it was funny.

</details>

<details>
<summary><strong>What format does the input CV need to be?</strong></summary>

**Word documents (`.docx`) only.** The pipeline relies on Word's heading styles and paragraph structure to identify section boundaries and entry breaks. PDFs and plain text files lose this structural information during extraction, which significantly degrades segmentation accuracy.

If you only have a PDF, convert it to `.docx` first (e.g., using Microsoft Word's built-in converter or Adobe Acrobat).

</details>

<details>
<summary><strong>How long does it take to process a CV?</strong></summary>

Typically **2-5 minutes** depending on CV length and the number of publications. The sample CV (Dr. Vasquez, ~6,000 characters) processes in about 2.5 minutes. Longer CVs with hundreds of publications may take 8-10 minutes.

Most of the time is spent on Stage 3b (entry classification), which sends each section group to the LLM individually for accurate taxonomy mapping.

</details>

<details>
<summary><strong>How much does it cost per CV?</strong></summary>

Each run's actual cost is recorded on the run and shown in the admin dashboard. Per-token rates for the configured models, and the safe places to drop to a cheaper model, are in [LLM_MODELS.md](LLM_MODELS.md#cost).

</details>

<details>
<summary><strong>What LLM models are supported?</strong></summary>

CViche is **Bedrock-only** -- all LLM traffic stays in AWS Bedrock. Supported model families include Claude (Anthropic), Llama (Meta), and Mistral, via the Converse API; the default is Claude Sonnet 4.6.

Every stage, including 3b, runs Claude Sonnet 5 by default. Model selection lives in `src/unified_pipeline/config/llm_config.yaml` (a `default` block plus `stages:` overrides) -- different stages may benefit from different model tiers, with segmentation/classification benefiting most from larger models and formatting stages (5c, 5d) working well with smaller ones. See [LLM_MODELS.md](LLM_MODELS.md) for how to change a stage's model.

</details>

---

### Setup & Configuration

<details>
<summary><strong>I get a Bedrock access/credentials error -- what do I do?</strong></summary>

Set AWS credentials so the boto3 default credential chain can find them:

```bash
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
export AWS_DEFAULT_REGION=us-east-1
```

(Or use `~/.aws/credentials`, or an IAM instance/pod role -- no static keys needed there.) You also need Bedrock model access enabled for the configured model in that AWS account/region -- see [docs/LLM_MODELS.md](LLM_MODELS.md#bedrock-setup).

To make env vars persistent, add the export lines to your `~/.zshrc` or `~/.bashrc`.

</details>

<details>
<summary><strong>Can I run individual pipeline stages?</strong></summary>

Yes. Use the `--stage` flag:

```bash
# Run only Stage 3b (entry classification)
python3 run_full_pipeline.py sample_vasquez_cv --stage 3b
```

This is useful for debugging or re-running a specific stage after adjusting prompts. Each stage reads its input from the previous stage's output directory.

</details>

<details>
<summary><strong>Where are the pipeline outputs?</strong></summary>

Each stage writes its output to a dedicated directory:

```
src/unified_pipeline/outputs/
  stage_1a_segmentation/
  stage_1b_hierarchy_mapping/
  stage_2_entry_extraction/
  stage_3a_header_mappings/
  stage_3b_classified_entries/
  stage_4_field_extraction/
  stage_4_5_research_summary/
  stage_5_pubmed_enrichment/
  stage_5b_institution_enrichment/
  stage_5c_teaching_formatted/
  stage_5d_citation_formatted/
  stage_6_wcm_documents/          <-- final Word output
```

The final WCM-formatted document is in `stage_6_wcm_documents/`.

</details>

<details>
<summary><strong>How do I set up the web interface?</strong></summary>

The fastest path is Docker:

```bash
cd web_interface
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
export AWS_DEFAULT_REGION=us-east-1
docker compose up --build
```

This starts MariaDB, the FastAPI backend (port 8000), and the React frontend (port 3000). Open `http://localhost:3000` in your browser.

For development without Docker, see the [README](../README.md#development-mode-without-docker).

</details>

---

### Pipeline Behavior

<details>
<summary><strong>What are the 43 post-classification validators?</strong></summary>

After the LLM classifies entries in Stage 3b, a suite of rule-based validators checks for common misclassifications. For example:

- **Committee vs. position**: Ensures committee memberships aren't classified as professional positions
- **Grant status**: Uses date ranges to distinguish current vs. completed funding
- **Teaching leadership**: Detects course director roles that should be classified differently from regular teaching
- **Invited talks**: Catches presentations misclassified as publications

These validators encode institutional knowledge that the LLM may not consistently apply.

</details>

<details>
<summary><strong>How does PubMed enrichment work?</strong></summary>

Stage 5 takes publications classified as peer-reviewed (S1) and searches PubMed via NCBI E-utilities. When a match is found, it adds the PMID, DOI, journal abbreviation, and MeSH terms. The target author's name is bolded in the final output.

Publications that don't match PubMed (conference proceedings, book chapters, etc.) pass through unchanged and get formatted by Stage 5d (citation formatter) instead.

</details>

<details>
<summary><strong>What is the research summary (Stage 4.5)?</strong></summary>

Stage 4.5 generates a biosketch-style research narrative in NIH M1 format. It analyzes the faculty member's publications, grants, and appointments to produce a ~150-200 word summary of their research program. This is inserted into Section M1 of the WCM template.

</details>

<details>
<summary><strong>Why does Stage 3b cost so much more than other stages?</strong></summary>

Stage 3b classifies every entry in the CV individually, grouped by section. A CV with 15 sections sends 15 separate LLM requests, each containing the full taxonomy definition as context. The taxonomy prompt is large (~4,000 tokens), and it's repeated for each group. This is by design -- per-group classification with full context produces significantly better accuracy than batch approaches.

</details>

---

### Troubleshooting

<details>
<summary><strong>The pipeline hangs or times out</strong></summary>

This is usually a Bedrock throttle or timeout. Check:

1. Your AWS account has Bedrock model access enabled for the configured model, in the configured region
2. You're not hitting per-minute token limits
3. Your network can reach the Bedrock endpoint for your configured AWS region

If a specific stage fails, you can re-run just that stage with `--stage` rather than restarting the entire pipeline.

</details>

<details>
<summary><strong>The output document has entries in the wrong section</strong></summary>

Classification accuracy depends on how clearly the input CV is structured. Common causes of misclassification:

- **Ambiguous section headers**: "Professional Activities" could map to several taxonomy codes. The LLM uses the entries beneath the header to disambiguate.
- **Mixed-content sections**: Sections that combine service, teaching, and administrative roles are harder to classify than sections with a single entry type.
- **Non-standard CV structure**: CVs that don't follow a traditional academic format may confuse the header-taxonomy mapping in Stage 3a.

Check the Stage 3b output JSON to see the classification reasoning, then adjust the input CV's section headers if needed.

</details>

<details>
<summary><strong>PubMed enrichment missed some of my publications</strong></summary>

PubMed matching requires that the author name and enough title words match a PubMed record. Common reasons for missed matches:

- The publication is too recent and not yet indexed in PubMed
- The author name in the CV differs from the PubMed record (e.g., middle initial)
- It's a non-PubMed publication (book chapter, conference paper, white paper)
- The journal is not indexed by PubMed

Non-matched publications are still included in the output -- they're formatted by Stage 5d (citation formatter) instead.

</details>

<details>
<summary><strong>Docker compose fails to start</strong></summary>

Common issues:

- **Port conflict**: Another process is using port 3000, 3306, or 8000. Check with `lsof -i :3000`.
- **Missing AWS credentials**: The backend requires AWS credentials (for Bedrock) to be set in your host shell before running `docker compose`.
- **Docker not running**: Ensure Docker Desktop (or Docker Engine) is running.
- **First build is slow**: The initial `docker compose up --build` downloads base images and installs dependencies. Subsequent starts are faster.

</details>

---

### Adapting CViche

<details>
<summary><strong>Can I use this for a non-WCM institution?</strong></summary>

The pipeline architecture is institution-agnostic, but the taxonomy and output template are WCM-specific. See the [Adapting for Other Institutions](../README.md#adapting-for-other-institutions) section in the README for what you'd need to change.

In short: replace the taxonomy codes, update the Word output template, and adjust the classification prompts. The segmentation, entry extraction, field parsing, and PubMed enrichment stages work without modification.

</details>

<details>
<summary><strong>Can I add new pipeline stages?</strong></summary>

Yes. Each stage is a standalone Python module that reads from the previous stage's output directory and writes to its own. To add a new stage:

1. Create `stage_N_your_stage.py` following the pattern of existing stages
2. Add it to the stage list in `run_full_pipeline.py`
3. Create an output directory under `src/unified_pipeline/outputs/`

The pipeline runner calls stages sequentially, so your new stage just needs to read the right input JSON and produce its output.

</details>

---

## Getting Help

- **Bug reports**: [Open an issue](https://github.com/wcmc-its/CViche/issues) on GitHub
- **Sample CV for testing**: Run `python3 run_full_pipeline.py sample_vasquez_cv` to verify your setup works before processing real CVs
- **Pipeline documentation**: See [docs/PIPELINE_README.md](PIPELINE_README.md) for detailed stage-by-stage documentation
