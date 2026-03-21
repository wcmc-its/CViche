"""
Disambiguation Validators for CV Taxonomy Classification

These validators detect and flag common confusion patterns in CV classification,
helping to reduce misclassifications between ambiguous categories.

Based on DISAMBIGUATION_GUIDE.md confusion areas.
"""

import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass


@dataclass
class ValidationFlag:
    """A flag raised by a validator indicating a potential classification issue."""
    severity: str  # 'info', 'warning', 'error'
    confusion_type: str  # e.g., 'H_I_FELLOWSHIP', 'H_M2_CAREER_AWARD'
    message: str
    suggestion: str
    alternative_sections: List[str]  # Suggested alternative section IDs
    key_question: str  # The key question to resolve the ambiguity


class FellowAmbiguityValidator:
    """
    Detects when 'Fellow' entries may be misclassified.

    Handles:
    - Confusion #1: H (Honors) ↔ I (Professional Orgs)
    - Confusion #9: C (Postdoc) ↔ B (Education) ↔ D (Positions) ↔ N (Mentoring)
    """

    TRAINING_FELLOW_KEYWORDS = [
        'postdoctoral fellow', 'postdoc', 'research fellow', 'clinical fellow',
        'resident', 'intern', 'house officer', 'visiting fellow', 'irta fellow',
        'instructor', 'lecturer', 'clinical instructor'
    ]

    MENTEE_CONTEXT_KEYWORDS = [
        'supervised by', 'mentor:', 'mentee:', 'advisee:', 'trainee:',
        'thesis:', 'dissertation:', 'current position:', 'now at'
    ]

    # Common fellowship abbreviations (professional societies/boards)
    # These suggest ongoing membership (I) rather than one-time honor (H)
    FELLOWSHIP_ABBREVIATIONS = [
        # U.S. Medical Fellowships
        'FACP', 'FAAP', 'FAAFP', 'FACOG', 'FACS', 'FACR', 'FACC', 'FAHA',
        'FASN', 'FAPA', 'FASCO', 'FAAN', 'FAANP', 'FNAS', 'FCAP',
        # U.K. Royal Colleges
        'FRCP', 'FRCS', 'FRCA', 'FRCOG', 'FRCPCH', 'FRCOphth', 'FRCR',
        'FRCPath', 'FRCGP', 'FRCEM',
        # Canada
        'FRCPC', 'FRCSC',
        # Australia/New Zealand
        'FRACP', 'FRACS', 'FRACGP', 'FACEM', 'FANZCA', 'FRANZCP', 'FRANZCO',
        # Common variants
        'FRCP(Edin)', 'FRCP(Lon)', 'FRCS(Ed)', 'FRCS(Eng)'
    ]

    # Honor society induction keywords (signals H not I)
    # These distinguish honor society inductions (one-time recognition)
    # from ongoing professional society membership
    HONOR_SOCIETY_KEYWORDS = [
        'inducted', 'induction into', 'honor society', 'honorary society',
        'elected to', 'selection to'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if Fellow entry might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Extract entry text for analysis
        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check if "fellow" or related training role appears, or honor society keywords
        is_fellow = 'fellow' in label
        is_training_role = any(kw in label or kw in entry_text
                              for kw in FellowAmbiguityValidator.TRAINING_FELLOW_KEYWORDS)
        has_honor_society_keyword = any(kw in entry_text for kw in FellowAmbiguityValidator.HONOR_SOCIETY_KEYWORDS)

        # Only proceed if relevant to H ↔ I confusion or training role confusion
        if parent_id in ['H', 'I']:
            # For H/I sections, check for fellow/honor society keywords
            if not (is_fellow or has_honor_society_keyword):
                return None
        elif not (is_fellow or is_training_role):
            # For other sections, check for fellow/training role
            return None

        # Signals for different classifications
        has_dollar = bool(re.search(r'\$[\d,]+', entry_text))
        has_pi_role = bool(re.search(r'\b(pi|principal investigator|co-pi|co-investigator)\b', entry_text))
        has_since = bool(re.search(r'\bsince\s+\d{4}\b', entry_text))
        has_current = bool(re.search(r'\bcurrent\s+fellow\b', entry_text))
        has_honorary = bool(re.search(r'\b(honorary|elected|distinguished)\b', entry_text))

        # Enhanced mentee context detection
        has_mentee_context = any(kw in entry_text for kw in FellowAmbiguityValidator.MENTEE_CONTEXT_KEYWORDS)

        # Check for person name at start (suggests this is about someone else)
        has_person_name = bool(re.search(r'^[A-Z][a-z]+\s+[A-Z][a-z]+', ' '.join(str(e) for e in entries)))

        # Check for mentor attribution (suggests this is the CV owner's training)
        has_mentor_attribution = bool(re.search(r'\b(mentor|advisor|supervisor|pi):\s*[A-Z]', ' '.join(str(e) for e in entries)))

        # Check for institutional appointment language (suggests D - Position)
        has_appointment_language = bool(re.search(r'\b(appointed|appointment|position|rank)\b', entry_text))

        # Check for training completion language (suggests C - Training)
        has_training_completion = bool(re.search(r'\b(completed|graduated|trained|training period)\b', entry_text))

        # Check for fellowship abbreviations (suggests I - membership)
        has_fellowship_abbrev = any(abbrev in ' '.join(str(e) for e in entries)
                                   for abbrev in FellowAmbiguityValidator.FELLOWSHIP_ABBREVIATIONS)

        # Decision logic

        # Case 1: H ↔ I confusion (Fellowship as honor vs membership)
        if parent_id in ['H', 'I']:
            # Sub-case 1a: Fellowship abbreviation (strong signal for I)
            if has_fellowship_abbrev and parent_id == 'H':
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_I_FELLOWSHIP',
                    message=f'Fellow entry with abbreviation (FACP/FRCP/etc.) classified as H (Honor)',
                    suggestion='Fellowship abbreviations usually indicate ongoing membership status (→ I) rather than one-time honor (H)',
                    alternative_sections=['I'],
                    key_question='Is this describing ongoing membership status or a one-time recognition?'
                )

            # Sub-case 1a-new: Honor society induction (strong signal for H)
            if has_honor_society_keyword and parent_id == 'I':
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_I_FELLOWSHIP',
                    message=f'Entry with honor society induction language classified as I (Professional Org)',
                    suggestion='Honor society inductions (inducted/elected to honor society) are one-time recognitions (→ H) rather than ongoing membership (I)',
                    alternative_sections=['H'],
                    key_question='Is this describing ongoing membership status or a one-time recognition?'
                )

            # Sub-case 1b: Ongoing membership signals ("since", "current")
            if has_since or has_current:
                # Strong signal for I (Professional Org)
                if parent_id == 'H':
                    return ValidationFlag(
                        severity='warning',
                        confusion_type='H_I_FELLOWSHIP',
                        message=f'Fellow entry classified as H (Honor) but has ongoing membership signals',
                        suggestion='Check if this is ongoing society membership status (→ I) rather than one-time honor (H)',
                        alternative_sections=['I'],
                        key_question='Is this describing ongoing membership status or a one-time recognition?'
                    )

            # Sub-case 1c: Honorary/elected without ongoing signals (favor H)
            elif has_honorary and not (has_since or has_current or has_fellowship_abbrev):
                # Strong signal for H (Honor)
                if parent_id == 'I':
                    return ValidationFlag(
                        severity='warning',
                        confusion_type='H_I_FELLOWSHIP',
                        message=f'Fellow entry classified as I (Professional Org) but has honor/award signals',
                        suggestion='Check if this is a one-time honor/recognition (→ H) rather than membership (I)',
                        alternative_sections=['H'],
                        key_question='Is this describing ongoing membership status or a one-time recognition?'
                    )

        # Case 2: H ↔ M2 confusion (Fellowship with funding)
        if parent_id in ['H', 'M2'] and has_dollar:
            if has_pi_role and has_dollar:
                # Strong signal for M2 (Grant)
                if parent_id == 'H':
                    return ValidationFlag(
                        severity='warning',
                        confusion_type='H_M2_CAREER_AWARD',
                        message=f'Fellowship classified as H (Honor) but has PI role and funding',
                        suggestion='Check if this is a funded career development award (→ M2) rather than pure honor (H)',
                        alternative_sections=['M2'],
                        key_question='Is emphasis on prestige/recognition or funded project support?'
                    )
            elif not has_pi_role:
                # Weak funding signal without PI role - likely H
                if parent_id == 'M2':
                    return ValidationFlag(
                        severity='info',
                        confusion_type='H_M2_CAREER_AWARD',
                        message=f'Fellowship classified as M2 (Grant) but lacks PI/project details',
                        suggestion='If funding < $50K and ≤ 1 year, consider H (Honor) instead of M2',
                        alternative_sections=['H'],
                        key_question='Is emphasis on prestige/recognition or funded project support?'
                    )

        # Case 3: C ↔ B ↔ D ↔ N confusion (Postdoc/residency as training vs position vs mentee)
        if parent_id in ['C', 'C1', 'B', 'D', 'N', 'N3', 'N4'] and is_training_role:

            # Sub-case 3a: C/B/D → N confusion (Entry describes someone the CV owner supervises)
            # Note: Only check person names for B/D codes, not C codes (C is self-referential by definition)
            if has_mentee_context or (has_person_name and not parent_id.startswith('C')):
                if parent_id in ['C', 'C1', 'B', 'D']:
                    severity = 'error' if has_mentee_context else 'warning'
                    return ValidationFlag(
                        severity=severity,
                        confusion_type='C_B_D_N_FELLOW',
                        message=f'Training role classified as {parent_id} but appears to describe someone CV owner supervises',
                        suggestion='This appears to be a mentee/advisee - should be N3/N4 (Mentees), not CV owner\'s own training/position',
                        alternative_sections=['N3', 'N4'],
                        key_question='Is this the CV owner\'s own training/position, or someone they supervise?'
                    )

            # Sub-case 3b: C ↔ D confusion (Training vs Employment)
            if parent_id in ['C', 'C1', 'D']:
                if parent_id.startswith('C') and has_appointment_language and not has_mentor_attribution:
                    return ValidationFlag(
                        severity='info',
                        confusion_type='C_D_POSTDOC',
                        message=f'Postdoc/fellowship classified as C (Training) but has employment/appointment language',
                        suggestion='Check if this is an institutional position (→ D) vs training program (C)',
                        alternative_sections=['D'],
                        key_question='Is this training (C) or an employment position (D)?'
                    )
                elif parent_id == 'D' and (has_mentor_attribution or has_training_completion):
                    return ValidationFlag(
                        severity='info',
                        confusion_type='C_D_POSTDOC',
                        message=f'Postdoc/fellowship classified as D (Position) but has training/mentor signals',
                        suggestion='Check if this is training (→ C) vs institutional appointment (D)',
                        alternative_sections=['C', 'C1'],
                        key_question='Is this training (C) or an employment position (D)?'
                    )

            # Sub-case 3c: C ↔ B confusion (Postdoc vs Education)
            if parent_id in ['C', 'C1', 'B']:
                if parent_id == 'B' and is_training_role and not has_training_completion:
                    return ValidationFlag(
                        severity='info',
                        confusion_type='C_B_POSTDOC',
                        message=f'Postdoc/fellowship classified as B (Education) - typically belongs in C (Postdoc/Fellowship)',
                        suggestion='Postdoctoral training is usually documented in C (Postdoc), not B (Education - degrees only)',
                        alternative_sections=['C', 'C1'],
                        key_question='Is this degree-granting education (B) or postdoctoral/fellowship training (C)?'
                    )

        return None


