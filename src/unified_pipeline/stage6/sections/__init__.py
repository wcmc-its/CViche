"""One module per WCM template section (#398).

`stage_6_word_template.py` carried 30 `_fill_*` writers inside one class, so a
change to Service was reviewed in the same file as Publications. Each WCM
section has its own rules -- its own template tables, its own taxonomy codes,
its own rerouting quirks -- and almost nothing to say to its neighbours. That is
the boundary the files follow.

    administrative_activities.py  section P:   institutional committees, roles
    appendix.py                   section T:   unmapped content, last resort
    bibliography.py               section S:   publications, S1-S9
    board_certification.py        section F2:  specialty boards
    clinical_practice.py          section L:   clinical practice, innovation, leadership
    education.py                  section B1:  degrees
    honors.py                     section H:   honors and awards
    leadership.py                 section O:   institutional leadership activities
    licensure.py                  section F1:  state licences, DEA and NPI
    memberships.py                section I:   professional organizations, societies
    mentoring.py                  section N:   current / past mentees, outcomes
    other_education.py            section B2:  non-degree training and certificates
    passthrough.py                sections E and G: copied straight from the source CV
    patents.py                    section M2D: patents and inventions
    personal_data.py              section A:   name, addresses, phones, emails
    positions.py                  section D:   academic / hospital / other appointments
    postdoc_training.py           section C:   postdoctoral training, C1 and C2
    presentations.py              section R:   invitations to speak
    research_summary.py           section M1:  the stage-4.5 research summary
    research_support.py           section M2:  grants -- current, past, pending
    researcher_profiles.py        section S0:  ORCID and other profile identifiers
    service.py                    section Q:   extramural professional responsibilities
    teaching.py                   section K:   teaching activities, K1-K5

A module holds one section's writer plus only the helpers that section calls. A
helper reached from two sections is not exclusive to either and stays on
`WCMTemplateGenerator`.

`passthrough.py` is the one file that is not one section. E (employment status)
and G (hospital affiliation) have no writer of their own: they share a single
entry point that is dispatched once from `generate`, and they are the only two
sections filled by copying source text rather than by re-rendering stage-4
fields. Splitting them would leave three files where the boundary is one idea.
"""

# ponytail: mixins, not composition -- the writers are stateful and this keeps the
# move byte-identical with zero call-site churn. The ceiling is that a section
# mixin is not independently instantiable (it calls shared helpers that live on
# WCMTemplateGenerator). Promote to explicit collaborator objects if a section ever
# needs to be used or tested standalone.

# Nothing under this package may import `stage_6_word_template`: that module
# imports this one, so a back-edge is an import cycle and fails at load rather
# than at render. Every name a section writer needs is importable from
# `unified_pipeline.stage6.*`, from `unified_pipeline.core.*`, or from the
# standard library.

from .administrative_activities import AdministrativeActivitiesSection  # noqa: F401
from .appendix import AppendixSection  # noqa: F401
from .bibliography import BibliographySection  # noqa: F401
from .board_certification import BoardCertificationSection  # noqa: F401
from .clinical_practice import ClinicalPracticeSection  # noqa: F401
from .education import EducationSection  # noqa: F401
from .honors import HonorsSection  # noqa: F401
from .leadership import LeadershipSection  # noqa: F401
from .licensure import LicensureSection  # noqa: F401
from .memberships import MembershipsSection  # noqa: F401
from .mentoring import MentoringSection  # noqa: F401
from .other_education import OtherEducationSection  # noqa: F401
from .passthrough import PassthroughSection  # noqa: F401
from .patents import PatentsSection  # noqa: F401
from .personal_data import PersonalDataSection  # noqa: F401
from .positions import PositionsSection  # noqa: F401
from .postdoc_training import PostdocTrainingSection  # noqa: F401
from .presentations import PresentationsSection  # noqa: F401
from .research_summary import ResearchSummarySection  # noqa: F401
from .research_support import ResearchSupportSection  # noqa: F401
from .researcher_profiles import ResearcherProfilesSection  # noqa: F401
from .service import ServiceSection  # noqa: F401
from .teaching import TeachingSection  # noqa: F401
