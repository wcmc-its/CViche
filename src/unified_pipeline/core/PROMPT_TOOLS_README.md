# Prompt Logging and Analysis Tools

This directory contains tools for logging, analyzing, and testing LLM prompts used throughout the CV parsing pipeline.

## Overview

The prompt tooling system consists of three components:

1. **prompt_logger.py** - Captures exact prompts before every LLM call
2. **prompt_analyzer.py** - Analyzes logged prompts to find patterns
3. **prompt_ab_tester.py** - Tests prompt variations to improve results

## 1. Prompt Logger (prompt_logger.py)

### Purpose
Logs the EXACT prompt sent to the LLM before every API call, including:
- Complete messages array (system + user prompts)
- All API parameters (model, temperature, response_format, etc.)
- Metadata (timestamp, caller, purpose, context)

### Output Files
For each prompt, three files are created in `prompt_logs/`:
- **JSON**: `YYYY-MM-DD_HH-MM-SS_{purpose}_{log_id}.json` - Complete structured data
- **TXT**: `YYYY-MM-DD_HH-MM-SS_{purpose}_{log_id}_READABLE.txt` - Human-readable format
- **RESPONSE**: `YYYY-MM-DD_HH-MM-SS_{purpose}_{log_id}_RESPONSE.json` - API response with token usage

### Usage in Code
```python
from prompt_logger import log_prompt_before_call, log_prompt_response, get_caller_info

# Before API call
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt}
]

log_id = log_prompt_before_call(
    messages=messages,
    model="gpt-4o-mini",
    temperature=0.1,
    max_tokens=500,
    response_format=response_format,
    purpose="education_parsing",
    context={"cv_id": "2090", "section": "education"},
    caller_file=get_caller_info()
)

# Make API call
start_time = time.time()
response = client.chat.completions.create(...)
elapsed_time = time.time() - start_time

# Log response
log_prompt_response(log_id, response, "education_parsing", elapsed_time)
```

### Already Integrated In
- taxonomy_mapper.py
- personal_info_extractor.py
- publications_parser.py
- education_parser.py

## 2. Prompt Analyzer (prompt_analyzer.py)

### Purpose
Analyzes logged prompts to identify patterns in:
- Low-confidence results
- Parse errors and failures
- Token usage trends
- Prompt length vs. quality correlation
- Performance by purpose

### Usage

**Basic Analysis:**
```bash
python3 prompt_analyzer.py
```

**Filter by Purpose:**
```bash
python3 prompt_analyzer.py --purpose education_parsing
```

**Show Low-Confidence Cases:**
```bash
python3 prompt_analyzer.py --min-confidence 0.7
```

**Show Parse Errors:**
```bash
python3 prompt_analyzer.py --show-failures
```

**Save Report to File:**
```bash
python3 prompt_analyzer.py --output analysis_report.txt
```

**Combined Example:**
```bash
python3 prompt_analyzer.py \
    --purpose education_parsing \
    --min-confidence 0.7 \
    --show-failures \
    --output education_analysis.txt
```

### Output Report Sections

1. **Analysis by Purpose** - Count, avg character count, models used
2. **Token Usage Analysis** - Total tokens, avg per call, breakdown by purpose
3. **Prompt Length vs Quality** - Correlation between prompt size and confidence
4. **Low Confidence Cases** - Detailed list of results below threshold
5. **Parse Errors and Failures** - Grouped by error type

## 3. Prompt A/B Tester (prompt_ab_tester.py)

### Purpose
Tests prompt variations against logged prompts to:
- Compare different temperatures
- Test different models
- Try modified system prompts
- Evaluate additional instructions
- Find optimal configurations

### Usage

**Test Temperature Variations:**
```bash
python3 prompt_ab_tester.py --log-id abc123def456 --temperature 0.0,0.1,0.5
```

**Test Different Models:**
```bash
python3 prompt_ab_tester.py --log-id abc123def456 --models gpt-4o-mini,gpt-4o
```

**Test Modified System Prompt:**
```bash
python3 prompt_ab_tester.py --log-id abc123def456 --system-prompt-file new_system_prompt.txt
```

**Add Extra Instructions:**
```bash
python3 prompt_ab_tester.py \
    --log-id abc123def456 \
    --additional-instructions "Be more specific about dates and locations"
```

**Batch Test Low-Confidence Prompts:**
```bash
python3 prompt_ab_tester.py \
    --batch-low-confidence 0.7 \
    --temperature 0.0,0.3 \
    --max-tests 5
```

### Output

Each test produces:
- Live comparison showing confidence, tokens, and timing
- Best result identification
- Full results saved to `prompt_logs/ab_tests/ab_test_{id}.json`

Example output:
```
==================================================================================
A/B TEST: education_parsing
==================================================================================
Original Log ID: abc123def456
Variations: 2

Testing Original Prompt...
  ✅ Confidence: 0.725 | Tokens: 450 | Time: 1.23s

Testing Variation 1: temperature...
  ✅ Confidence: 0.850 | Tokens: 462 | Time: 1.31s

Testing Variation 2: temperature...
  ⚠️ Confidence: 0.680 | Tokens: 438 | Time: 1.18s

==================================================================================
COMPARISON
==================================================================================
✅ Variation 1     | Conf: 0.850     | Tokens:    462 | Temperature: 0.0
✅ Original        | Conf: 0.725     | Tokens:    450 | Temperature: 0.1
✅ Variation 2     | Conf: 0.680     | Tokens:    438 | Temperature: 0.5

🏆 Best Result: Variation 1
   Confidence: 0.850
   Variation: Temperature: 0.0
```