class CareerAwardValidator:
    """
    Detects when career awards/fellowships may be misclassified as honors vs grants.

    Handles:
    - Confusion #2: H (Honors) ↔ M2 (Research Funding)
    - Confusion #6: M2 (Grants) ↔ N (Mentoring)
    """

    # Funding amount thresholds
    SMALL_AWARD_THRESHOLD = 50000  # < $50K likely H
    LARGE_AWARD_THRESHOLD = 100000  # > $100K likely M2

    # Ambiguous award types that could be either H or M2
    AMBIGUOUS_AWARD_KEYWORDS = [
        'new scholar', 'young investigator', 'new investigator',
        'research scholar', 'career development', 'mentored investigator',
        'pilot award', 'bridge award', 'transition award'
    ]

    # Pure honor keywords (no funding implied or small stipend)
    PURE_HONOR_KEYWORDS = [
        'travel award', 'travel grant', 'poster award', 'student award',
        'dissertation award', 'thesis award', 'prize',
        'medal', 'recognition', 'best paper', 'outstanding'
    ]

    # Training grant faculty roles (signals M2 AND N, not just H)
    TRAINING_GRANT_KEYWORDS = [
        'program faculty mentor', 'training grant faculty', 'program director',
        'program co-director', 't32 director', 'training program director',
        'faculty mentor on', 'mentor for training grant'
    ]

    # Federal training grant agencies/programs
    TRAINING_GRANT_AGENCIES = [
        'HRSA', 'Health Resources and Services Administration',
        'Maternal and Child Health', 'MCH', 'MCH Bureau',
        'T32', 'T15', 'T35', 'TL1'
    ]

    # Major biomedical foundations (often fund career awards that blur H/M2)
    # These foundations commonly offer awards that could be either honors or grants
    MAJOR_FOUNDATIONS = [
        'Keck', 'W. M. Keck', 'WM Keck',
        'March of Dimes',
        'Howard Hughes', 'HHMI',
        'Gates Foundation', 'Bill & Melinda Gates',
        'Sloan', 'Alfred P. Sloan',
        'Simons Foundation',
        'Wellcome Trust',
        'Doris Duke',
        'American Heart Association', 'AHA',
        'American Cancer Society', 'ACS',
        'Ellison Medical Foundation',
        'Helmsley', 'Leona M. and Harry B. Helmsley',
        'Burroughs Wellcome', 'BWF',
        'Kavli Foundation',
        'Michael J. Fox Foundation',
        'Muscular Dystrophy Association',
        'Cystic Fibrosis Foundation',
        'JDRF', 'Juvenile Diabetes Research Foundation',
        'Susan G. Komen',
        'Prostate Cancer Foundation',
        'Damon Runyon',
        'Pew Charitable Trusts',
        'Rockefeller Foundation',
        'Gordon and Betty Moore',
        'Rita Allen Foundation',
        'Lasker Foundation',
        'Dana Foundation',
        'Hartwell Foundation'
    ]

    @staticmethod
    def extract_dollar_amount(text: str) -> Optional[int]:
        """Extract dollar amount from text, return as integer."""
        # Match patterns like $750,000 or $1.5M
        match = re.search(r'\$([0-9,]+(?:\.[0-9]+)?)\s*([KMB])?', text)
        if match:
            amount_str = match.group(1).replace(',', '')
            amount = float(amount_str)

            # Handle K, M, B multipliers
            multiplier = match.group(2)
            if multiplier == 'K':
                amount *= 1000
            elif multiplier == 'M':
                amount *= 1000000
            elif multiplier == 'B':
                amount *= 1000000000

            return int(amount)
        return None

    @staticmethod
    def is_multi_year(text: str) -> bool:
        """Check if entry describes multi-year project (e.g., 2023-2028)."""
        # Match patterns like "2023-2028", "2023–2028", "2023 - 2028"
        match = re.search(r'20\d{2}\s*[-–]\s*20\d{2}', text)
        if match:
            years = re.findall(r'20\d{2}', match.group(0))
            if len(years) == 2:
                return int(years[1]) - int(years[0]) > 1
        return False

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if career award might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Extract entry text
        entry_text = ' '.join(str(e) for e in entries)
        entry_lower = entry_text.lower()

        # Check if this is an ambiguous award type
        is_ambiguous_award = any(kw in label or kw in entry_lower
                                for kw in CareerAwardValidator.AMBIGUOUS_AWARD_KEYWORDS)

        # Check if this is clearly a pure honor (skip if so)
        is_pure_honor = any(kw in label or kw in entry_lower
                           for kw in CareerAwardValidator.PURE_HONOR_KEYWORDS)

        # Check for major foundation names (increases ambiguity)
        has_foundation = any(foundation.lower() in entry_lower
                            for foundation in CareerAwardValidator.MAJOR_FOUNDATIONS)

        # Look for general award/fellowship keywords
        award_keywords = ['award', 'fellowship', 'scholar', 'investigator']
        has_award_keyword = any(kw in label for kw in award_keywords)

        # Check for training grant keywords (HRSA, faculty roles)
        has_training_grant_keywords = any(kw.lower() in entry_lower for kw in CareerAwardValidator.TRAINING_GRANT_KEYWORDS)
        has_training_grant_agency = any(kw.lower() in entry_lower for kw in CareerAwardValidator.TRAINING_GRANT_AGENCIES)
        has_training_grant = has_training_grant_keywords or has_training_grant_agency or 'training grant' in entry_lower

        # Skip if no relevant keywords
        if not (has_award_keyword or is_ambiguous_award or has_foundation or has_training_grant):
            return None

        # Skip pure honors (unless they have funding signals or foundation names)
        if is_pure_honor and not (re.search(r'\$[\d,]+', entry_text) or has_foundation):
            return None

        # Extract signals
        dollar_amount = CareerAwardValidator.extract_dollar_amount(entry_text)
        is_multi_year = CareerAwardValidator.is_multi_year(entry_text)
        has_pi_role = bool(re.search(r'\b(pi|principal investigator|co-pi)\b', entry_lower))
        has_aims = bool(re.search(r'\b(aims|objectives|project:)\b', entry_lower))
        has_grant_number = bool(re.search(r'\b(K\d{2}|R\d{2}|T\d{2}|P\d{2}|U\d{2})\b', entry_text))

        # Case 1: H ↔ M2 confusion
        if parent_id in ['H', 'M2'] and dollar_amount:
            # Signals favoring M2
            m2_score = 0
            if has_pi_role: m2_score += 2
            if is_multi_year: m2_score += 2
            if has_aims: m2_score += 1
            if has_grant_number: m2_score += 2
            if dollar_amount > CareerAwardValidator.LARGE_AWARD_THRESHOLD: m2_score += 2

            # Signals favoring H
            h_score = 0
            if dollar_amount < CareerAwardValidator.SMALL_AWARD_THRESHOLD: h_score += 2
            if not is_multi_year: h_score += 1
            if not has_pi_role: h_score += 2

            # Build descriptive award type for messages
            if has_foundation:
                award_type = "Foundation award"
            elif is_ambiguous_award:
                award_type = "Ambiguous award"
            else:
                award_type = "Award"

            if parent_id == 'H' and m2_score >= 4:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_M2_CAREER_AWARD',
                    message=f'{award_type} classified as H but has strong M2 signals (${dollar_amount:,}, PI role, multi-year)',
                    suggestion='This appears to be a funded research project - consider M2 (Research Funding)',
                    alternative_sections=['M2'],
                    key_question='Is emphasis on prestige/recognition or funded project support?'
                )
            elif parent_id == 'M2' and h_score >= 4:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_M2_CAREER_AWARD',
                    message=f'{award_type} classified as M2 but has strong H signals (small amount: ${dollar_amount:,}, no PI role)',
                    suggestion='This appears to be primarily a recognition/honor - consider H (Honors & Awards)',
                    alternative_sections=['H'],
                    key_question='Is emphasis on prestige/recognition or funded project support?'
                )
            # Special case: Ambiguous awards without clear signals (including foundation awards)
            elif (is_ambiguous_award or has_foundation) and abs(m2_score - h_score) <= 1:
                context = f"${dollar_amount:,}" if dollar_amount else "no funding amount specified"
                return ValidationFlag(
                    severity='info',
                    confusion_type='H_M2_CAREER_AWARD',
                    message=f'Ambiguous career/foundation award in {parent_id} ({context})',
                    suggestion='Verify classification: Check for grant number, PI role, multi-year project → M2; or pure recognition → H',
                    alternative_sections=['H', 'M2'],
                    key_question='Is emphasis on prestige/recognition or funded project support?'
                )

        # Case 2: M2 ↔ N confusion (Training grants)
        if parent_id in ['M2', 'N', 'N1', 'N3', 'H']:
            # Enhanced training grant detection with HRSA and faculty role keywords
            has_training_keywords = bool(re.search(r'\b(training grant|T32|fellowship program|program director)\b', entry_lower))
            has_training_grant_keywords = any(kw.lower() in entry_lower for kw in CareerAwardValidator.TRAINING_GRANT_KEYWORDS)
            has_hrsa = any(kw.lower() in entry_lower for kw in CareerAwardValidator.TRAINING_GRANT_AGENCIES)
            has_training_signals = has_training_keywords or has_training_grant_keywords or has_hrsa
            lists_mentees = bool(re.search(r'\b(mentee|trainee|fellow:.*supervised)\b', entry_lower))

            # Sub-case 2a: H → M2/N confusion (HRSA or training faculty role in H section)
            if parent_id == 'H' and (has_hrsa or has_training_grant_keywords):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_M2_N_TRAINING_GRANT',
                    message=f'Training grant/faculty role classified as H (Honor) but has training program signals',
                    suggestion='HRSA/training grants and faculty mentor roles typically belong in M2 (Funding) and/or N1 (Program Leadership), not H',
                    alternative_sections=['M2', 'N1'],
                    key_question='Is this an honor/award or a funded training program role?'
                )

            # Sub-case 2b: M2 → N confusion (emphasizes mentee relationships)
            if has_training_signals:
                if parent_id == 'M2' and lists_mentees:
                    return ValidationFlag(
                        severity='info',
                        confusion_type='M2_N_TRAINING_GRANT',
                        message=f'Training grant classified as M2 but emphasizes mentee relationships',
                        suggestion='Consider also documenting mentoring relationships in N1 (Program Leadership) or N3 (Mentees)',
                        alternative_sections=['N1', 'N3'],
                        key_question='Is this primarily documenting funding structure or mentoring relationships?'
                    )
                # Sub-case 2c: N → M2 confusion (emphasizes grant structure)
                elif parent_id in ['N', 'N1', 'N3'] and dollar_amount and not lists_mentees:
                    return ValidationFlag(
                        severity='info',
                        confusion_type='M2_N_TRAINING_GRANT',
                        message=f'Training program classified as N but emphasizes grant structure (${dollar_amount:,})',
                        suggestion='Consider also documenting funding in M2 (Research Funding) - OK to appear in both',
                        alternative_sections=['M2'],
                        key_question='Is this primarily documenting funding structure or mentoring relationships?'
                    )

        return None


