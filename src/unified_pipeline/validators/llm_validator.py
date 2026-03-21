"""
GPT-5 Validator for CV Classification

Dual-use system for:
1. Validation - Quality check existing classifications
2. Production - Potentially replace current classifier if cost/accuracy meet thresholds

Phase 1: Run GPT-5 in parallel with current system to gather comparison data
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    print("WARNING: openai package not available. GPT-5 validation will be disabled.")


class GPT5Validator:
    """
    Post-classification validation and/or direct classification using GPT-5 API.

    Supports two modes:
    1. Validation mode: Compare GPT-5 classification against existing results
    2. Classification mode: Use GPT-5 as primary classifier
    """

    def __init__(
        self,
        model: str = "gpt-5.1",
        mode: str = "validation",  # "validation" or "classification"
        reasoning_effort: str = "low",  # "none", "low", "medium", "high" for GPT-5.1
        verbosity: str = "medium",  # "low", "medium", "high" for GPT-5.1
        max_output_tokens: int = 4000
    ):
        """
        Initialize GPT-5.1 validator.

        Args:
            model: OpenAI model to use (default: "gpt-5.1")
            mode: "validation" (compare with existing) or "classification" (direct use)
            reasoning_effort: GPT-5.1 reasoning level ("none", "low", "medium", "high")
            verbosity: GPT-5.1 output verbosity ("low", "medium", "high")
            max_output_tokens: Maximum tokens for response (GPT-5.1 parameter)
        """
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package required. Install with: pip install openai")

        self.model = model
        self.mode = mode
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.max_output_tokens = max_output_tokens

        # Initialize OpenAI client (uses OPENAI_API_KEY from environment)
        self.client = openai.OpenAI()

        # Track costs and usage
        self.total_cost = 0.0
        self.total_calls = 0
        self.total_tokens = 0

    def _extract_section_mappings(self, cv_analysis_text: str) -> Dict[str, Dict]:
        """
        Extract section IDs and create compact mappings.

        Returns a dict mapping section_id -> {label, classified_as, confidence}

        Example:
        {
            "G7": {"label": "Appointments at Hospitals", "classified_as": "K", "confidence": 0.85},
            "G9": {"label": "Education", "classified_as": "B", "confidence": 0.95}
        }
        """
        import re

        mappings = {}

        # Parse CV_ANALYSIS format to extract GROUP X (GY) patterns
        # Example: GROUP 7: G8
        # Label: Appointments at Hospitals/Affiliated Institutions
        # Mapped to: K (Educational Contributions)
        # Pass 1 Parent: K (conf: 0.85)

        pattern = r'GROUP \d+: (G\d+)\s*-+\s*Label: (.+?)\n.*?Mapped to: (.+?)\n.*?Pass 1 Parent: (.+?) \(conf: ([\d.]+)\)'

        for match in re.finditer(pattern, cv_analysis_text, re.DOTALL):
            section_id = match.group(1)  # e.g., "G8"
            label = match.group(2).strip()  # Full label
            mapped_to = match.group(3).strip()  # e.g., "K (Educational Contributions)"
            parent = match.group(4).strip()  # e.g., "K"
            confidence = float(match.group(5))

            # Extract just the code (e.g., "K" from "K (Educational Contributions)")
            code_match = re.match(r'^([A-Z]\d*)', mapped_to)
            code = code_match.group(1) if code_match else mapped_to

            mappings[section_id] = {
                'label': label[:100],  # Truncate long labels
                'classified_as': code,
                'confidence': confidence
            }

        return mappings

    def validate_cv_classification(
        self,
        cv_analysis_text: str,
        cv_id: str,
        current_classification: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Validate CV classification using GPT-5.

        Args:
            cv_analysis_text: Full CV analysis output (CV_ANALYSIS_XXXX.txt content)
            cv_id: CV identifier (e.g., "2061")
            current_classification: Optional current system's classification results

        Returns:
            {
                'cv_id': str,
                'misclassifications': List[Dict],
                'correct_classifications': List[Dict],
                'confusion_patterns': List[Dict],
                'missing_categories': List[str],
                'accuracy_estimate': float,
                'grade': str,
                'recommendations': List[str],
                'raw_response': str,
                'cost': float,
                'tokens_used': int,
                'comparison': Dict (if current_classification provided)
            }
        """

        # Extract section mappings for compact references
        section_mappings = self._extract_section_mappings(cv_analysis_text)

        # Build validation prompt with section mappings
        prompt = self._build_validation_prompt(cv_analysis_text, section_mappings)

        # Call API (use Responses API for GPT-5.1, Chat Completions for others)
        try:
            # Check if using GPT-5.1 (which supports Responses API)
            is_gpt5 = 'gpt-5' in self.model.lower()

            if is_gpt5:
                # Use Responses API for GPT-5.1
                full_input = f"{self._get_system_prompt_validation()}\n\n{prompt}"

                response = self.client.responses.create(
                    model=self.model,
                    input=full_input,
                    reasoning={"effort": self.reasoning_effort},
                    text={"verbosity": self.verbosity},
                    max_output_tokens=self.max_output_tokens,
                    response_format={"type": "json_object"}
                )
            else:
                # Use Chat Completions API for gpt-4o and other models
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt_validation()},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,  # Low temperature for consistency
                    max_tokens=self.max_output_tokens,
                    response_format={"type": "json_object"}
                )

        except Exception as e:
            return {
                'error': str(e),
                'cv_id': cv_id,
                'status': 'failed',
                'cost': 0.0,
                'tokens_used': 0
            }

        # Parse response (handles both API formats)
        result = self._parse_validation_response(response, cv_id, is_gpt5=is_gpt5)

        # Calculate cost (handles both API formats)
        result['cost'] = self._calculate_cost(response.usage)
        result['tokens_used'] = response.usage.total_tokens

        # Track cumulative stats
        self.total_cost += result['cost']
        self.total_calls += 1
        self.total_tokens += result['tokens_used']

        # If current classification provided, add comparison
        if current_classification:
            result['comparison'] = self._compare_with_current(
                result,
                current_classification
            )

        return result

    def classify_with_gpt5(
        self,
        section_label: str,
        sample_entries: List[str],
        hierarchical_context: Optional[Dict] = None,
        full_section_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Direct classification using GPT-5 (single-pass).

        Alternative to two-pass classification system.

        Args:
            section_label: Section heading/label
            sample_entries: 3-5 sample entries from section
            hierarchical_context: Optional context (level, parent classification, etc.)
            full_section_text: Optional full section content (if available)

        Returns:
            {
                'section_id': str,  # e.g., "D1" for Academic Positions
                'section_title': str,
                'confidence': float,
                'reasoning': str,
                'alternatives': List[Dict],  # Alternative classifications with scores
                'cost': float,
                'tokens_used': int
            }
        """

        # Build classification prompt
        prompt = self._build_classification_prompt(
            section_label,
            sample_entries,
            hierarchical_context,
            full_section_text
        )

        # Call API (use Responses API for GPT-5.1, Chat Completions for others)
        try:
            is_gpt5 = 'gpt-5' in self.model.lower()

            if is_gpt5:
                # Use Responses API for GPT-5.1
                full_input = f"{self._get_system_prompt_classification()}\n\n{prompt}"

                response = self.client.responses.create(
                    model=self.model,
                    input=full_input,
                    reasoning={"effort": self.reasoning_effort},
                    text={"verbosity": "low"},
                    max_output_tokens=500,
                    response_format={"type": "json_object"}
                )
            else:
                # Use Chat Completions API for gpt-4o and other models
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt_classification()},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=500,
                    response_format={"type": "json_object"}
                )

        except Exception as e:
            return {
                'error': str(e),
                'section_label': section_label,
                'status': 'failed',
                'cost': 0.0,
                'tokens_used': 0
            }

        # Parse response (handles both API formats)
        result = self._parse_classification_response(response, is_gpt5=is_gpt5)

        # Calculate cost
        result['cost'] = self._calculate_cost(response.usage)
        result['tokens_used'] = response.usage.total_tokens

        # Track stats
        self.total_cost += result['cost']
        self.total_calls += 1
        self.total_tokens += result['tokens_used']

        return result

    def _get_system_prompt_validation(self) -> str:
        """System prompt for validation mode."""
        return """You are an expert CV taxonomy validator with deep knowledge of academic career documentation and classification systems.

Your task is to systematically analyze CV classification results and identify errors, patterns, and improvement opportunities.

You must return a structured JSON object with your analysis. Be thorough, specific, and prioritize by severity and frequency."""

    def _get_system_prompt_classification(self) -> str:
        """System prompt for direct classification mode."""
        return """You are an expert CV taxonomist specializing in the WCM (Weill Cornell Medicine) academic CV taxonomy.

Your task is to classify CV sections into the most specific appropriate taxonomy code.

You must return a structured JSON object with the classification, confidence score, reasoning, and alternatives."""

    def _build_validation_prompt(self, cv_analysis_text: str, section_mappings: Dict) -> str:
        """Build comprehensive validation prompt with compact section references."""

        taxonomy_ref = self._get_taxonomy_reference()
        known_confusions = self._get_known_confusion_areas()
        recent_fixes = self._get_recent_fixes()

        # Build compact section reference table
        section_ref = "## Section Reference (Use These IDs)\n\n"
        section_ref += "| ID | Label (truncated) | Classified As | Conf |\n"
        section_ref += "|----|-------------------|---------------|------|\n"
        for sid, info in sorted(section_mappings.items()):
            section_ref += f"| {sid} | {info['label'][:50]} | {info['classified_as']} | {info['confidence']:.2f} |\n"

        prompt = f"""# Task: Validate CV Classification

Analyze this CV's classification results and identify all errors.

{section_ref}

## Taxonomy Reference

{taxonomy_ref}

## Known Confusion Areas to Watch For

{known_confusions}

## Recent Fixes to Validate

{recent_fixes}

---

## Output Format

**IMPORTANT**: Use section IDs (e.g., "G7", "G9") instead of full labels to minimize tokens.

Return a JSON object with this exact structure:

{{
  "misclassifications": [
    {{
      "section_id": "G7",  // Use compact ID from table above
      "classified_as": "K",
      "should_be": "D",
      "confidence": 0.85,
      "why_wrong": "Brief explanation (1 sentence)",
      "severity": "HIGH|MEDIUM|LOW"
    }}
  ],
  "correct_classifications": [
    {{
      "section_id": "G9",  // Use compact ID
      "classified_as": "B",
      "confidence": 0.95
    }}
  ],
  "confusion_patterns": [
    {{
      "pattern": "Brief description",  // Keep concise
      "sections": "K↔D",  // Compact format
      "freq": "high|med|low",  // Abbreviated
      "terms": ["term1", "term2"]  // Key terms only
    }}
  ],
  "missing_categories": ["category1", "category2"],  // Array of strings only
  "accuracy": 0.XX,  // Abbreviated field name
  "grade": "A|B|C|D|F",
  "recs": ["rec1", "rec2", "rec3"]  // Abbreviated, max 3
}}

**Token optimization**: Use IDs, abbreviate field names, keep explanations to 1 sentence max.
Analyze EVERY section ID from the table above."""

        return prompt

    def _build_classification_prompt(
        self,
        section_label: str,
        sample_entries: List[str],
        hierarchical_context: Optional[Dict],
        full_section_text: Optional[str]
    ) -> str:
        """Build single-pass classification prompt."""

        taxonomy_ref = self._get_taxonomy_reference()

        # Format sample entries
        entries_text = "\n".join([f"- {entry[:200]}" for entry in sample_entries[:5]])

        # Format context if provided
        context_text = ""
        if hierarchical_context:
            context_text = f"""
## Hierarchical Context
- Level: {hierarchical_context.get('level', 'unknown')}
- Parent Section: {hierarchical_context.get('parent_section', 'none')}
- Position in CV: {hierarchical_context.get('position', 'unknown')}
"""

        prompt = f"""# Task: Classify CV Section

Classify this section to the most specific appropriate WCM taxonomy code.

## Section Label
{section_label}

## Sample Entries
{entries_text}

{context_text}

## Taxonomy Reference
{taxonomy_ref}

---

## Output Format

Return a JSON object:

{{
  "section_id": "X1",  // Most specific code (e.g., "D1", "S1", etc.)
  "section_title": "Full category name",
  "confidence": 0.XX,  // 0.0-1.0
  "reasoning": "Brief explanation of why this classification is correct",
  "alternatives": [
    {{"section_id": "Y2", "confidence": 0.XX, "reason": "Why this was considered"}}
  ]
}}

Be specific and provide your confidence score."""

        return prompt

    def _get_taxonomy_reference(self) -> str:
        """Simplified taxonomy reference for prompts."""

        return """**WCM Academic CV Taxonomy (Simplified)**

**A - Personal Data**: Name, contact, ORCID, identifiers

**B - Education**: Formal degrees
- B1: Undergraduate (BA, BS)
- B2: Graduate (MA, MS, PhD, MD, ScD, DO)

**C - Postdoctoral Training**: As trainee
- C1: Research postdocs
- C2: Residency
- C3: Clinical fellowships

**D - Professional Positions**: Faculty appointments, employment
- D1: Academic (Assistant/Associate/Full Professor)
- D2: Clinical (Attending, Clinical Director)

**G - Hospital/Institutional Affiliations**: Staff appointments, privileges

**H - Honors and Awards**: Symbolic recognition, competitive honors
- NOT funding grants (M2)
- NOT membership status (I)

**I - Professional Organizations**: Society memberships

**K - Educational Contributions**: Teaching activities
- K1: Course teaching
- K2: Clinical teaching
- K3: Educational leadership
- K4: Educational materials

**L - Patient Care / Clinical Service**: Direct patient care

**M - Research Activities**:
- M1: Research positions/roles
- M2: Research funding (grants with PI role, budget, numbers)
  - M2A: Current/active funding
  - M2B: Past/completed funding
  - M2C: Pending/submitted funding
  - M2D: Patents & Inventions
- NOTE: Clinical trials are classified as M2A/M2B/M2C based on status (like grants)

**N - Mentoring**: Named mentees with outcomes

**O - Institutional Leadership**: Administrative roles, committees, governance

**Q - Professional Service**:
- Q1: External leadership (boards, society officers)
- Q2: Journal editorial, peer review
- Q3: Grant/study section review
- Q4: Community/public service

**R - Invited Presentations**: Invited talks, keynotes, named lectures (external)

**S - Bibliography**: Publications
- S1: Published peer-reviewed articles (volume/pages/DOI)
- S2: Reviews, editorials
- S3: Books
- S4: Book chapters
- S5: Conference proceedings
- S6: Abstracts
- S7: Unpublished (submitted, in review, in prep, preprints)
- S8: Presentations (conference talks/posters - not invited)
- S9: Media/public scholarship
- S10: Datasets
- S11: Software/code
- S12: Other scholarly

**T - Other**: Miscellaneous content

**META_STRUCTURAL**: Document metadata (headers, "CV" title, dates)"""

    def _get_known_confusion_areas(self) -> str:
        """Known confusion patterns to watch for."""

        return """**Common Confusion Patterns**:

1. **H ↔ M2**: Awards vs Funding - Grant number/budget → M2, symbolic honor → H
2. **H ↔ I**: Honor vs Membership - One-time recognition → H, ongoing status → I
3. **D ↔ O**: Position vs Leadership - Faculty job → D, administrative authority → O
4. **K ↔ R**: Teaching vs Invited Talks - Students/curriculum → K, professional presentation → R
5. **S1 ↔ S7**: Published vs Unpublished - Volume/pages/DOI → S1, "submitted"/"in review" → S7
6. **D ↔ K**: Hospital appointments - Employment signals (Staff, FTE) → D, teaching signals → K
7. **C ↔ D**: Training vs Position - Trainee role → C, faculty/staff role → D
8. **N ↔ K**: Mentoring vs Teaching - Named mentees with outcomes → N, generic students → K"""

    def _get_recent_fixes(self) -> str:
        """Recent fixes that should be working."""

        return """**Recent Fixes Applied** (validate these are working):

1. **Metadata Filter**: Document headers, "CV", "Date of preparation" → META_STRUCTURAL
2. **S1/S7 Disambiguation**: Published articles (volume/pages/publisher DOI) → S1, submissions/preprints → S7
3. **Mixed Content Sections**: Sections with multiple types (e.g., BA+PhD+postdoc) → Parent category
4. **Hospital Appointments**: "Appointments at Hospitals" with Staff Nurse, Informatician → D (Positions), not K"""

    def _parse_validation_response(self, response, cv_id: str, is_gpt5: bool = False) -> Dict[str, Any]:
        """Parse validation JSON response from either Responses API (GPT-5) or Chat Completions API."""

        try:
            # Get content based on API type
            if is_gpt5:
                # Responses API returns output_text
                content = response.output_text
            else:
                # Chat Completions API returns choices[0].message.content
                content = response.choices[0].message.content

            parsed = json.loads(content)

            # Add metadata
            parsed['cv_id'] = cv_id
            parsed['raw_response'] = content
            parsed['timestamp'] = datetime.now().isoformat()

            return parsed
        except json.JSONDecodeError as e:
            # Fallback if JSON parsing fails
            if is_gpt5:
                raw = response.output_text if hasattr(response, 'output_text') else str(response)
            else:
                raw = response.choices[0].message.content if hasattr(response, 'choices') else str(response)

            return {
                'cv_id': cv_id,
                'error': f'JSON parse error: {str(e)}',
                'raw_response': raw,
                'status': 'parse_failed',
                'cost': 0.0,
                'tokens_used': 0
            }

    def _parse_classification_response(self, response, is_gpt5: bool = False) -> Dict[str, Any]:
        """Parse classification JSON response from either Responses API or Chat Completions API."""

        try:
            # Get content based on API type
            if is_gpt5:
                content = response.output_text
            else:
                content = response.choices[0].message.content

            parsed = json.loads(content)
            parsed['timestamp'] = datetime.now().isoformat()
            return parsed
        except json.JSONDecodeError as e:
            if is_gpt5:
                raw = response.output_text if hasattr(response, 'output_text') else str(response)
            else:
                raw = response.choices[0].message.content if hasattr(response, 'choices') else str(response)

            return {
                'error': f'JSON parse error: {str(e)}',
                'raw_response': raw,
                'status': 'parse_failed',
                'cost': 0.0,
                'tokens_used': 0
            }

    def _calculate_cost(self, usage) -> float:
        """
        Calculate API cost based on token usage.

        Uses approximate GPT-4o pricing (will update when GPT-5 pricing available):
        - Input: $2.50 per 1M tokens
        - Output: $10.00 per 1M tokens
        """

        # GPT-4o pricing (placeholder for GPT-5)
        input_cost_per_1m = 2.50
        output_cost_per_1m = 10.00

        input_cost = (usage.prompt_tokens / 1_000_000) * input_cost_per_1m
        output_cost = (usage.completion_tokens / 1_000_000) * output_cost_per_1m

        return input_cost + output_cost

    def _compare_with_current(
        self,
        gpt5_result: Dict,
        current_classification: Dict
    ) -> Dict[str, Any]:
        """
        Compare GPT-5 results with current system.

        Returns comparison metrics and discrepancies.
        """

        # Count agreements and disagreements
        agreements = []
        disagreements = []

        # This is simplified - would need to map current_classification format
        # to GPT-5's misclassifications list

        return {
            'agreement_rate': 0.0,  # Placeholder
            'agreements': agreements,
            'disagreements': disagreements,
            'gpt5_accuracy_estimate': gpt5_result.get('accuracy_estimate', 0.0),
            'comparison_summary': "Comparison implementation pending"
        }

    def save_validation_report(
        self,
        validation_result: Dict,
        output_path: str
    ):
        """Save validation report as markdown."""

        report = self._format_validation_report(validation_result)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(report)

    def _format_validation_report(self, result: Dict) -> str:
        """Format validation result as readable markdown."""

        if 'error' in result:
            return f"""# Validation Report: CV {result['cv_id']}

**Status**: ERROR
**Error**: {result['error']}
**Date**: {result.get('timestamp', 'unknown')}
"""

        md = f"""# Validation Report: CV {result['cv_id']}

**Date**: {result.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}
**Accuracy Estimate**: {result.get('accuracy_estimate', 0.0):.1%}
**Grade**: {result.get('grade', 'N/A')}
**Validation Cost**: ${result.get('cost', 0.0):.4f}
**Tokens Used**: {result.get('tokens_used', 0):,}

---

## Misclassifications Found

"""

        misclassifications = result.get('misclassifications', [])
        if misclassifications:
            for error in misclassifications:
                md += f"""### {error.get('group_id', 'Unknown')}: {error.get('label', 'Unknown')}

- **Classified as**: {error.get('classified_as', 'Unknown')} (conf: {error.get('confidence', 0.0)})
- **Should be**: {error.get('should_be', 'Unknown')}
- **Why wrong**: {error.get('why_wrong', 'No explanation')}
- **Severity**: {error.get('severity', 'UNKNOWN')}

"""
        else:
            md += "*No misclassifications found.*\n\n"

        md += """---

## Correct Classifications (Sample)

"""

        correct = result.get('correct_classifications', [])
        for item in correct[:10]:
            md += f"- ✅ **{item.get('group_id', '?')}**: {item.get('label', '?')} → {item.get('classified_as', '?')} (conf: {item.get('confidence', 0.0)})\n"

        if not correct:
            md += "*No correct classifications documented.*\n"

        md += """

---

## Confusion Patterns Discovered

"""

        patterns = result.get('confusion_patterns', [])
        if patterns:
            for pattern in patterns:
                md += f"""### {pattern.get('pattern', 'Unknown pattern')}

- **Sections confused**: {pattern.get('sections_confused', 'Unknown')}
- **Frequency**: {pattern.get('frequency_estimate', 'Unknown')}
- **Ambiguous terms**: {', '.join(pattern.get('ambiguous_terms', []))}
- **How to fix**: {pattern.get('disambiguation_criteria', 'No criteria provided')}

"""
        else:
            md += "*No new confusion patterns identified.*\n\n"

        md += """---

## Missing Categories

"""

        missing = result.get('missing_categories', [])
        if missing:
            for category in missing:
                md += f"- {category}\n"
        else:
            md += "*No missing categories identified.*\n"

        md += """

---

## Recommendations

"""

        recommendations = result.get('recommendations', [])
        for i, rec in enumerate(recommendations, 1):
            md += f"{i}. {rec}\n"

        if not recommendations:
            md += "*No specific recommendations.*\n"

        md += f"""

---

**Validation powered by**: {self.model}
**Total cost**: ${result.get('cost', 0.0):.4f}
**Total tokens**: {result.get('tokens_used', 0):,}
"""

        return md

    def get_statistics(self) -> Dict[str, Any]:
        """Get cumulative usage statistics."""

        return {
            'total_calls': self.total_calls,
            'total_cost_usd': self.total_cost,
            'total_tokens': self.total_tokens,
            'avg_cost_per_call': self.total_cost / max(self.total_calls, 1),
            'avg_tokens_per_call': self.total_tokens / max(self.total_calls, 1),
            'model': self.model,
            'mode': self.mode
        }