## Workflow Example

### 1. Run Pipeline (with prompt logging)
```bash
# Logging happens automatically when parsers are called
python3 cv_pipeline.py --cv-id 2090
```

### 2. Analyze Results
```bash
# Check for low-confidence education parsing
python3 prompt_analyzer.py \
    --purpose education_parsing \
    --min-confidence 0.7 \
    --show-failures \
    --output education_analysis.txt
```

### 3. Identify Problem Log IDs
From the analysis report, find log IDs with issues (e.g., `abc123def456`)

### 4. Test Variations
```bash
# Try lower temperature for more deterministic results
python3 prompt_ab_tester.py \
    --log-id abc123def456 \
    --temperature 0.0,0.05,0.1
```

### 5. Implement Winning Configuration
If temperature=0.0 performs better:
```python
# Update education_parser.py
response = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=messages,
    response_format=response_format,
    temperature=0.0,  # Changed from 0.1
    max_tokens=500
)
```

### 6. Batch Test on Historical Failures
```bash
# Test all low-confidence cases with new configuration
python3 prompt_ab_tester.py \
    --batch-low-confidence 0.7 \
    --temperature 0.0 \
    --max-tests 10
```

## Finding Log IDs

### Method 1: From Analysis Report
```bash
python3 prompt_analyzer.py --min-confidence 0.7 --output report.txt
# Look for "Log ID: xxx" in the report
```

### Method 2: Browse prompt_logs Directory
```bash
ls -lt prompt_logs/*.json | grep education_parsing | head -5
# Extract log_id from filename (12-char hash)
```

### Method 3: From Pipeline Logs
The pipeline prints:
```
📝 Prompt logged: 2025-11-04_14-27-40_education_parsing_abc123def456.json
```
The log_id is: `abc123def456`

## Environment Variables

**PROMPT_LOG_DIR** - Override default prompt_logs directory:
```bash
export PROMPT_LOG_DIR=/path/to/custom/logs
python3 prompt_analyzer.py
```

**OPENAI_API_KEY_WORK** - Required for A/B testing:
```bash
export OPENAI_API_KEY_WORK=sk-...
python3 prompt_ab_tester.py --log-id abc123
```

## Tips and Best Practices

### When to Use Each Tool

**prompt_analyzer.py:**
- After running a batch of CVs
- To identify systematic issues
- To measure overall pipeline quality
- Before making prompt changes (baseline)

**prompt_ab_tester.py:**
- When specific prompts fail consistently
- To validate prompt improvements
- To tune hyperparameters (temperature, max_tokens)
- To compare model performance

### Common A/B Test Scenarios

1. **Temperature Tuning:**
   ```bash
   --temperature 0.0,0.1,0.2,0.3,0.5
   ```
   Lower = more deterministic, Higher = more creative

2. **Model Comparison:**
   ```bash
   --models gpt-4o-mini,gpt-4o
   ```
   Test if more expensive model improves quality

3. **Prompt Refinement:**
   ```bash
   --additional-instructions "Focus on extracting complete date ranges"
   ```
   Add specific guidance for known failure modes

4. **Batch Testing:**
   ```bash
   --batch-low-confidence 0.7 --max-tests 20
   ```
   Test improvements across many cases

### Interpreting Results

**High Confidence (≥0.8):** Well-formed, complete extraction
**Medium Confidence (0.6-0.79):** Acceptable but may have minor issues
**Low Confidence (<0.6):** Incomplete or uncertain extraction - needs review

**Token Usage:** Higher tokens don't always mean better results. Look for efficiency.

**Elapsed Time:** Important for production. Optimize without sacrificing quality.

## Troubleshooting

**"No prompt logs found"**
- Run the pipeline first to generate logs
- Check PROMPT_LOG_DIR path
- Ensure parsers have prompt logging integrated

**"Error loading prompt log"**
- Verify log_id is correct (12-char hash)
- Check file isn't corrupted
- Try full filename instead of just ID

**"API rate limit exceeded"**
- Add delays between batch tests
- Reduce --max-tests value
- Check OpenAI API quota

## Integration Checklist

To add prompt logging to a new parser:

1. ✅ Import prompt logger:
   ```python
   from prompt_logger import log_prompt_before_call, log_prompt_response, get_caller_info
   ```

2. ✅ Log before API call:
   ```python
   log_id = log_prompt_before_call(
       messages=messages,
       model="gpt-4o-mini",
       temperature=0.1,
       purpose="your_parser_name",
       caller_file=get_caller_info()
   )
   ```

3. ✅ Time the API call:
   ```python
   start_time = time.time()
   response = client.chat.completions.create(...)
   elapsed_time = time.time() - start_time
   ```

4. ✅ Log response:
   ```python
   log_prompt_response(log_id, response, "your_parser_name", elapsed_time)
   ```

## Future Enhancements

Potential additions to this tooling:
- Automatic prompt optimization suggestions
- Cost analysis ($/call trends)
- Multi-run comparisons (track improvements over time)
- Integration with data quality dashboard
- Slack/email alerts for high failure rates
- Prompt version control and rollback