class ClinicalResearchValidator:
    """
    Detects when clinical activities may be misclassified between service, research, and trials.

    Handles:
    - Confusion #5: L (Clinical Practice) ↔ M1 (Research Activities) ↔ M2A/M2B/M2C (Clinical Trials)
    NOTE: Clinical trials now use M2A (active), M2B (completed), M2C (pending) based on status
    """

    QI_KEYWORDS = [
        'quality improvement', 'qi project', 'program evaluation', 'outcomes project',
        'performance improvement', 'implementation project', 'service evaluation',
        'process improvement', 'quality assurance'
    ]

    RESEARCH_KEYWORDS = [
        'study', 'research', 'hypothesis', 'analysis', 'investigation',
        'systematic evaluation', 'data collection', 'publication', 'irb'
    ]

    # Clinical trials-specific keywords (M2A/M2B/M2C based on status)
    CLINICAL_TRIAL_KEYWORDS = [
        'trial', 'clinical trial', 'randomized', 'multicenter', 'phase i', 'phase ii',
        'phase iii', 'phase iv', 'protocol', 'site pi', 'site investigator',
        'enrollment', 'nct number', 'clinicaltrials.gov', 'rct', 'placebo',
        'double-blind', 'single-blind', 'intervention study'
    ]

    SERVICE_KEYWORDS = [
        'service delivery', 'clinical duties', 'patient care', 'care coordination',
        'clinical responsibilities', 'routine care', 'clinic', 'outpatient'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if clinical/QI/trial activity might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        # NOTE: Clinical trials now use M2A/M2B/M2C based on status
        if parent_id not in ['L', 'M1', 'M2A', 'M2B', 'M2C']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check for different activity types
        is_qi = any(kw in label or kw in entry_text for kw in ClinicalResearchValidator.QI_KEYWORDS)

        # Count signals for each category
        research_signals = sum(1 for kw in ClinicalResearchValidator.RESEARCH_KEYWORDS if kw in entry_text)
        service_signals = sum(1 for kw in ClinicalResearchValidator.SERVICE_KEYWORDS if kw in entry_text)
        trial_signals = sum(1 for kw in ClinicalResearchValidator.CLINICAL_TRIAL_KEYWORDS if kw in entry_text)

        # Check for specific markers
        has_dissemination = bool(re.search(r'\b(publication|abstract|presentation|disseminat|generaliz)', entry_text))
        has_aims = bool(re.search(r'\b(aim|objective|hypothesis|research question)', entry_text))
        has_pi_role = bool(re.search(r'\b(pi|principal investigator|site pi|site investigator)\b', entry_text))

        # Case 1: L → M2A/M2B/M2C confusion (Clinical practice that's actually trial participation)
        if parent_id == 'L' and trial_signals >= 2:
            return ValidationFlag(
                severity='warning',
                confusion_type='L_M2_TRIAL',
                message=f'Entry classified as L (Clinical) but has {trial_signals} clinical trial signals',
                suggestion='Check if this is trial participation/research (→ M2A/M2B/M2C Clinical Trials) rather than routine clinical service',
                alternative_sections=['M2A', 'M2B', 'M2C'],
                key_question='Is primary intent clinical service or trial participation/research?'
            )

        # Case 2: L → M1 confusion (QI/clinical work that's actually research)
        if parent_id == 'L' and is_qi and (research_signals >= 2 or has_dissemination or has_aims):
            return ValidationFlag(
                severity='warning',
                confusion_type='L_M1_QI',
                message=f'QI project classified as L (Clinical) but has {research_signals} research signals',
                suggestion='Check if this has systematic evaluation with generalizable findings (→ M1 Research)',
                alternative_sections=['M1'],
                key_question='Is this ongoing service/clinical function, or research project with generalizable knowledge goal?'
            )

        # Case 3: M1 → L confusion (Research that's actually operational QI)
        if parent_id == 'M1' and service_signals >= 3 and not (has_dissemination or has_aims):
            return ValidationFlag(
                severity='warning',
                confusion_type='L_M1_QI',
                message=f'Project classified as M1 (Research) but has {service_signals} service delivery signals',
                suggestion='Check if this is operational improvement (→ L Clinical) vs publishable research (M1)',
                alternative_sections=['L'],
                key_question='Is this ongoing service/clinical function, or research project with generalizable knowledge goal?'
            )

        # Case 4: M2A/M2B/M2C → L confusion (Trial listed as clinical work)
        if parent_id in ['M2A', 'M2B', 'M2C'] and service_signals >= 3 and trial_signals < 2:
            return ValidationFlag(
                severity='info',
                confusion_type='L_M2_TRIAL',
                message=f'Entry in M2A/M2B/M2C (Trials) has strong clinical service signals',
                suggestion='Verify this is trial participation (M2A/M2B/M2C) vs routine clinical work (→ L)',
                alternative_sections=['L'],
                key_question='Is primary intent clinical service or trial participation/research?'
            )

        return None


class ClinicalSupervisionValidator:
    """
    Detects when clinical supervision entries may be misclassified.

    Handles:
    - Confusion #5: L (Clinical) ↔ N (Mentoring) ↔ K (Teaching)
    """

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if clinical supervision might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        child_id = group.get('child_section_id', '')
        entries = group.get('entries', [])

        # Check for clinical supervision keywords
        supervision_keywords = ['supervisor', 'preceptor', 'supervision', 'clinical teaching']
        if not any(kw in label for kw in supervision_keywords):
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Signal detection
        has_clinical_caseload = bool(re.search(r'\b(\d+%\s+fte|clinical effort|patient|caseload)', entry_text))
        has_specific_mentees = bool(re.search(r'\b(trainee|fellow|resident):\s+[A-Z]', ' '.join(str(e) for e in entries)))
        has_curriculum = bool(re.search(r'\b(curriculum|course|credit|cme|learning objectives)', entry_text))
        has_dates = bool(re.search(r'20\d{2}\s*[-–]\s*20\d{2}', ' '.join(str(e) for e in entries)))

        # Decision logic
        if parent_id == 'L':
            if has_specific_mentees and has_dates:
                return ValidationFlag(
                    severity='info',
                    confusion_type='L_N_K_SUPERVISION',
                    message=f'Clinical supervision classified as L but lists specific mentees with dates',
                    suggestion='Consider also documenting in N (Mentoring) if focus is trainee development',
                    alternative_sections=['N', 'N3'],
                    key_question='Is primary focus patient care, trainee development, or structured teaching?'
                )
            elif has_curriculum:
                return ValidationFlag(
                    severity='info',
                    confusion_type='L_N_K_SUPERVISION',
                    message=f'Clinical supervision classified as L but describes curriculum/course',
                    suggestion='Consider K2 (Clinical Teaching) if part of formal training program',
                    alternative_sections=['K2'],
                    key_question='Is primary focus patient care, trainee development, or structured teaching?'
                )

        elif parent_id in ['N', 'N3', 'N4']:
            if has_clinical_caseload and not (has_specific_mentees or has_dates):
                return ValidationFlag(
                    severity='info',
                    confusion_type='L_N_K_SUPERVISION',
                    message=f'Clinical supervision classified as N but emphasizes clinical FTE/caseload',
                    suggestion='Check if primary focus is patient care (→ L) vs specific trainee development (N)',
                    alternative_sections=['L'],
                    key_question='Is primary focus patient care, trainee development, or structured teaching?'
                )

        elif parent_id == 'K' or child_id == 'K2':
            if has_specific_mentees and not has_curriculum:
                return ValidationFlag(
                    severity='info',
                    confusion_type='L_N_K_SUPERVISION',
                    message=f'Clinical supervision classified as K but lists specific mentees without curriculum',
                    suggestion='Check if this is ongoing mentoring (→ N) vs formal course teaching (K)',
                    alternative_sections=['N', 'N3'],
                    key_question='Is primary focus patient care, trainee development, or structured teaching?'
                )

        return None


class MembershipLeadershipValidator:
    """
    Detects when society memberships/leadership may be misclassified between honors, membership, and leadership.

    Handles:
    - Confusion #2: H (Honors) ↔ Q1 (Leadership in External Orgs)
    - Confusion #4: I (Professional Organizations/Memberships) ↔ Q1 (Leadership in External Orgs)
    """

    LEADERSHIP_TITLES = [
        'president', 'vice president', 'chair', 'co-chair', 'chairperson',
        'secretary', 'treasurer', 'councilor', 'council member', 'board member',
        'trustee', 'director', 'co-director', 'officer', 'executive committee',
        'steering committee', 'advisory board', 'governing board', 'editorial board'
    ]

    SIMPLE_MEMBERSHIP_KEYWORDS = [
        'member', 'fellow', 'invited member', 'affiliate', 'associate member',
        'student member', 'emeritus member', 'honorary member'
    ]

    PURE_HONOR_KEYWORDS = [
        'award', 'prize', 'medal', 'recognition', 'distinguished', 'outstanding',
        'achievement', 'lectureship', 'named lecture', 'recipient'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if membership/leadership entry might be misclassified between H, I, and Q1."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check sections that could have this confusion
        if parent_id not in ['H', 'I', 'Q', 'Q1']:
            return None

        # Check if this looks like a membership/society/leadership entry
        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check for society/organization keywords
        has_society = bool(re.search(r'\b(society|association|academy|college|institute|federation|organization|consortium)\b', entry_text))

        if not has_society and not any(kw in label for kw in MembershipLeadershipValidator.SIMPLE_MEMBERSHIP_KEYWORDS + MembershipLeadershipValidator.LEADERSHIP_TITLES + MembershipLeadershipValidator.PURE_HONOR_KEYWORDS):
            return None

        # Check for leadership titles
        has_leadership_title = any(title in label or title in entry_text
                                  for title in MembershipLeadershipValidator.LEADERSHIP_TITLES)

        # Check for simple membership keywords
        has_membership_keyword = any(kw in label or kw in entry_text
                                    for kw in MembershipLeadershipValidator.SIMPLE_MEMBERSHIP_KEYWORDS)

        # Check for pure honor keywords
        has_pure_honor = any(kw in label or kw in entry_text
                            for kw in MembershipLeadershipValidator.PURE_HONOR_KEYWORDS)

        # Check for signals of governance/decision-making
        has_governance_signals = bool(re.search(r'\b(elected|appointed|nominated|serve[ds]? on|governance|oversight|policy|duties|responsibilities)\b', entry_text))

        # Check for continuous membership vs discrete term
        has_continuous_range = bool(re.search(r'(19\d{2}|20\d{2})\s*[-–]\s*present', entry_text))
        has_term = bool(re.search(r'\bterm\b', entry_text))
        has_date_range = bool(re.search(r'(19\d{2}|20\d{2})\s*[-–]\s*(19\d{2}|20\d{2})', entry_text))

        # Decision logic

        # Case 1: H → Q1 confusion (Honors section contains leadership role)
        if parent_id == 'H':
            if has_leadership_title and has_society:
                # Strong signal: leadership title in society context
                if has_date_range or has_term:
                    # Very strong: has defined term
                    return ValidationFlag(
                        severity='warning',
                        confusion_type='H_Q1_LEADERSHIP',
                        message=f'Entry classified as H (Honor) but describes leadership/officer role in external organization',
                        suggestion='Check if this is governance/leadership position (→ Q1) rather than recognition/honor (H)',
                        alternative_sections=['Q1'],
                        key_question='Is this primarily governance/leadership work in an external organization, or a non-work recognition?'
                    )
                else:
                    # Moderate: leadership title but no clear term
                    return ValidationFlag(
                        severity='info',
                        confusion_type='H_Q1_LEADERSHIP',
                        message=f'Entry in H (Honor) has leadership title ({has_leadership_title})',
                        suggestion='Check if this describes office/leadership duties (→ Q1) or pure recognition (H)',
                        alternative_sections=['Q1'],
                        key_question='Is this primarily governance/leadership work in an external organization, or a non-work recognition?'
                    )

        # Case 2: I → Q1 confusion (Membership section contains leadership role)
        elif parent_id == 'I':
            if has_leadership_title or (has_governance_signals and has_term):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='I_Q1_LEADERSHIP',
                    message=f'Entry classified as I (Membership) but contains leadership title or governance role',
                    suggestion='Check if this describes an office/leadership position (→ Q1) rather than simple membership (I)',
                    alternative_sections=['Q1'],
                    key_question='Is this describing simple membership status or a formal leadership/office in an external organization?'
                )

        # Case 3: Q1 → I confusion (Leadership section contains simple membership)
        elif parent_id in ['Q', 'Q1']:
            if has_membership_keyword and not has_leadership_title and has_continuous_range:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='I_Q1_LEADERSHIP',
                    message=f'Entry classified as Q1 (Leadership) but appears to be simple membership status',
                    suggestion='Check if this is ongoing membership (→ I) vs leadership/office role (Q1)',
                    alternative_sections=['I'],
                    key_question='Is this describing simple membership status or a formal leadership/office in an external organization?'
                )
            # Q1 → H confusion (Leadership section contains pure honor)
            elif has_pure_honor and not has_leadership_title and not has_governance_signals:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_Q1_LEADERSHIP',
                    message=f'Entry classified as Q1 (Leadership) but appears to be pure honor/recognition',
                    suggestion='Check if this is a recognition/award (→ H) vs leadership role (Q1)',
                    alternative_sections=['H'],
                    key_question='Is this primarily governance/leadership work in an external organization, or a non-work recognition?'
                )

        # Case 4: Ambiguous "Fellow" - could be honor, membership, or leadership
        if 'fellow' in label or 'fellow' in entry_text:
            if parent_id == 'I' and has_governance_signals:
                return ValidationFlag(
                    severity='info',
                    confusion_type='I_Q1_LEADERSHIP',
                    message=f'Fellow entry in I (Membership) with governance signals',
                    suggestion='Check if fellowship includes leadership duties (→ Q1) or is purely membership status (I)',
                    alternative_sections=['Q1'],
                    key_question='Is this describing simple membership status or a formal leadership/office in an external organization?'
                )

        return None


