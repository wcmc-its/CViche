"""
Validators Package

Provides validation-guided classification:
- Pre-validation: Narrow LLM options using deterministic signals
- Modular validators: Easy to add new patterns from Phase 2 feedback
- Guidance engine: Orchestrates all validators

Usage:
    from validators import analyze_entries_for_guidance

    guidance = analyze_entries_for_guidance(
        parent_section_id='S',
        entries=['Smith J. Nature. 2020. doi: 10.1038/...']
    )

    # Use guidance.recommended_subsections in LLM prompt
    # Check guidance.excluded_subsections for hard rules
"""

from .awards_grants import AwardsGrantsValidator
from .base_validator import BaseValidator, GuidanceResult, ValidatorGuidance
from .book_chapter import BookChapterValidator
from .committee_position_corrector import (
    CommitteePositionCorrector,
    apply_committee_corrections,
)
from .contact_section import ContactSectionValidator
from .dataset_cohort import DatasetCohortValidator
from .education_postdoc import EducationPostdocValidator
from .fellowship_classifier import FellowshipValidator
from .grant_structure import GrantStructureValidator
from .guidance_engine import (
    GuidanceEngine,
    analyze_entries_for_guidance,
    get_guidance_engine,
    register_validator,
)
from .honors_membership import HonorsMembershipValidator
from .label_content_conflict import LabelContentConflictValidator
from .leadership_committee import LeadershipCommitteeValidator
from .mentee_outcomes import MenteeOutcomesValidator

# NEW: ChatGPT CV 2036 Hoffman feedback validators
from .mentoring_indicators import MentoringIndicatorsValidator
from .postdoc_position import PostdocPositionValidator
from .professional_service import ProfessionalServiceValidator

# NEW: S* publication validators (Pass 1.5 deterministic routing)
from .publication_abstract import PublicationAbstractValidator
from .publication_case_report import CaseReportValidator
from .publication_manuscript_in_prep import ManuscriptInPrepValidator
from .publication_preprint import PublicationPreprintValidator
from .reasoning_consistency_checker import (
    apply_reasoning_corrections,
    check_reasoning_consistency,
)
from .s7_unpublished import S7UnpublishedValidator

# NEW: Structural improvement validators (addressing confusion areas)
from .section_header_context import SectionHeaderContextValidator

# NEW: CV 2032 Haendel feedback validators
from .service_vs_publication import ServiceVsPublicationValidator

# NEW: Post-classification auto-correction validators (v11.1)
from .structural_header import StructuralHeaderValidator, apply_structural_corrections
from .temporal_patterns import TemporalPatternValidator
from .url_domain import URLDomainValidator

# Register validators on import
_s7_validator = S7UnpublishedValidator()
register_validator(_s7_validator)

_education_postdoc_validator = EducationPostdocValidator()
register_validator(_education_postdoc_validator)

_awards_grants_validator = AwardsGrantsValidator()
register_validator(_awards_grants_validator)

_contact_validator = ContactSectionValidator()
register_validator(_contact_validator)

_url_domain_validator = URLDomainValidator()
register_validator(_url_domain_validator)

# NEW: ChatGPT CV 2036 Hoffman feedback validators
_mentoring_validator = MentoringIndicatorsValidator()
register_validator(_mentoring_validator)

_professional_service_validator = ProfessionalServiceValidator()
register_validator(_professional_service_validator)

_leadership_committee_validator = LeadershipCommitteeValidator()
register_validator(_leadership_committee_validator)

_fellowship_validator = FellowshipValidator()
register_validator(_fellowship_validator)

_label_content_conflict_validator = LabelContentConflictValidator()
register_validator(_label_content_conflict_validator)

# NEW: CV 2032 Haendel feedback validators
_service_vs_publication_validator = ServiceVsPublicationValidator()
register_validator(_service_vs_publication_validator)

_mentee_outcomes_validator = MenteeOutcomesValidator()
register_validator(_mentee_outcomes_validator)

# NEW: S* publication validators (Pass 1.5 deterministic routing)
_publication_abstract_validator = PublicationAbstractValidator()
register_validator(_publication_abstract_validator)

_publication_preprint_validator = PublicationPreprintValidator()
register_validator(_publication_preprint_validator)

_manuscript_in_prep_validator = ManuscriptInPrepValidator()
register_validator(_manuscript_in_prep_validator)

_case_report_validator = CaseReportValidator()
register_validator(_case_report_validator)

# NEW: Structural improvement validators
_section_header_validator = SectionHeaderContextValidator()
register_validator(_section_header_validator)

_grant_structure_validator = GrantStructureValidator()
register_validator(_grant_structure_validator)

_postdoc_position_validator = PostdocPositionValidator()
register_validator(_postdoc_position_validator)

_temporal_pattern_validator = TemporalPatternValidator()
register_validator(_temporal_pattern_validator)

_book_chapter_validator = BookChapterValidator()
register_validator(_book_chapter_validator)

_honors_membership_validator = HonorsMembershipValidator()
register_validator(_honors_membership_validator)

_dataset_cohort_validator = DatasetCohortValidator()
register_validator(_dataset_cohort_validator)


__all__ = [
    # Base classes
    'BaseValidator',
    'ValidatorGuidance',
    'GuidanceResult',

    # Engine
    'GuidanceEngine',
    'get_guidance_engine',
    'register_validator',
    'analyze_entries_for_guidance',

    # Validators
    'S7UnpublishedValidator',
    'EducationPostdocValidator',
    'AwardsGrantsValidator',
    'ContactSectionValidator',
    'URLDomainValidator',
    'LabelContentConflictValidator',
    'PublicationAbstractValidator',
    'PublicationPreprintValidator',
    'ManuscriptInPrepValidator',
    'CaseReportValidator',
    'SectionHeaderContextValidator',
    'GrantStructureValidator',
    'PostdocPositionValidator',
    'TemporalPatternValidator',
    'BookChapterValidator',
    'HonorsMembershipValidator',
    'DatasetCohortValidator',
    # Post-classification auto-correctors
    'StructuralHeaderValidator',
    'apply_structural_corrections',
    'CommitteePositionCorrector',
    'apply_committee_corrections',
    'apply_reasoning_corrections',
    'check_reasoning_consistency',
]
