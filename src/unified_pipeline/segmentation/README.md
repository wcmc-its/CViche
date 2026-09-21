# CV Segmentation Approaches (Stage 1a)

Stage 1a provides hierarchical CV segmentation using LLM-based analysis.

## Production (Recommended)

**signature_based_segmentation.py** - Signature-based Word CV segmentation
- ✅ **USE THIS** for production Word CV processing
- Uses GPT-5.1 for hierarchy normalization
- Two-pass prompts for hierarchy correction
- Console output shows both prompts and responses
- Output: `stage_1a_segmentation/{file_handle}_segmented.json`

## Alternative Approaches

**word_chunked.py** - Chunked Word CV segmentation
- Handles large CVs (1,000+ CVs)
- No rate limit issues
- Cost: ~$0.15-0.20 per CV
- Time: 30-60 seconds per CV

**pdf_vision.py** - PDF CV segmentation (vision-based)
- Three-pass vision approach
- For PDF CVs
- More expensive (~$0.65-1.15 per CV)
- Slower (3-5 minutes per CV)

**word_delimited.py** - Word with paragraph delimiters
- Alternative Word approach
- Experimental

**word_original.py** - Original Word segmentation
- ⚠️ Has rate limit issues for large CVs
- Kept for reference

## Pipeline Flow

```
Stage 1a (Segmentation - LLM)
    → outputs/stage_1a_segmentation/{file_handle}_segmented.json

Stage 1b (Hierarchy Mapping - NO LLM)
    → outputs/stage_1b_hierarchy_mapping/{file_handle}_hierarchy_mapped.json

Stage 2 (Entry Extraction)
    → outputs/stage_2_entry_extraction/{file_handle}_entries.json
```

## Documentation

See `../OUTPUT_REORGANIZATION_SUMMARY.md` for complete pipeline documentation.