class InvitedTalkHonorValidator:
    """
    Detects when invited talks/lectures may be misclassified between honors and presentations.

    Handles:
    - Confusion #1 (new): H (Honors) ↔ R (Invited Talks/Presentations)
    """

    TALK_KEYWORDS = [
        'invited lecture', 'keynote', 'distinguished speaker', 'public lecture',
        'invited presentation', 'plenary', 'grand rounds', 'visiting professor',
        'seminar', 'colloquium', 'invited talk', 'distinguished lecture'
    ]

    HONOR_EMPHASIS_KEYWORDS = [
        'honored to', 'selected to present', 'distinguished speaker series',
        'award', 'recognition', 'recipient'
    ]

    TALK_EMPHASIS_KEYWORDS = [
        'presented', 'delivered', 'gave', 'spoke on', 'addressed',
        'seminar series', 'department of', 'university of', 'grand rounds'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if invited talk might be misclassified between H and R."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check sections that could have this confusion
        if parent_id not in ['H', 'R', 'R1', 'R2']:
            return None

        # Check if this looks like an invited talk/lecture
        entry_text = ' '.join(str(e) for e in entries).lower()
        is_invited_talk = any(kw in label or kw in entry_text
                             for kw in InvitedTalkHonorValidator.TALK_KEYWORDS)

        if not is_invited_talk:
            return None

        # Count signals for each interpretation
        honor_signals = sum(1 for kw in InvitedTalkHonorValidator.HONOR_EMPHASIS_KEYWORDS
                           if kw in entry_text)
        talk_signals = sum(1 for kw in InvitedTalkHonorValidator.TALK_EMPHASIS_KEYWORDS
                          if kw in entry_text)

        # Check for talk-specific details
        has_talk_title = bool(re.search(r'["""].*["""]', ' '.join(str(e) for e in entries)))
        has_venue_details = bool(re.search(r'\b(department|university|institute|hospital|center)\s+of\b', entry_text))
        has_date = bool(re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}/\d{1,2}/\d{2,4})\b', entry_text))

        # Decision logic

        # Case 1: H → R confusion (Honor section contains actual talk)
        if parent_id == 'H':
            r_score = talk_signals
            if has_talk_title: r_score += 2
            if has_venue_details: r_score += 1
            if has_date: r_score += 1

            if r_score >= 3:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_R_INVITED_TALK',
                    message=f'Invited talk classified as H (Honor) but has {r_score} presentation-activity signals',
                    suggestion='Check if this is an invited presentation (→ R1/R2) rather than a recognition/honor (H)',
                    alternative_sections=['R1', 'R2'],
                    key_question='Is this primarily listing a talk given (with venue/date), or summarizing a recognition bestowed?'
                )

        # Case 2: R → H confusion (Presentation section contains honor)
        elif parent_id in ['R', 'R1', 'R2']:
            h_score = honor_signals
            if not has_talk_title: h_score += 1
            if not has_venue_details: h_score += 1

            if h_score >= 2:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='H_R_INVITED_TALK',
                    message=f'Invited talk classified as R but has {h_score} honor/recognition signals',
                    suggestion='Check if emphasis is on recognition/selection (→ H) rather than the talk itself (R)',
                    alternative_sections=['H'],
                    key_question='Is this primarily listing a talk given (with venue/date), or summarizing a recognition bestowed?'
                )

        return None


class PublicationMenteeValidator:
    """
    Detects when publication lists may be misclassified between owner's bibliography and mentee outputs.

    Handles:
    - Confusion #10: S (Bibliography) ↔ N (Mentoring - Mentee Publications)
    """

    MENTEE_SECTION_KEYWORDS = [
        'mentee publications', 'trainee publications', 'publications of mentees',
        'publications of trainees', 'advisee publications', 'student publications',
        'mentee scholarly output', 'trainee outcomes', 'publications by mentees'
    ]

    BIBLIOGRAPHY_SECTION_KEYWORDS = [
        'publications', 'bibliography', 'peer-reviewed articles',
        'journal articles', 'published work', 'research output',
        'selected publications', 'peer-reviewed publications'
    ]

    MENTEE_ANNOTATION_KEYWORDS = [
        'mentee:', 'trainee:', 'advisee:', 'student:', 'supervised',
        'mentored', 'advised', 'thesis student', 'postdoc:',
        '(mentee)', '(trainee)', '(advisee)', '(student)'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if publication list might be misclassified between S and N."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        child_id = group.get('child_section_id', '')
        entries = group.get('entries', [])

        # Only check publication-related sections
        if parent_id not in ['S', 'S1', 'S2', 'S8', 'N', 'N3', 'N4']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check if label/heading suggests mentee publications
        has_mentee_heading = any(kw in label for kw in PublicationMenteeValidator.MENTEE_SECTION_KEYWORDS)

        # Check if label/heading suggests bibliography
        has_bib_heading = any(kw in label for kw in PublicationMenteeValidator.BIBLIOGRAPHY_SECTION_KEYWORDS)

        # Count mentee annotations in entries
        mentee_annotations = sum(1 for kw in PublicationMenteeValidator.MENTEE_ANNOTATION_KEYWORDS
                                if kw in entry_text)

        # Check for formatting signals (*, †, underline markers, bold)
        has_formatting_markers = bool(re.search(r'(\*|\†|‡|§|\[mentee\]|\[trainee\]|<u>|<b>)', entry_text))

        # Look for "now at", "current position" language (mentee outcome tracking)
        has_outcome_language = bool(re.search(r'\b(now at|current position|currently|present position)\b', entry_text))

        # Case 1: S → N confusion (Bibliography section that's actually mentee publications)
        if parent_id in ['S', 'S1', 'S2', 'S8']:
            if has_mentee_heading or (mentee_annotations >= 2 and has_formatting_markers):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='S_N_MENTEE_PUBS',
                    message=f'Publication section classified as S (Bibliography) but has mentee-focused language',
                    suggestion='Check if this documents mentee scholarly output (→ N3/N4) rather than CV owner\'s own publications',
                    alternative_sections=['N3', 'N4'],
                    key_question='Is this a list of CV owner\'s publications or mentees\' publications (possibly without owner as co-author)?'
                )

        # Case 2: N → S confusion (Mentee section that looks like bibliography)
        if parent_id in ['N', 'N3', 'N4']:
            # Check if this looks like a standard publication list without mentee context
            if has_bib_heading and mentee_annotations == 0 and not has_outcome_language:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='S_N_MENTEE_PUBS',
                    message=f'Publication entries in N (Mentoring) section lack mentee annotations',
                    suggestion='Check if these are CV owner\'s own publications (→ S) or should have mentee annotations',
                    alternative_sections=['S1', 'S2'],
                    key_question='Is this a list of CV owner\'s publications or mentees\' publications (possibly without owner as co-author)?'
                )

        # Case 3: Ambiguous - publications with some mentee annotations in S section
        if parent_id in ['S', 'S1', 'S2', 'S8'] and mentee_annotations == 1:
            return ValidationFlag(
                severity='info',
                confusion_type='S_N_MENTEE_PUBS',
                message=f'Publication section has isolated mentee annotations',
                suggestion='Verify if entire section is mentee publications (→ N) or mixed owner/mentee publications',
                alternative_sections=['N3', 'N4'],
                key_question='Is this a list of CV owner\'s publications or mentees\' publications (possibly without owner as co-author)?'
            )

        return None


