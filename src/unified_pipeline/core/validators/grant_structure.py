"""
Grant Structure Validator

Detects grant-like structural patterns to distinguish funded research (M2)
from honors (H) and mentoring relationships (N).
"""

import re
from typing import List
from .base_validator import BaseValidator, ValidatorGuidance


class GrantStructureValidator(BaseValidator):
    """
    Detects grant/funding structure patterns regardless of section location.

    Patterns detected:
    - Grant numbers (R01, K08, P30, T32, etc.)
    - Dollar amounts
    - Role markers (PI:, Co-I:, Mentor:)
    - Project periods (2020-2025)
    - Funding agencies (NIH, NSF, DOD, etc.)

    Routes to M2 even if entry appears in "Honors" or "Mentoring" sections.
    """

    name = "GrantStructureValidator"

    def applies_to(self) -> List[str]:
        """Applies to sections that might contain grants."""
        return ['M', 'research_overview', 'H', 'honors_awards', 'N', 'mentoring']

    def priority(self) -> int:
        """High priority - grant structure is a strong signal."""
        return 80

    # Grant mechanism patterns (NIH, NSF, DOD, etc.)
    GRANT_MECHANISMS = [
        # NIH mechanisms
        r'\b(R01|R03|R15|R21|R34|R35|R37|R56|RC1|RC2|RC4|RF1|RL1|RL2|RL9|RM1|U01|U19|U24|U34|U54|UG3|UH2|UH3|UM1|UM2)',
        r'\b(P01|P20|P30|P40|P41|P42|P50|P51|P60)',
        r'\b(K01|K02|K07|K08|K22|K23|K24|K25|K99|KL2)',
        r'\b(T32|T34|T35|T36|T37|T90|TL1|TU2)',
        r'\b(F30|F31|F32|F33|F99)',
        r'\b(D43|D71|DP1|DP2|DP3|DP4|DP5)',

        # NSF mechanisms
        r'\b(CAREER|PECASE)',
        r'\bNSF[- ]?\d{7}',

        # DOD mechanisms
        r'\b(W81XWH|FA9550|N00014)',

        # Foundation mechanisms
        r'\b(CZI|HHMI|PCORI|CPRIT)',
    ]

    # Dollar amount patterns
    DOLLAR_PATTERNS = [
        r'\$\s*[\d,]+(?:,\d{3})*(?:\.\d{2})?(?:\s*[KkMm])?',  # $150,000 or $1.5M
        r'(?:Direct|Total|Annual)\s+[Cc]osts?:\s*\$[\d,]+',
    ]

    # Role markers
    ROLE_PATTERNS = [
        r'\b(PI|Co-PI|Co-I|Co-Investigator|Principal Investigator|Multiple PI|MPI|Contact PI)[\s:,]',
        r'\bRole:\s*(PI|Co-PI|Co-I|Principal Investigator|Co-Investigator)',
    ]

    # Project period patterns
    PROJECT_PERIOD_PATTERNS = [
        r'\b(20\d{2})\s*[-–—]\s*(20\d{2}|\d{2})\b',  # 2020-2025 or 2020-25
        r'\b(Project|Award)\s+[Pp]eriod:\s*\d{1,2}/\d{1,2}/\d{4}',
    ]

    # Funding agency patterns (comprehensive biomedical funders)
    FUNDING_AGENCY_PATTERNS = [
        # NIH Institutes
        r'\b(NIH|National Institutes of Health)',
        r'\b(NCI|National Cancer Institute)',
        r'\b(NHLBI)',
        r'\b(NIA|National Institute on Aging)',
        r'\b(NIAAA)',
        r'\b(NIAID)',
        r'\b(NIAMS)',
        r'\b(NIDDK)',
        r'\b(NIDA)',
        r'\b(NIDCD)',
        r'\b(NIDCR)',
        r'\b(NIEHS)',
        r'\b(NINDS)',
        r'\b(NIMH)',
        r'\b(NIMHD)',
        r'\b(NICHD)',
        r'\b(NIGMS)',
        r'\b(NIBIB)',
        r'\b(NHGRI)',
        r'\b(NLM)',

        # NIH Centers & Offices
        r'\b(NCATS)',
        r'\b(NCCIH)',
        r'\b(FIC)',
        r'\b(CIT)',
        r'\b(CSR)',
        r'\b(ORS)',
        r'\b(OD)',

        # U.S. Federal Biomedical Agencies (Non-NIH)
        r'\b(AHRQ)',
        r'\b(BARDA)',
        r'\b(CDC|Centers for Disease Control)',
        r'\b(FDA|Food and Drug Administration)',
        r'\b(HRSA)',
        r'\b(PCORI)',
        r'\b(SAMHSA)',
        r'\b(VA|Veterans Affairs)',
        r'\b(DOD|Department of Defense)',
        r'\b(CDMRP)',
        r'\b(DARPA|ONR|AFOSR|ARO)',
        r'\b(EPA|Environmental Protection Agency)',
        r'\b(NASA)',
        r'\b(NSF|National Science Foundation)',
        r'\b(NSF-BIO|NSF-SBE)',

        # Major U.S. Private / Nonprofit Biomedical Funders
        r'\b(HHMI|Howard Hughes Medical Institute)',
        r'\b(American Cancer Society|ACS)',
        r'\b(American Heart Association|AHA)',
        r'\b(American Diabetes Association|ADA)',
        r'\b(Muscular Dystrophy Association|MDA)',
        r'\b(JDRF)',
        r'\b(CRUK|Cancer Research UK)',
        r'\b(LAF)',
        r'\b(ALS-A)',
        r'\b(AFSP)',
        r'\b(BRF)',
        r'\b(CPRIT)',
        r'\b(Susan G\. Komen|Komen Foundation)',
        r'\b(Prostate Cancer Foundation|PCF)',
        r'\b(Chan Zuckerberg Initiative|CZI)',
        r'\b(Bill & Melinda Gates Foundation|GATES)',

        # Europe – National Biomedical Research Funders
        r'\b(DFG)',
        r'\b(BMBF)',
        r'\b(SNF)',
        r'\b(FWO)',
        r'\b(FNRS)',
        r'\b(INSERM)',
        r'\b(ANR)',
        r'\b(MRC|Medical Research Council)',
        r'\b(NIHR)',
        r'\b(WELLCOME|Wellcome Trust)',
        r'\b(IRC)',
        r'\b(HRB)',
        r'\b(NETHRF)',

        # Nordic Countries & Northern Europe
        r'\b(VR)',
        r'\b(SRC)',
        r'\b(RRF)',
        r'\b(RCN)',
        r'\b(AKA)',
        r'\b(FRIPRO)',

        # Asia-Pacific Biomedical Funders
        r'\b(AMED)',
        r'\b(JSPS)',
        r'\b(KAKENHI)',
        r'\b(NRF-KOREA)',
        r'\b(KHIDI)',
        r'\b(MOHW-KOREA)',
        r'\b(NHMRC)',
        r'\b(ARC-AUS)',
        r'\b(HRC-NZ)',
        r'\b(MOST-TAIWAN)',
        r'\b(CIHR)',
        r'\b(NSERC-CA)',
        r'\b(SSHRC-CA)',

        # China
        r'\b(NSFC)',
        r'\b(NSTC-CHINA)',
        r'\b(CAMS)',

        # International / Multinational Organizations
        r'\b(WHO|World Health Organization)',
        r'\b(IARC)',
        r'\b(UNICEF)',
        r'\b(GAVI)',
        r'\b(CEPI)',
        r'\b(GFATM)',
        r'\b(UNDP-GH)',

        # Disease-Specific International Nonprofits
        r'\b(MSIF)',
        r'\b(IHF)',
        r'\b(IOF)',
        r'\b(ICF)',
    ]

    # Project title/aims indicators
    PROJECT_INDICATORS = [
        r'\bAims?:\s*[A-Z]',
        r'\bProject\s+[Tt]itle:\s*[A-Z]',
        r'\bSpecific\s+[Aa]ims',
        r'\bThe\s+aim\s+of\s+this\s+(project|study)',
    ]

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry for grant structure patterns.

        Returns hard recommendation for M2 if strong grant signals detected.
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        # Count grant structure indicators
        has_mechanism = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.GRANT_MECHANISMS
        )

        has_dollars = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.DOLLAR_PATTERNS
        )

        has_role = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.ROLE_PATTERNS
        )

        has_period = any(
            re.search(pattern, entry_text)
            for pattern in self.PROJECT_PERIOD_PATTERNS
        )

        has_agency = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.FUNDING_AGENCY_PATTERNS
        )

        has_project_indicators = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PROJECT_INDICATORS
        )

        # Count strong grant signals
        signals_count = sum([
            has_mechanism,
            has_dollars,
            has_role,
            has_period,
            has_agency,
            has_project_indicators
        ])

        deterministic_signals = []
        hints = []

        # HARD ROUTING: Strong grant structure (3+ indicators)
        if signals_count >= 3:
            if has_mechanism:
                deterministic_signals.append('grant_mechanism')
                hints.append('Grant mechanism detected (R01, K08, etc.) → M2')

            if has_dollars:
                deterministic_signals.append('funding_amount')
                hints.append('Funding amount present → Research Support (M2)')

            if has_role:
                deterministic_signals.append('investigator_role')
                hints.append('PI/Co-I role → Research grant (M2)')

            if has_period:
                deterministic_signals.append('project_period')

            if has_agency:
                deterministic_signals.append('funding_agency')

            return ValidatorGuidance(
                exclude_sections=['H', 'H1', 'H2', 'H3'],  # Not a pure honor
                recommend_sections=['M2'],
                hints=hints + [
                    f'Grant structure detected ({signals_count} indicators)',
                    'Even if in Honors section, this has grant mechanics → M2'
                ],
                confidence=0.90,
                severity='hard',
                allow_override=False,
                deterministic_signals=deterministic_signals
            )

        # SOFT GUIDANCE: Some grant signals (2 indicators)
        elif signals_count == 2:
            if has_mechanism:
                deterministic_signals.append('possible_grant_mechanism')
            if has_dollars:
                deterministic_signals.append('possible_funding_amount')
            if has_role:
                deterministic_signals.append('possible_role')

            return ValidatorGuidance(
                recommend_sections=['M2'],
                hints=[
                    'Partial grant structure detected',
                    'May be research funding (M2) rather than honor (H)'
                ],
                confidence=0.70,
                severity='soft',
                deterministic_signals=deterministic_signals
            )

        # No grant structure detected
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Detects grant structure (mechanism, amount, role, period) to identify research funding (M2)"
