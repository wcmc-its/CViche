"""
Dataset/Cohort Validator

Distinguishes dataset publications (S12) from cohort/biobank management (M1).
Critical for entries about datasets that could be either publications or research activities.
"""

import re

from .base_validator import BaseValidator, ValidatorGuidance


class DatasetCohortValidator(BaseValidator):
    """
    Disambiguates dataset publications (S12) from cohort management (M1).

    Key distinctions:
    - S12: Published datasets with DOIs, data papers, dataset citations
    - M1: Active cohort/biobank management, data collection activities

    Patterns detected:
    - Dataset DOI or repository (Dryad, Zenodo, Figshare) → S12
    - "Data descriptor", "Data article" → S12
    - "Established cohort", "Managing biobank" → M1
    - Active recruitment/enrollment language → M1
    """

    name = "DatasetCohortValidator"

    def applies_to(self) -> list[str]:
        """Applies to bibliography and research overview sections."""
        return ['S', 'bibliography', 'M', 'research_overview']

    def priority(self) -> int:
        """High priority - critical for S12 vs M1 disambiguation."""
        return 85

    # Dataset publication indicators (S12)
    DATASET_PUBLICATION_PATTERNS = [
        # Publication types
        r'\bData\s+(?:descriptor|article|paper|publication)\b',
        r'\bDataset\s+(?:publication|release|archive)\b',
        r'\bPublished\s+dataset\b',

        # Dataset repositories
        r'\b(?:Dryad|Zenodo|Figshare|OSF|Dataverse|GenBank|ArrayExpress|GEO|dbGaP)\b',

        # Dataset DOIs
        r'\bdoi:\s*10\.(?:5061|5281|6084|7910)',  # Common dataset DOI prefixes

        # Dataset citation patterns
        r'\bDataset\s+available\s+at\b',
        r'\bData\s+repository:\s*https?://',
        r'\bAccession\s+(?:number|code):\s*[A-Z]+\d+',

        # Journal names for data papers
        r'\bScientific\s+Data\b',
        r'\bData\s+in\s+Brief\b',
        r'\bGigaScience\b',
        r'\bBMC\s+Research\s+Notes\b',
    ]

    # Cohort/biobank management indicators (M1)
    COHORT_MANAGEMENT_PATTERNS = [
        # Establishment/management language
        r'\b(?:Established|Managing|Directing|Coordinating)\s+(?:cohort|biobank|registry)\b',
        r'\bCohort\s+(?:director|manager|coordinator)\b',
        r'\bBiobank\s+(?:establishment|management|coordination)\b',

        # Active recruitment
        r'\b(?:Recruiting|Enrolling|Accruing)\s+(?:participants|patients|subjects)\b',
        r'\bActive\s+(?:recruitment|enrollment)\b',
        r'\bn\s*=\s*\d+\s+(?:participants|patients|subjects)\s+(?:enrolled|recruited)\b',

        # Cohort characteristics
        r'\bLongitudinal\s+cohort\s+(?:study|of)\b',
        r'\bProspective\s+cohort\b',
        r'\bOngoing\s+(?:cohort|biobank|registry)\b',

        # Infrastructure/resources
        r'\bSample\s+(?:collection|banking|storage)\b',
        r'\bBiospecimen\s+repository\b',
        r'\bClinical\s+(?:registry|database)\s+management\b',
    ]

    # Shared dataset/cohort language (ambiguous)
    SHARED_PATTERNS = [
        r'\bcohort\b',
        r'\bdataset\b',
        r'\bbiobank\b',
        r'\bregistry\b',
    ]

    # Publication metadata (suggests S12)
    PUBLICATION_METADATA = [
        r'\b(?:PMID|PubMed):\s*\d+\b',
        r'\bPMC\d+\b',
        r'\bdoi:\s*10\.\d+',
        r'\b\d{4};\d+\(\d+\):\d+',  # Journal citation
        r'\bPublished:\s+\d{4}\b',
    ]

    # Anti-patterns (indicate active management, not publication)
    ACTIVE_MANAGEMENT_ANTI_PATTERNS = [
        r'\b(?:currently|ongoing|active)\s+(?:managing|directing|recruiting)\b',
        r'\b\d{4}\s*-\s*present\b',  # Ongoing activity
        r'\bPI\s+(?:for|of)\s+(?:cohort|biobank)\b',
    ]

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry to distinguish dataset publications from cohort management.

        Returns:
            Guidance for S12 vs M1 classification
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        text_lower = entry_text.lower()

        # Check for dataset publication indicators
        has_dataset_publication = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.DATASET_PUBLICATION_PATTERNS
        )

        has_publication_metadata = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PUBLICATION_METADATA
        )

        # Check for cohort management indicators
        has_cohort_management = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.COHORT_MANAGEMENT_PATTERNS
        )

        has_active_management = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.ACTIVE_MANAGEMENT_ANTI_PATTERNS
        )

        # Check for shared/ambiguous language
        has_shared_language = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.SHARED_PATTERNS
        )

        # HARD ROUTING: Dataset publication with DOI/repository
        if has_dataset_publication or (has_shared_language and has_publication_metadata):
            deterministic_signals = []
            hints = []

            if has_dataset_publication:
                deterministic_signals.append('dataset_publication_type')
                hints.append('Data paper/descriptor or dataset repository → Published dataset (S12)')

            if has_publication_metadata:
                deterministic_signals.append('publication_metadata')
                hints.append('PMID/DOI present → Published work (S12)')

            return ValidatorGuidance(
                exclude_sections=['M1', 'M'],
                recommend_sections=['S12', 'S'],
                hints=hints + [
                    'Published datasets → S12 (Dataset Publications)',
                    'Cohort/biobank management activities → M1'
                ],
                confidence=0.92,
                severity='hard',
                allow_override=True,
                deterministic_signals=deterministic_signals
            )

        # HARD ROUTING: Active cohort/biobank management
        if has_cohort_management or has_active_management:
            deterministic_signals = []
            hints = []

            if has_cohort_management:
                deterministic_signals.append('cohort_management_language')
                hints.append('Cohort establishment/management language → Research activity (M1)')

            if has_active_management:
                deterministic_signals.append('active_management')
                hints.append('Ongoing/active management → M1, not published dataset (S12)')

            return ValidatorGuidance(
                exclude_sections=['S12', 'S'],
                recommend_sections=['M1', 'M'],
                hints=hints + [
                    'Cohort/biobank management activities → M1 (Cohort/Biobank Development)',
                    'Published dataset papers → S12'
                ],
                confidence=0.88,
                severity='hard',
                allow_override=True,
                deterministic_signals=deterministic_signals
            )

        # SOFT GUIDANCE: Dataset/cohort language without clear context
        if has_shared_language and not has_dataset_publication and not has_cohort_management:
            return ValidatorGuidance(
                hints=[
                    'Entry mentions dataset/cohort but unclear if publication (S12) or management (M1)',
                    'Check for: DOI/repository (S12) or establishment/management language (M1)'
                ],
                confidence=0.50,
                severity='soft',
                deterministic_signals=['ambiguous_dataset_cohort']
            )

        # SOFT GUIDANCE: "Data descriptor" journal sections
        if 'scientific data' in text_lower or 'data in brief' in text_lower:
            return ValidatorGuidance(
                recommend_sections=['S12'],
                hints=[
                    'Journal specializing in data publications (Scientific Data, Data in Brief) → S12',
                    'These journals publish dataset descriptors, not original research'
                ],
                confidence=0.85,
                severity='soft',
                deterministic_signals=['data_journal']
            )

        # SOFT GUIDANCE: Accession numbers (genomic/proteomic databases)
        accession_pattern = r'\b(?:GEO|SRA|dbGaP|ArrayExpress|PRIDE):\s*[A-Z]+\d+\b'
        if re.search(accession_pattern, entry_text):
            return ValidatorGuidance(
                recommend_sections=['S12', 'S1'],
                hints=[
                    'Database accession number present → Likely published dataset (S12)',
                    'Could also be supplementary data in research article (S1)'
                ],
                confidence=0.75,
                severity='soft',
                deterministic_signals=['database_accession']
            )

        # No clear signals
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Distinguishes dataset publications (S12) from cohort/biobank management (M1)"