class SoftwarePatentValidator:
    """
    Detects when software/code releases may be misclassified as patents/inventions or vice versa.

    Handles:
    - Confusion #7: S11 (Software/Code Releases) ↔ M2D (Patents & Inventions)
    NOTE: Patents now use M2D instead of M3
    """

    SOFTWARE_KEYWORDS = [
        'software', 'code', 'package', 'library', 'tool', 'pipeline',
        'algorithm', 'program', 'application', 'app', 'platform',
        'github', 'gitlab', 'bitbucket', 'open-source', 'open source',
        'released', 'version', 'download', 'repository', 'repo',
        'pypi', 'cran', 'npm', 'conda', 'bioconductor'
    ]

    PATENT_KEYWORDS = [
        'patent', 'provisional', 'pct', 'invention', 'copyright',
        'us patent', 'patent no', 'patent number', 'patent application',
        'filed', 'inventor', 'assignee', 'intellectual property',
        'ip', 'licensed', 'license agreement', 'patent pending'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if software/patent entry might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        # NOTE: Patents now use M2D instead of M3
        if parent_id not in ['S11', 'M2D']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Count signals for each category
        software_signals = sum(1 for kw in SoftwarePatentValidator.SOFTWARE_KEYWORDS if kw in entry_text)
        patent_signals = sum(1 for kw in SoftwarePatentValidator.PATENT_KEYWORDS if kw in entry_text)

        # Check for specific markers
        has_github = bool(re.search(r'\b(github|gitlab|bitbucket)\.com\b', entry_text))
        has_doi = bool(re.search(r'\bdoi:', entry_text))
        has_patent_number = bool(re.search(r'\b(us|ep|wo|pct)\s*\d{7,}', entry_text))
        has_version = bool(re.search(r'\bv?\d+\.\d+', entry_text))

        # Case 1: S11 → M2D confusion (Software described as invention/patent)
        if parent_id == 'S11' and (patent_signals >= 2 or has_patent_number):
            return ValidationFlag(
                severity='warning',
                confusion_type='S11_M2D_SOFTWARE_PATENT',
                message=f'Software entry classified as S11 but has {patent_signals} patent/IP signals',
                suggestion='Check if emphasis is on IP protection (→ M2D Patents) rather than scholarly software release (S11)',
                alternative_sections=['M2D'],
                key_question='Is this emphasizing scholarly software dissemination or formal IP protection?'
            )

        # Case 2: M2D → S11 confusion (Patent that's actually open-source software)
        if parent_id == 'M2D' and (software_signals >= 3 or has_github):
            return ValidationFlag(
                severity='warning',
                confusion_type='S11_M2D_SOFTWARE_PATENT',
                message=f'Patent entry has {software_signals} software release signals (GitHub, open-source, etc.)',
                suggestion='Check if this is open scholarly software (→ S11) rather than protected invention (M2D)',
                alternative_sections=['S11'],
                key_question='Is this emphasizing scholarly software dissemination or formal IP protection?'
            )

        # Case 3: Dual nature (both patent and software release)
        if (parent_id == 'S11' and patent_signals >= 1) or (parent_id == 'M2D' and software_signals >= 1):
            if has_github and has_patent_number:
                return ValidationFlag(
                    severity='info',
                    confusion_type='S11_M2D_SOFTWARE_PATENT',
                    message=f'Entry has both software release and patent indicators',
                    suggestion='May warrant dual documentation in both S11 (software) and M2D (patent)',
                    alternative_sections=['S11', 'M2D'],
                    key_question='Is this emphasizing scholarly software dissemination or formal IP protection?'
                )

        return None


class DatasetResearchValidator:
    """
    Detects when datasets may be misclassified as research activities or vice versa.

    Handles:
    - Confusion #8: S12 (Datasets) ↔ M1 (Research Activities/Resources)
    """

    DATASET_KEYWORDS = [
        'dataset', 'data', 'database', 'repository', 'resource',
        'released', 'deposited', 'published', 'doi', 'accession',
        'figshare', 'zenodo', 'dryad', 'dataverse', 'genbank',
        'geo', 'sra', 'dbgap', 'pdb', 'ncbi', 'public data'
    ]

    RESEARCH_ACTIVITY_KEYWORDS = [
        'developing', 'building', 'maintaining', 'lead pi', 'pi for',
        'directing', 'managing', 'coordinating', 'establishing',
        'ongoing', 'current', 'project', 'initiative', 'program'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if dataset/research resource might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        if parent_id not in ['S12', 'M1']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Count signals for each category
        dataset_signals = sum(1 for kw in DatasetResearchValidator.DATASET_KEYWORDS if kw in entry_text)
        research_signals = sum(1 for kw in DatasetResearchValidator.RESEARCH_ACTIVITY_KEYWORDS if kw in entry_text)

        # Check for specific markers
        has_doi = bool(re.search(r'\bdoi:\s*10\.\d+', entry_text))
        has_accession = bool(re.search(r'\b(GSE|SRR|PRJ|PDB)\d+\b', entry_text))
        has_url = bool(re.search(r'\bhttps?://', entry_text))
        has_ongoing = bool(re.search(r'\b(ongoing|current|present|in progress)\b', entry_text))
        has_pi_role = bool(re.search(r'\b(pi|principal investigator|lead)\b', entry_text))

        # Case 1: S12 → M1 confusion (Dataset described as ongoing research activity)
        if parent_id == 'S12' and (research_signals >= 2 or (has_ongoing and has_pi_role)):
            if not (has_doi or has_accession):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='S12_M1_DATASET',
                    message=f'Dataset entry classified as S12 but described as ongoing research activity',
                    suggestion='Check if this is research infrastructure/activity (→ M1) vs released dataset product (S12)',
                    alternative_sections=['M1'],
                    key_question='Is this a finished, citable data product or ongoing research infrastructure?'
                )

        # Case 2: M1 → S12 confusion (Research activity that's actually a released dataset)
        if parent_id == 'M1' and (dataset_signals >= 2 or has_doi or has_accession):
            return ValidationFlag(
                severity='warning',
                confusion_type='S12_M1_DATASET',
                message=f'Research activity has dataset release signals (DOI, accession, repository)',
                suggestion='Check if this is a citable dataset product (→ S12) vs ongoing research activity (M1)',
                alternative_sections=['S12'],
                key_question='Is this a finished, citable data product or ongoing research infrastructure?'
            )

        # Case 3: Dual nature (both dataset and research activity)
        if (has_doi or has_accession) and has_ongoing and has_pi_role:
            return ValidationFlag(
                severity='info',
                confusion_type='S12_M1_DATASET',
                message=f'Entry has both dataset product and ongoing research activity characteristics',
                suggestion='May warrant dual documentation: released dataset (S12) and ongoing research program (M1)',
                alternative_sections=['S12', 'M1'],
                key_question='Is this a finished, citable data product or ongoing research infrastructure?'
            )

        return None


class TrialPublicationValidator:
    """
    Detects when clinical trial records may be misclassified as publications or vice versa.

    Handles:
    - Confusion #6 (new): M2A/M2B/M2C (Clinical Trials) ↔ S (Bibliography)
    NOTE: Clinical trials now use M2A (active), M2B (completed), M2C (pending) based on status

    Key distinction:
    - M2A/M2B/M2C: Trial registration/conduct (NCT number, PI role, enrollment, no citation format)
    - S: Publication about trial results (citation format: authors, journal, volume, pages)
    """

    # Trial registration/conduct markers (signals M2A/M2B/M2C)
    TRIAL_MARKERS = [
        'nct', 'clinicaltrials.gov', 'trial registration', 'protocol',
        'site pi', 'site investigator', 'enrollment', 'accrual',
        'phase i', 'phase ii', 'phase iii', 'phase iv',
        'randomized controlled trial', 'rct', 'multicenter trial',
        'principal investigator', 'co-investigator', 'study coordinator'
    ]

    # Publication markers (signals S)
    PUBLICATION_MARKERS = [
        'published in', 'journal:', 'vol.', 'volume', 'pages',
        'pp.', 'doi:', 'pmid:', 'pubmed', 'epub',
        'author:', 'first author', 'corresponding author',
        'impact factor', 'peer-reviewed', 'peer reviewed'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if trial record might be misclassified as publication or vice versa."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        # NOTE: Clinical trials now use M2A/M2B/M2C based on status
        if parent_id not in ['M2A', 'M2B', 'M2C', 'S', 'S1']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Count signals for each category
        trial_signals = sum(1 for kw in TrialPublicationValidator.TRIAL_MARKERS if kw in entry_text)
        publication_signals = sum(1 for kw in TrialPublicationValidator.PUBLICATION_MARKERS if kw in entry_text)

        # Check for specific markers
        has_nct = bool(re.search(r'\bNCT\d{8}\b', entry_text, re.IGNORECASE))
        has_pmid = bool(re.search(r'\bPMID:\s*\d+\b', entry_text, re.IGNORECASE))
        has_doi = bool(re.search(r'\bdoi:\s*10\.\d+', entry_text))
        has_journal_citation = bool(re.search(r'\b\d{4};\s*\d+\(\d+\):\d+-\d+', entry_text))  # e.g., "2024; 45(3):123-456"
        has_pi_role = bool(re.search(r'\b(pi|principal investigator|site pi|study chair)\b', entry_text))
        has_enrollment = bool(re.search(r'\b(enrollment|accrual|patients|subjects):\s*\d+', entry_text))

        # Case 1: M2A/M2B/M2C → S confusion (Trial record with publication markers)
        if parent_id in ['M2A', 'M2B', 'M2C'] and (publication_signals >= 2 or has_pmid or has_doi):
            return ValidationFlag(
                severity='warning',
                confusion_type='M2_S_TRIAL_PUBLICATION',
                message=f'Clinical trial entry classified as {parent_id} but has publication citation markers',
                suggestion='Check if this is a published paper about trial results (→ S1) rather than the trial registration/conduct',
                alternative_sections=['S1'],
                key_question='Is this documenting the trial conduct (M2A/M2B/M2C) or a publication reporting results (S)?'
            )

        # Case 2: S → M2A/M2B/M2C confusion (Publication with trial markers but no citation format)
        if parent_id in ['S', 'S1'] and (trial_signals >= 2 or has_nct):
            # Strong signal for M2A/M2B/M2C if has NCT and no journal citation format
            if has_nct and not (has_journal_citation or has_pmid):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='M2_S_TRIAL_PUBLICATION',
                    message=f'Entry classified as S (Publication) but has trial registration markers without journal citation',
                    suggestion='Check if this is trial registration/conduct (→ M2A/M2B/M2C) rather than a published paper',
                    alternative_sections=['M2A', 'M2B', 'M2C'],
                    key_question='Is this documenting the trial conduct (M2A/M2B/M2C) or a publication reporting results (S)?'
                )

        # Case 3: Dual nature (both trial and publication - common for trial reports)
        if has_nct and (has_pmid or has_journal_citation):
            return ValidationFlag(
                severity='info',
                confusion_type='M2_S_TRIAL_PUBLICATION',
                message=f'Entry has both trial registration (NCT) and publication citation markers',
                suggestion='Trial results publications may warrant dual documentation: trial conduct (M2A/M2B/M2C) and publication (S1)',
                alternative_sections=['M2A', 'M2B', 'M2C', 'S1'],
                key_question='Is this documenting the trial conduct (M2A/M2B/M2C) or a publication reporting results (S)?'
            )

        return None


class BookTeachingValidator:
    """
    Detects when books/chapters may be misclassified as educational materials or vice versa.

    Handles:
    - Confusion #10 (new): S3/S4 (Books/Chapters) ↔ K (Educational Contributions)

    Key distinction:
    - S3/S4: Published scholarly books/chapters (full citation: authors, title, publisher, year, ISBN)
    - K: Educational materials/curricula (course codes, syllabi, "used in course X", internal materials)
    """

    # Scholarly publication markers (signals S3/S4)
    BOOK_PUBLICATION_MARKERS = [
        'isbn', 'publisher:', 'published by', 'edition',
        'springer', 'elsevier', 'wiley', 'oxford university press',
        'cambridge university press', 'taylor & francis', 'sage',
        'academic press', 'doi:', 'chapter in:', 'pp.', 'pages'
    ]

    # Educational material markers (signals K)
    TEACHING_MATERIAL_MARKERS = [
        'course', 'syllabus', 'curriculum', 'teaching module',
        'used in', 'lecture notes', 'course materials', 'instructional',
        'for students', 'training materials', 'educational module',
        'course code', 'class handout', 'internal use'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if book/chapter might be misclassified as teaching material or vice versa."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        if parent_id not in ['S3', 'S4', 'K', 'K1']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Count signals for each category
        book_signals = sum(1 for kw in BookTeachingValidator.BOOK_PUBLICATION_MARKERS if kw in entry_text)
        teaching_signals = sum(1 for kw in BookTeachingValidator.TEACHING_MATERIAL_MARKERS if kw in entry_text)

        # Check for specific markers
        has_isbn = bool(re.search(r'\bISBN[:\s-]*[\d-]{10,17}\b', entry_text, re.IGNORECASE))
        has_doi = bool(re.search(r'\bdoi:\s*10\.\d+', entry_text))
        has_publisher = bool(re.search(r'\b(publisher|published by|press)\b', entry_text))
        has_course_code = bool(re.search(r'\b(course|class)[\s:]*[A-Z]{2,4}[\s-]?\d{3,4}\b', entry_text))
        has_used_in_course = bool(re.search(r'\bused in\s+(course|class|training)\b', entry_text))
        has_syllabus = 'syllabus' in entry_text or 'curriculum' in entry_text

        # Case 1: S3/S4 → K confusion (Book/chapter with teaching usage notes)
        if parent_id in ['S3', 'S4'] and (teaching_signals >= 2 or has_used_in_course):
            # Only flag if it's JUST teaching materials without publication markers
            if not (has_isbn or has_doi or has_publisher):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='S3_S4_K_BOOK_TEACHING',
                    message=f'Book/chapter entry classified as {parent_id} but has teaching material signals without publication markers',
                    suggestion='Check if this is educational curriculum material (→ K1) rather than a published book/chapter',
                    alternative_sections=['K1'],
                    key_question='Is this a published scholarly work (S3/S4) or educational curriculum material (K)?'
                )

        # Case 2: K → S3/S4 confusion (Teaching material that's actually a published book)
        if parent_id in ['K', 'K1'] and (book_signals >= 2 or has_isbn or (has_doi and has_publisher)):
            return ValidationFlag(
                severity='warning',
                confusion_type='S3_S4_K_BOOK_TEACHING',
                message=f'Teaching material classified as {parent_id} but has book publication markers',
                suggestion='Check if this is a published book/chapter (→ S3/S4) rather than educational curriculum material',
                alternative_sections=['S3', 'S4'],
                key_question='Is this a published scholarly work (S3/S4) or educational curriculum material (K)?'
            )

        # Case 3: Dual nature (textbook used in courses - common scenario)
        if (has_isbn or has_publisher) and (has_course_code or has_used_in_course):
            return ValidationFlag(
                severity='info',
                confusion_type='S3_S4_K_BOOK_TEACHING',
                message=f'Entry has both published book and course usage characteristics',
                suggestion='Textbooks used in teaching may warrant dual documentation: book (S3) and teaching materials (K1)',
                alternative_sections=['S3', 'K1'],
                key_question='Is this a published scholarly work (S3/S4) or educational curriculum material (K)?'
            )

        return None


class PositionLeadershipValidator:
    """
    Detects when positions may be misclassified as leadership roles or vice versa.

    Handles:
    - Confusion #14 (new): D (Professional Positions) ↔ O (Institutional Leadership)
    - Confusion #15 (new): Q1 (External Leadership) ↔ O (Institutional Leadership)

    Key distinctions:
    - D: Job title/appointment without executive authority
    - O: Leadership with authority over budget/personnel/programs (internal)
    - Q1: Leadership in external organizations
    """

    # Leadership titles that could be D, O, or Q1
    AMBIGUOUS_LEADERSHIP_TITLES = [
        'director', 'chief', 'chair', 'vice chair', 'co-chair',
        'head', 'coordinator', 'lead', 'manager', 'program lead',
        'administrative lead', 'executive', 'section chief'
    ]

    # Authority markers (signal O - institutional leadership)
    AUTHORITY_MARKERS = [
        'oversaw', 'managed budget', 'budget authority', 'fiscal responsibility',
        'responsible for all', 'supervised faculty', 'hired', 'appointed',
        'strategic planning', 'resource allocation', 'personnel decisions',
        'reporting to', 'direct reports', 'staff of', 'team of',
        'executive authority', 'governance', 'administrative oversight'
    ]

    # Institutional markers (signal O not Q1)
    INSTITUTIONAL_MARKERS = [
        'university', 'school of', 'college of', 'hospital', 'medical center',
        'campus', 'institutional', 'department', 'division', 'section',
        'center', 'institute', 'program'
    ]

    # External organization markers (signal Q1 not O)
    EXTERNAL_ORG_MARKERS = [
        'national', 'international', 'society', 'association', 'consortium',
        'foundation', 'academy', 'federation', 'alliance', 'coalition',
        'network', 'collaborative', 'organization', 'board of directors',
        'external advisory board', 'scientific advisory board'
    ]

    # Position-only markers (signal D not O)
    POSITION_MARKERS = [
        'attending', 'faculty', 'staff', 'instructor', 'assistant',
        'associate', 'professor', 'researcher', 'scientist', 'clinician',
        'physician', 'fellow' # Fellow as position, not honor
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if position/leadership might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        if parent_id not in ['D', 'D1', 'D2', 'O', 'Q1']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check for ambiguous leadership titles
        has_leadership_title = any(title in label or title in entry_text
                                   for title in PositionLeadershipValidator.AMBIGUOUS_LEADERSHIP_TITLES)

        if not has_leadership_title:
            return None  # No potential confusion

        # Check for various markers
        has_authority = any(marker in entry_text
                           for marker in PositionLeadershipValidator.AUTHORITY_MARKERS)
        has_institutional = any(marker in entry_text
                               for marker in PositionLeadershipValidator.INSTITUTIONAL_MARKERS)
        has_external = any(marker in entry_text
                          for marker in PositionLeadershipValidator.EXTERNAL_ORG_MARKERS)
        has_position_marker = any(marker in entry_text
                                 for marker in PositionLeadershipValidator.POSITION_MARKERS)

        # Count authority signals
        authority_signal_count = sum(1 for marker in PositionLeadershipValidator.AUTHORITY_MARKERS
                                     if marker in entry_text)

        # Case 1: D → O confusion (Position classified as D but has authority markers)
        if parent_id in ['D', 'D1', 'D2'] and (authority_signal_count >= 2 or has_authority):
            # Strong signal for O if authority + institutional
            if has_institutional or not has_external:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='D_O_POSITION_LEADERSHIP',
                    message=f'Position classified as D but has institutional leadership/authority signals',
                    suggestion='Check if this role has executive authority over programs/budget/personnel (→ O)',
                    alternative_sections=['O'],
                    key_question='Does this role carry authority over people, programs, or budget?'
                )

        # Case 2: O → D confusion (Leadership classified as O but lacks authority markers)
        if parent_id == 'O' and not has_authority and authority_signal_count == 0:
            # Weak leadership signals - might just be a position
            if has_position_marker:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='D_O_POSITION_LEADERSHIP',
                    message=f'Entry classified as O (Leadership) but lacks clear authority markers',
                    suggestion='Check if this is primarily a position/appointment (→ D) rather than executive leadership',
                    alternative_sections=['D', 'D1'],
                    key_question='Does this role carry authority over people, programs, or budget?'
                )

        # Case 3: Q1 → O confusion (External leadership classified as Q1 but is internal)
        if parent_id == 'Q1' and has_institutional and not has_external:
            return ValidationFlag(
                severity='warning',
                confusion_type='Q1_O_EXTERNAL_INTERNAL',
                message=f'External leadership classified as Q1 but has institutional/internal markers',
                suggestion='Check if this is internal institutional leadership (→ O) rather than external organization',
                alternative_sections=['O'],
                key_question='Is this organization inside your home institution or external?'
            )

        # Case 4: O → Q1 confusion (Internal leadership classified as O but is external)
        if parent_id == 'O' and has_external and not has_institutional:
            return ValidationFlag(
                severity='warning',
                confusion_type='Q1_O_EXTERNAL_INTERNAL',
                message=f'Institutional leadership classified as O but has external organization markers',
                suggestion='Check if this is external organization leadership (→ Q1) rather than internal institutional',
                alternative_sections=['Q1'],
                key_question='Is this organization inside your home institution or external?'
            )

        # Case 5: Ambiguous leadership without clear context
        if has_leadership_title and authority_signal_count == 1:
            return ValidationFlag(
                severity='info',
                confusion_type='D_O_Q1_LEADERSHIP',
                message=f'Leadership title present but context unclear',
                suggestion='Verify: Position without authority (D), institutional leadership (O), or external leadership (Q1)',
                alternative_sections=['D', 'O', 'Q1'],
                key_question='Is this a position (D), internal leadership (O), or external leadership (Q1)?'
            )

        return None


