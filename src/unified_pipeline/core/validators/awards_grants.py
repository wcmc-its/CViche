"""
Validator for disambiguating Awards between Grants (Research Support) and Honors.

Implements deterministic cascade to classify any "award" entry as either:
- M2 (Research Support): Grants, contracts, funding with aims/budget/role
- H (Honors & Awards): Recognition, prizes, fellowships-as-titles

No REVIEW fallback - everything gets classified.
"""

import re
from .base_validator import BaseValidator, ValidatorGuidance


class AwardsGrantsValidator(BaseValidator):
    """
    Disambiguates awards between grants (M2) and honors (H).

    Uses a deterministic cascade with hard overrides, strong signals,
    and context fallbacks to ensure every award gets classified.
    """

    name = "AwardsGrantsValidator"

    def applies_to(self) -> list[str]:
        """This validator helps distinguish between M (Research) and H (Honors)."""
        return ['research', 'honors_awards']

    def priority(self) -> int:
        """Medium priority - runs after specialized validators."""
        return 50

    def analyze(self, entry_text: str) -> ValidatorGuidance | None:
        """Per-entry analysis - use analyze_section for better results."""
        # This validator is designed for section-level analysis
        # Return None for per-entry calls
        return None

    # =========================================================================
    # REGEX PATTERNS - Core detection signals
    # =========================================================================

    # GRANT signals
    ROLE = re.compile(
        r'\b(contact\s+pi|site\s+pi|co-?pi|principal\s+investigator|'
        r'project\s+director|pd|co-?investigator|subcontract\s+pi|multi-?pi)\b',
        re.I
    )

    MONEY = re.compile(
        r'(\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\b|'
        r'(?:amount|budget|annual|total|direct\s+costs|subaward)',
        re.I
    )

    MECHANISM = re.compile(
        r'\b(?:[RUPKTFZDP]\d{1,3}[A-Z]{0,3}|OT3|HHSN\d+)\b',
        re.I
    )

    AIMS = re.compile(
        r'\b(goal|aims?|objective)s?\s*:',
        re.I
    )

    DATES = re.compile(
        r'\b(?:\d{4}\s*[–-]\s*\d{2,4}|'
        r'\d{1,2}/\d{1,2}/\d{2,4}\s*(?:–|-|to)\s*\d{1,2}/\d{1,2}/\d{2,4})\b'
    )

    CONTRACT = re.compile(
        r'\bcontract|task\s*order|procurement\b',
        re.I
    )

    AGENCY = re.compile(
        r'\b(nih|nsf|dod|darpa|nlm|ncats|nci|nhgri|cdc|fda|onc|'
        r'gates\s+foundation|wellcome|ahrq|usda|leidos|cdmrp)\b',
        re.I
    )

    TRAINING_FELLOWSHIP = re.compile(
        r'\bF3[12]\b',
        re.I
    )

    # HONOR signals
    FELLOW_TITLE = re.compile(
        r'\b(fellow\s+of|fellow,|elected\s+fellow|inducted)\b',
        re.I
    )

    HONOR_WORDS = re.compile(
        r'\b(finalist|nominee|nominated|recipient|awardee|distinguished|'
        r'excellence|outstanding|merit|medal|prize|laureate)\b',
        re.I
    )

    AWARD_WORD = re.compile(
        r'\b(award|fellow(ship)?)\b',
        re.I
    )

    def analyze_section(
        self,
        section_label: str,
        entries: list[str]
    ) -> ValidatorGuidance | None:
        """
        Analyze a section that might contain awards/grants/honors.

        Uses deterministic cascade to classify each entry and provide
        guidance on which parent section it belongs to.
        """
        if not entries:
            return None

        # Handle both dict entries (from entry objects) and strings
        entry_texts = []
        for entry in entries:
            if isinstance(entry, dict):
                text = entry.get('text_snippet', '')
            else:
                text = entry
            if text:  # Skip empty entries
                entry_texts.append(text)

        if not entry_texts:
            return None

        # Quick check: does this section contain award-like content?
        section_text = ' '.join(entry_texts).lower()
        has_award_content = bool(
            re.search(r'\b(award|grant|fellowship|prize|honor|funding|support)\b', section_text)
        )

        if not has_award_content:
            return None

        # Classify each entry
        grant_count = 0
        honor_count = 0
        mixed = False

        classifications = []
        for entry_text in entry_texts[:10]:  # Sample first 10 for performance
            classification, reason = self._classify_award_entry(entry_text, section_label)
            classifications.append((classification, reason))

            if classification == "GRANT":
                grant_count += 1
            elif classification == "HONOR":
                honor_count += 1

        # If we have both types, it's mixed content
        if grant_count > 0 and honor_count > 0:
            mixed = True

        # Build guidance
        if mixed:
            return ValidatorGuidance(
                recommend_sections=['research', 'honors_awards'],
                exclude_sections=[],
                hints=[
                    f"⚠️ MIXED CONTENT DETECTED: {grant_count} grant entries, {honor_count} honor entries",
                    "GRANTS (→ M2 Research Support) should have:",
                    "  • Role terms (PI, Co-PI, Co-I, PD, Project Director)",
                    "  • Budget/dollar amounts",
                    "  • Mechanism codes (R01, U24, F32, K99, etc.)",
                    "  • Project aims/goals/objectives",
                    "  • Funding agency (NIH, NSF, Gates, etc.)",
                    "",
                    "HONORS (→ H) should have:",
                    "  • Recognition words (finalist, nominee, distinguished, excellence)",
                    "  • Society titles (Fellow of AMIA, Elected Fellow, etc.)",
                    "  • Awards without project aims/budget/role",
                    "",
                    "⚠️ This section should likely be split into separate groups"
                ],
                confidence=0.85,
                deterministic_signals=[self.name]
            )

        elif grant_count > 0:
            # All grants
            examples = [reason for cls, reason in classifications[:3] if cls == "GRANT"]
            return ValidatorGuidance(
                recommend_sections=['research'],
                exclude_sections=['honors_awards'],
                hints=[
                    f"✓ {grant_count} GRANT entries detected → M2 (Research Support)",
                    "Key grant signals found:",
                ] + [f"  • {ex}" for ex in examples],
                confidence=0.90,
                deterministic_signals=[self.name]
            )

        elif honor_count > 0:
            # All honors
            examples = [reason for cls, reason in classifications[:3] if cls == "HONOR"]
            return ValidatorGuidance(
                recommend_sections=['honors_awards'],
                exclude_sections=['research'],
                hints=[
                    f"✓ {honor_count} HONOR entries detected → H (Honors & Awards)",
                    "Key honor signals found:",
                ] + [f"  • {ex}" for ex in examples],
                confidence=0.90,
                deterministic_signals=[self.name]
            )

        return None

    def _classify_award_entry(
        self,
        text: str,
        section_hint: str | None = None
    ) -> tuple:
        """
        Classify a single entry as GRANT or HONOR using deterministic cascade.

        Returns:
            (classification, reason): ("GRANT"|"HONOR", explanation string)
        """
        t = ' '.join(text.strip().split())

        # =====================================================================
        # 1) HARD OVERRIDES - Stop immediately if matched
        # =====================================================================

        if self.FELLOW_TITLE.search(t):
            return ("HONOR", "Fellow title (Fellow of..., Elected Fellow, etc.)")

        if self.MECHANISM.search(t):
            return ("GRANT", "NIH/agency mechanism code detected")

        if self.ROLE.search(t) and self.MONEY.search(t):
            return ("GRANT", "Role + Budget (PI/Co-I + dollar amount)")

        # =====================================================================
        # 2) STRONG GRANT SIGNALS
        # =====================================================================

        has_role = self.ROLE.search(t)
        has_money = self.MONEY.search(t)
        has_dates = self.DATES.search(t)
        has_aims = self.AIMS.search(t)
        has_contract = self.CONTRACT.search(t)
        has_training_f = self.TRAINING_FELLOWSHIP.search(t)

        if has_role and (has_dates or has_aims):
            return ("GRANT", "Role + (Dates or Aims)")

        if has_money and (has_dates or has_aims):
            return ("GRANT", "Budget + (Dates or Aims)")

        if has_contract:
            return ("GRANT", "Contract/procurement language")

        if has_training_f and (has_aims or has_dates):
            return ("GRANT", "NIH training fellowship (F31/F32) with aims/dates")

        # =====================================================================
        # 3) STRONG HONOR SIGNALS
        # =====================================================================

        has_honor_words = self.HONOR_WORDS.search(t)

        # Check if NO grant cues present
        no_grant_cues = not any([
            has_role, has_money, has_aims, has_dates,
            self.MECHANISM.search(t), has_contract
        ])

        if has_honor_words and no_grant_cues:
            return ("HONOR", "Recognition words (finalist/nominee/distinguished) without grant cues")

        # Bare award title without grant cues
        if self.AWARD_WORD.search(t) and no_grant_cues:
            return ("HONOR", "Award title without role/budget/aims/dates")

        # =====================================================================
        # 4) MEDIUM SIGNALS WITH TIE-BREAKERS
        # =====================================================================

        grant_cue_count = sum([
            bool(has_role),
            bool(has_money),
            bool(self.MECHANISM.search(t)),
            bool(has_aims),
            bool(has_dates),
            bool(has_contract),
            bool(self.AGENCY.search(t))
        ])

        if grant_cue_count >= 1:
            return ("GRANT", f"{grant_cue_count} grant signal(s) detected")

        if self.AWARD_WORD.search(t):
            return ("HONOR", "Titled award without grant signals")

        # =====================================================================
        # 5) CONTEXT FALLBACKS
        # =====================================================================

        if section_hint:
            hint_lower = section_hint.lower()
            if any(word in hint_lower for word in ['grant', 'support', 'funding', 'contract']):
                return ("GRANT", "Section context: Grants/Support/Funding")
            if any(word in hint_lower for word in ['honor', 'award', 'recognition']):
                return ("HONOR", "Section context: Honors/Awards/Recognition")

        # =====================================================================
        # FINAL DEFAULT (minimizes false grant positives)
        # =====================================================================

        return ("HONOR", "Default: insufficient grant signals")