class ClinicalTeachingValidator:
    """
    Detects when clinical teaching may be misclassified as clinical practice or vice versa.

    Handles:
    - Confusion #16 (new): K2 (Clinical Teaching) ↔ L (Clinical Practice)

    Key distinction:
    - K2: Clinical activities with primary educational purpose
    - L: Clinical activities with primary service/patient care purpose
    """

    # Teaching-focused markers (signal K2)
    TEACHING_MARKERS = [
        'teaching rounds', 'teaching service', 'clinical instruction',
        'preceptor', 'precepting', 'resident education', 'trainee supervision',
        'medical student teaching', 'clinical educator', 'educational supervision',
        'teaching attending', 'bedside teaching', 'didactic', 'curriculum',
        'instructional', 'clerkship', 'rotation supervision'
    ]

    # Service-focused markers (signal L)
    CLINICAL_SERVICE_MARKERS = [
        'clinical care', 'patient care', 'patient panel', 'caseload',
        'clinical duties', 'fte', 'clinical time', 'outpatient clinic',
        'inpatient service', 'quality improvement', 'quality assurance',
        'service delivery', 'clinical responsibilities', 'practice management',
        'patient management', 'clinical operations'
    ]

    # Ambiguous terms (could be either)
    AMBIGUOUS_TERMS = [
        'attending', 'attending physician', 'service', 'clinic',
        'supervising', 'supervision', 'rounds', 'weekly sessions'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if clinical teaching vs practice might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        if parent_id not in ['K2', 'L']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check for ambiguous terms
        has_ambiguous = any(term in label or term in entry_text
                           for term in ClinicalTeachingValidator.AMBIGUOUS_TERMS)

        if not has_ambiguous:
            return None  # No potential confusion

        # Count signals for each category
        teaching_signals = sum(1 for kw in ClinicalTeachingValidator.TEACHING_MARKERS
                              if kw in entry_text)
        service_signals = sum(1 for kw in ClinicalTeachingValidator.CLINICAL_SERVICE_MARKERS
                             if kw in entry_text)

        # Check for specific markers
        has_fte = bool(re.search(r'\b(\d+%\s*(fte|clinical|time))', entry_text))
        has_caseload = bool(re.search(r'\b(caseload|patient panel|patients?/|visits?/)', entry_text))
        has_curriculum = 'curriculum' in entry_text or 'course' in entry_text
        has_trainees = bool(re.search(r'\b(resident|fellow|student|trainee|learner)s?\b', entry_text))
        has_educational = bool(re.search(r'\b(educat|teach|instruct|train)', entry_text))

        # Case 1: K2 → L confusion (Teaching classified as K2 but has service signals)
        if parent_id == 'K2' and (service_signals >= 2 or has_fte or has_caseload):
            # Strong signal for L if service-heavy without educational markers
            if not (has_curriculum or teaching_signals >= 2):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='K2_L_CLINICAL_TEACHING',
                    message=f'Clinical teaching entry classified as K2 but has clinical service/FTE signals',
                    suggestion='Check if this is primarily clinical service delivery (→ L) rather than educational activity',
                    alternative_sections=['L'],
                    key_question='Is the primary purpose education or clinical service?'
                )

        # Case 2: L → K2 confusion (Service classified as L but has teaching signals)
        if parent_id == 'L' and (teaching_signals >= 2 or (has_educational and has_trainees)):
            # Strong signal for K2 if teaching-focused without service metrics
            if not (has_fte or has_caseload or service_signals >= 2):
                return ValidationFlag(
                    severity='warning',
                    confusion_type='K2_L_CLINICAL_TEACHING',
                    message=f'Clinical service entry classified as L but has teaching/educational signals',
                    suggestion='Check if this is primarily clinical teaching (→ K2) rather than service delivery',
                    alternative_sections=['K2'],
                    key_question='Is the primary purpose education or clinical service?'
                )

        # Case 3: Dual nature (both teaching and service - common in academic medicine)
        if (teaching_signals >= 1 and service_signals >= 1) or (has_educational and has_fte):
            return ValidationFlag(
                severity='info',
                confusion_type='K2_L_CLINICAL_TEACHING',
                message=f'Entry has both clinical teaching and service characteristics',
                suggestion='Academic attending roles may warrant documentation in both K2 (teaching) and L (service)',
                alternative_sections=['K2', 'L'],
                key_question='Is the primary purpose education or clinical service?'
            )

        return None


class PresentationTypeValidator:
    """
    Detects when conference presentations may be misclassified as invited talks or vice versa.

    Handles:
    - Confusion #17 (new): S8 (Conference Abstracts/Posters) ↔ R (Invited Talks)
    - Enhancement of InvitedTalkHonorValidator for S8 ↔ R confusion

    Key distinction:
    - S8: Conference abstract submission (selected via peer review)
    - R: Invited presentation (selected by invitation)
    - H: Honor of being invited (if emphasis is on recognition)
    """

    # Invited presentation markers (signal R)
    INVITED_MARKERS = [
        'invited', 'keynote', 'plenary', 'distinguished lecture',
        'named lecture', 'featured speaker', 'guest speaker',
        'visiting professor', 'invited speaker', 'invited talk',
        'invited presentation', 'invited address', 'grand rounds'
    ]

    # Abstract/poster markers (signal S8)
    ABSTRACT_MARKERS = [
        'abstract', 'poster', 'poster presentation', 'poster session',
        'oral presentation', 'oral abstract', 'platform presentation',
        'selected abstract', 'accepted abstract', 'submitted abstract',
        'abstract number', 'session', 'concurrent session'
    ]

    # Conference/meeting markers (could be either)
    CONFERENCE_MARKERS = [
        'conference', 'meeting', 'symposium', 'congress', 'summit',
        'annual meeting', 'international conference'
    ]

    @staticmethod
    def validate(group: Dict[str, Any]) -> Optional[ValidationFlag]:
        """Check if presentation type might be misclassified."""
        label = group.get('label_inferred', '').lower()
        parent_id = group.get('parent_section_id', '')
        entries = group.get('entries', [])

        # Only check relevant sections
        if parent_id not in ['S8', 'R', 'R1', 'R2', 'H']:
            return None

        entry_text = ' '.join(str(e) for e in entries).lower()

        # Check for conference context
        has_conference = any(marker in label or marker in entry_text
                            for marker in PresentationTypeValidator.CONFERENCE_MARKERS)

        # Check for specific markers
        has_invited = any(marker in entry_text
                         for marker in PresentationTypeValidator.INVITED_MARKERS)
        has_abstract = any(marker in entry_text
                          for marker in PresentationTypeValidator.ABSTRACT_MARKERS)

        # Check for abstract formatting patterns
        has_abstract_number = bool(re.search(r'\babstract\s*[#:]?\s*\d+', entry_text))
        has_citation_pattern = bool(re.search(r'\b\d{4};\s*\d+\(?\d*\)?:\s*\d+', entry_text))  # Journal citation

        # Case 1: S8 → R confusion (Abstract classified as S8 but has invited markers)
        if parent_id == 'S8' and has_invited:
            return ValidationFlag(
                severity='warning',
                confusion_type='S8_R_PRESENTATION',
                message=f'Abstract/poster classified as S8 but has "invited" language',
                suggestion='Check if this is an invited presentation (→ R1/R2) rather than accepted abstract submission',
                alternative_sections=['R1', 'R2'],
                key_question='Was the speaker invited or selected via abstract submission?'
            )

        # Case 2: R → S8 confusion (Invited talk classified as R but has abstract markers)
        if parent_id in ['R', 'R1', 'R2'] and (has_abstract or has_abstract_number):
            # Only flag if NO invited language present
            if not has_invited:
                return ValidationFlag(
                    severity='warning',
                    confusion_type='S8_R_PRESENTATION',
                    message=f'Presentation classified as R (Invited) but has abstract/poster markers without "invited" language',
                    suggestion='Check if this is conference abstract submission (→ S8) rather than invited talk',
                    alternative_sections=['S8'],
                    key_question='Was the speaker invited or selected via abstract submission?'
                )

        # Case 3: Ambiguous presentation at conference without clear markers
        if has_conference and not has_invited and not has_abstract:
            if parent_id in ['R', 'R1', 'R2']:
                return ValidationFlag(
                    severity='info',
                    confusion_type='S8_R_PRESENTATION',
                    message=f'Conference presentation classified as R (Invited) but lacks clear "invited" language',
                    suggestion='Verify this was an invited presentation. Without "invited" marker, may be abstract submission (→ S8)',
                    alternative_sections=['S8'],
                    key_question='Was the speaker invited or selected via abstract submission?'
                )

        # Case 4: H ↔ R confusion for invited talks (enhancement from InvitedTalkHonorValidator)
        if parent_id in ['H', 'R', 'R1', 'R2'] and has_invited:
            has_honor_language = bool(re.search(r'\b(recipient|honored|selected|award)', entry_text))
            has_presentation_details = bool(re.search(r'\b(presented|delivered|gave|spoke)', entry_text))

            if parent_id == 'H' and has_presentation_details and not has_honor_language:
                return ValidationFlag(
                    severity='info',
                    confusion_type='H_R_INVITED_TALK',
                    message=f'Invited presentation classified as H (Honor) but emphasizes presentation activity',
                    suggestion='Check if this documents the presentation event (→ R) rather than honor of selection',
                    alternative_sections=['R1', 'R2'],
                    key_question='Is emphasis on being selected (H) or on the presentation activity (R)?'
                )

        return None


def run_all_validators(group: Dict[str, Any]) -> List[ValidationFlag]:
    """Run all disambiguation validators on a single group."""
    validators = [
        # Phase 1 validators
        FellowAmbiguityValidator,
        CareerAwardValidator,
        MembershipLeadershipValidator,
        InvitedTalkHonorValidator,
        ClinicalResearchValidator,
        ClinicalSupervisionValidator,
        # Phase 2 validators
        PublicationMenteeValidator,
        SoftwarePatentValidator,
        DatasetResearchValidator,
        TrialPublicationValidator,
        BookTeachingValidator,
        # Phase 3 validators (V6.0)
        PositionLeadershipValidator,
        ClinicalTeachingValidator,
        PresentationTypeValidator
    ]

    flags = []
    for validator_class in validators:
        flag = validator_class.validate(group)
        if flag:
            flags.append(flag)

    return flags


def validate_cv(cv_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run validators on all groups in a CV and add validation metadata.

    Args:
        cv_data: Full CV JSON with groups

    Returns:
        CV data with added 'validation_flags' field in each group
    """
    groups = cv_data.get('groups', [])

    total_flags = 0
    flags_by_severity = {'info': 0, 'warning': 0, 'error': 0}
    flags_by_type = {}

    for group in groups:
        flags = run_all_validators(group)

        if flags:
            # Add flags to group metadata
            group['validation_flags'] = [
                {
                    'severity': f.severity,
                    'confusion_type': f.confusion_type,
                    'message': f.message,
                    'suggestion': f.suggestion,
                    'alternative_sections': f.alternative_sections,
                    'key_question': f.key_question
                }
                for f in flags
            ]

            # Update statistics
            total_flags += len(flags)
            for f in flags:
                flags_by_severity[f.severity] = flags_by_severity.get(f.severity, 0) + 1
                flags_by_type[f.confusion_type] = flags_by_type.get(f.confusion_type, 0) + 1

    # Add validation summary to metadata
    if 'meta' not in cv_data:
        cv_data['meta'] = {}

    cv_data['meta']['validation_summary'] = {
        'total_flags': total_flags,
        'by_severity': flags_by_severity,
        'by_confusion_type': flags_by_type,
        'validators_applied': [
            # Phase 1 validators (high-priority confusion areas)
            'FellowAmbiguityValidator',
            'CareerAwardValidator',
            'MembershipLeadershipValidator',
            'InvitedTalkHonorValidator',
            'ClinicalResearchValidator',
            'ClinicalSupervisionValidator',
            # Phase 2 validators (medium-priority, specialized areas)
            'PublicationMenteeValidator',
            'SoftwarePatentValidator',
            'DatasetResearchValidator'
        ]
    }

    return cv_data


if __name__ == '__main__':
    """Test validators with example cases."""
    import json

    # Test case 1: Fellow ambiguity
    test_group_1 = {
        'id': '1',
        'label_inferred': 'Fellow, American College of Physicians',
        'parent_section_id': 'H',
        'entries': ['Fellow, American College of Physicians, since 2019']
    }

    flag = FellowAmbiguityValidator.validate(test_group_1)
    if flag:
        print("Test 1 (Fellow H→I confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 1b: Honor society induction (I → H confusion)
    test_group_1b = {
        'id': '1b',
        'label_inferred': 'Delta Omega National Public Health Honor Society',
        'parent_section_id': 'I',
        'entries': ['Inducted into Delta Omega National Public Health Honor Society, 2014']
    }

    flag = FellowAmbiguityValidator.validate(test_group_1b)
    if flag:
        print("Test 1b (Honor society I→H confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 2: Career award
    test_group_2 = {
        'id': '2',
        'label_inferred': 'NIH K08 Award',
        'parent_section_id': 'H',
        'entries': ['NIH Mentored Clinical Scientist Development Award (K08), 2023–2028, $750,000, PI']
    }

    flag = CareerAwardValidator.validate(test_group_2)
    if flag:
        print("Test 2 (Career Award H→M2 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 2b: HRSA training grant (H → M2/N confusion)
    test_group_2b = {
        'id': '2b',
        'label_inferred': 'Maternal and Child Health Training Grant',
        'parent_section_id': 'H',
        'entries': ['Maternal and Child Health Training Grant supported by HRSA, 1998–2002, Program Faculty Mentor']
    }

    flag = CareerAwardValidator.validate(test_group_2b)
    if flag:
        print("Test 2b (HRSA training grant H→M2/N confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 3: QI project
    test_group_3 = {
        'id': '3',
        'label_inferred': 'Quality Improvement Project',
        'parent_section_id': 'L',
        'entries': [
            'Hospital QI Project to Reduce Readmissions',
            'Systematic data collection and analysis',
            'Planned publication in JAMA'
        ]
    }

    flag = ClinicalResearchValidator.validate(test_group_3)
    if flag:
        print("Test 3 (QI L→M1 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 4: Invited talk (H → R confusion)
    test_group_4 = {
        'id': '4',
        'label_inferred': 'Invited Keynote Lecture',
        'parent_section_id': 'H',
        'entries': [
            '2020 | Invited Keynote Lecture at the 5th Canadian Symposium on Telomeres and Genome Stability',
            'Presented "Telomere Dynamics in Aging" at University of Toronto, Department of Biochemistry'
        ]
    }

    flag = InvitedTalkHonorValidator.validate(test_group_4)
    if flag:
        print("Test 4 (Invited Talk H→R confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 5: Leadership role (I → Q1 confusion)
    test_group_5 = {
        'id': '5',
        'label_inferred': 'Member, Environmental Mutagenesis Society',
        'parent_section_id': 'I',
        'entries': [
            '2018–2022 | Elected Vice President and next President of the Environmental Mutagenesis and Genomics Society'
        ]
    }

    flag = MembershipLeadershipValidator.validate(test_group_5)
    if flag:
        print("Test 5 (Membership I→Q1 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 5b: Leadership role in honors section (H → Q1 confusion)
    test_group_5b = {
        'id': '5b',
        'label_inferred': 'President, American Academy of Allergy, Asthma and Immunology',
        'parent_section_id': 'H',
        'entries': [
            'President, American Academy of Allergy, Asthma and Immunology, 2017–2018'
        ]
    }

    flag = MembershipLeadershipValidator.validate(test_group_5b)
    if flag:
        print("Test 5b (Leadership role H→Q1 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 6: Postdoc mentee (C → N confusion)
    test_group_6 = {
        'id': '6',
        'label_inferred': 'Postdoctoral Fellow',
        'parent_section_id': 'C',
        'entries': [
            'Samantha Sanford | August 2020 – Postdoctoral fellow, UPMC Hillman Cancer Center',
            'Thesis: Telomere maintenance in cancer cells'
        ]
    }

    flag = FellowAmbiguityValidator.validate(test_group_6)
    if flag:
        print("Test 6 (Postdoc C→N confusion - mentee context):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 7: Ambiguous career award (with some funding but unclear signals)
    test_group_7 = {
        'id': '7',
        'label_inferred': 'New Scholar Award',
        'parent_section_id': 'H',
        'entries': [
            '2006 | Ellison Medical Foundation New Scholar in Aging Research, $75,000'
        ]
    }

    flag = CareerAwardValidator.validate(test_group_7)
    if flag:
        print("Test 7 (Ambiguous Career Award - New Scholar):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")
    else:
        print("Test 7 (Ambiguous Career Award - New Scholar): No flag (as expected - borderline amount)\n")

    # Test case 8: Clinical trial (L → M2A/M2B/M2C confusion)
    test_group_8 = {
        'id': '8',
        'label_inferred': 'Clinical Activities',
        'parent_section_id': 'L',
        'entries': [
            'Site PI for randomized Phase II trial of novel AMD treatment',
            'Multicenter, double-blind study; enrollment ongoing; NCT12345678'
        ]
    }

    flag = ClinicalResearchValidator.validate(test_group_8)
    if flag:
        print("Test 8 (Clinical Trial L→M2A/M2B/M2C confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 9: Fellowship abbreviation (H → I confusion)
    test_group_9 = {
        'id': '9',
        'label_inferred': 'Distinguished Fellow Award',
        'parent_section_id': 'H',
        'entries': [
            '2019 | Fellow, American College of Physicians (FACP)'
        ]
    }

    flag = FellowAmbiguityValidator.validate(test_group_9)
    if flag:
        print("Test 9 (Fellowship abbreviation H→I confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 10: Foundation award (H/M2 ambiguity)
    test_group_10 = {
        'id': '10',
        'label_inferred': 'Research Award',
        'parent_section_id': 'H',
        'entries': [
            '2015 | Ellison Medical Foundation New Scholar in Aging Award, $150,000'
        ]
    }

    flag = CareerAwardValidator.validate(test_group_10)
    if flag:
        print("Test 10 (Foundation award with funding - H/M2 ambiguity):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # ========== Phase 2 Validator Tests ==========

    # Test case 11: Mentee publications (S → N confusion)
    test_group_11 = {
        'id': '11',
        'label_inferred': 'Publications of Mentees',
        'parent_section_id': 'S1',
        'entries': [
            'Smith J (mentee), Opresko PL. "Telomere dysfunction in aging." Nature. 2020.',
            'Jones A (trainee), Lee K. "DNA repair mechanisms." Cell. 2021. Now at Harvard Medical School.'
        ]
    }

    flag = PublicationMenteeValidator.validate(test_group_11)
    if flag:
        print("Test 11 (Mentee Publications S→N confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 12: Software with patent (S11 → M2D confusion)
    test_group_12 = {
        'id': '12',
        'label_inferred': 'TelomereTool Software',
        'parent_section_id': 'S11',
        'entries': [
            'TelomereTool: Analysis platform for telomere length quantification',
            'US Patent 10,123,456, filed 2020, licensed to GenomeCorp'
        ]
    }

    flag = SoftwarePatentValidator.validate(test_group_12)
    if flag:
        print("Test 12 (Software with Patent S11→M2D confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 13: Open-source software in patent section (M2D → S11 confusion)
    test_group_13 = {
        'id': '13',
        'label_inferred': 'Computational Method',
        'parent_section_id': 'M2D',
        'entries': [
            'BPAC: Universal model for transcription factor binding site prediction',
            'Open-source Python package, available on GitHub: github.com/lab/bpac',
            'Released v1.2, 2023'
        ]
    }

    flag = SoftwarePatentValidator.validate(test_group_13)
    if flag:
        print("Test 13 (Open-source in Patents M2D→S11 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 14: Dataset with DOI (M1 → S12 confusion)
    test_group_14 = {
        'id': '14',
        'label_inferred': 'Retinal Degeneration Registry',
        'parent_section_id': 'M1',
        'entries': [
            'Lead PI for multicenter registry of inherited retinal degeneration patients',
            'Dataset released 2023, DOI: 10.5061/dryad.abc123',
            'Available on Figshare, n=2,500 participants'
        ]
    }

    flag = DatasetResearchValidator.validate(test_group_14)
    if flag:
        print("Test 14 (Dataset with DOI M1→S12 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 15: Ongoing research activity (S12 → M1 confusion)
    test_group_15 = {
        'id': '15',
        'label_inferred': 'Genomic Database',
        'parent_section_id': 'S12',
        'entries': [
            'Developing single-cell RNA-seq atlas of human retina',
            'PI for ongoing data collection, currently expanding to >1M cells',
            'Building public data portal'
        ]
    }

    flag = DatasetResearchValidator.validate(test_group_15)
    if flag:
        print("Test 15 (Ongoing activity S12→M1 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 16: Trial publication (M2A/M2B/M2C → S confusion)
    test_group_16 = {
        'id': '16',
        'label_inferred': 'Clinical Trial',
        'parent_section_id': 'M2B',  # Using M2B for completed trials
        'entries': [
            'NCT01234567: Randomized trial of Drug X vs Placebo for Hypertension. ',
            'Published in N Engl J Med 2024; 390(5):456-467. PMID: 12345678'
        ]
    }

    flag = TrialPublicationValidator.validate(test_group_16)
    if flag:
        print("Test 16 (Trial with publication M2A/M2B/M2C→S confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 17: Publication with trial info (S → M2A/M2B/M2C confusion)
    test_group_17 = {
        'id': '17',
        'label_inferred': 'Clinical Trial',
        'parent_section_id': 'S1',
        'entries': [
            'NCT01234567: Phase III multicenter trial of Drug X',
            'Site PI, enrollment: 150 patients, ongoing accrual'
        ]
    }

    flag = TrialPublicationValidator.validate(test_group_17)
    if flag:
        print("Test 17 (Trial registration S→M2A/M2B/M2C confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 18: Teaching material as book (K → S3/S4 confusion)
    test_group_18 = {
        'id': '18',
        'label_inferred': 'Course Materials',
        'parent_section_id': 'K1',
        'entries': [
            'Foundations of Clinical Bioinformatics, 2nd edition. ',
            'Elsevier, 2022. ISBN: 978-0-12-345678-9'
        ]
    }

    flag = BookTeachingValidator.validate(test_group_18)
    if flag:
        print("Test 18 (Published book K→S3 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 19: Book as teaching material (S3 → K confusion)
    test_group_19 = {
        'id': '19',
        'label_inferred': 'Chapter in Book',
        'parent_section_id': 'S4',
        'entries': [
            'Introduction to Genomics course syllabus and lecture notes',
            'Used in BIOL 5150, updated annually for graduate students'
        ]
    }

    flag = BookTeachingValidator.validate(test_group_19)
    if flag:
        print("Test 19 (Course materials S4→K confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 20: Position with authority (D → O confusion)
    test_group_20 = {
        'id': '20',
        'label_inferred': 'Director, Cancer Genomics Core Facility',
        'parent_section_id': 'D1',
        'entries': [
            'Director, Cancer Genomics Core Facility, 2019–present',
            'Oversaw staff of 10, managed budget of $2M, responsible for all strategic planning'
        ]
    }

    flag = PositionLeadershipValidator.validate(test_group_20)
    if flag:
        print("Test 20 (Position with authority D→O confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 21: External leadership misclassified as internal (Q1 → O confusion)
    test_group_21 = {
        'id': '21',
        'label_inferred': 'Board Member, Center for Digital Health',
        'parent_section_id': 'Q1',
        'entries': [
            'Board Member, University Center for Digital Health Innovation, 2020–present',
            'Campus-wide institutional center reporting to Provost'
        ]
    }

    flag = PositionLeadershipValidator.validate(test_group_21)
    if flag:
        print("Test 21 (External org Q1→O confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 22: Clinical teaching with service signals (K2 → L confusion)
    test_group_22 = {
        'id': '22',
        'label_inferred': 'Attending Physician, Internal Medicine',
        'parent_section_id': 'K2',
        'entries': [
            'Attending physician, Internal Medicine inpatient service, 40% FTE',
            'Patient care responsibilities, caseload of 15 patients'
        ]
    }

    flag = ClinicalTeachingValidator.validate(test_group_22)
    if flag:
        print("Test 22 (Teaching with service K2→L confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 23: Clinical service with teaching signals (L → K2 confusion)
    test_group_23 = {
        'id': '23',
        'label_inferred': 'Clinical Service',
        'parent_section_id': 'L',
        'entries': [
            'Preceptor for Family Medicine clerkship, weekly teaching rounds',
            'Supervised medical students and residents in clinical instruction'
        ]
    }

    flag = ClinicalTeachingValidator.validate(test_group_23)
    if flag:
        print("Test 23 (Service with teaching L→K2 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 24: Abstract with invited markers (S8 → R confusion)
    test_group_24 = {
        'id': '24',
        'label_inferred': 'Conference Presentation',
        'parent_section_id': 'S8',
        'entries': [
            'Invited keynote presentation at ASCO Annual Meeting, 2023',
            'Advances in Precision Oncology'
        ]
    }

    flag = PresentationTypeValidator.validate(test_group_24)
    if flag:
        print("Test 24 (Abstract with invited S8→R confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    # Test case 25: Invited talk with abstract markers (R → S8 confusion)
    test_group_25 = {
        'id': '25',
        'label_inferred': 'Presentation',
        'parent_section_id': 'R1',
        'entries': [
            'Oral presentation at RSNA, Abstract #12345',
            'Selected for poster session on AI imaging methods'
        ]
    }

    flag = PresentationTypeValidator.validate(test_group_25)
    if flag:
        print("Test 25 (Invited with abstract R→S8 confusion):")
        print(f"  {flag.severity.upper()}: {flag.message}")
        print(f"  Suggestion: {flag.suggestion}\n")

    print("=" * 60)
    print("✓ All validator tests complete")
    print("  Phase 1 tests (1-10): High-priority confusion areas")
    print("  Phase 2 tests (11-19): Specialized confusion areas")
    print("  Phase 3 tests (20-25): V6.0 new validators")
    print("=" * 60)
