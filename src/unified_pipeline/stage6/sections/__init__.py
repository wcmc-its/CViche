"""One module per WCM template section (#398).

`stage_6_word_template.py` carried 30 `_fill_*` writers inside one class, so a
change to Service was reviewed in the same file as Publications. Each WCM
section has its own rules -- its own template tables, its own taxonomy codes,
its own rerouting quirks -- and almost nothing to say to its neighbours. That is
the boundary the files follow.

    clinical_practice.py  section L:  clinical practice, innovation, leadership
    honors.py             section H:  honors and awards
    mentoring.py          section N:  current / past mentees, outcomes
    personal_data.py      section A:  name, addresses, phones, emails
    positions.py          section D:  academic / hospital / other appointments
    research_support.py   section M2: grants -- current, past, pending
    service.py            section Q:  extramural professional responsibilities

A module holds one section's writer plus only the helpers that section calls. A
helper reached from two sections is not exclusive to either and stays on
`WCMTemplateGenerator`.
"""

# ponytail: mixins, not composition -- the writers are stateful and this keeps the
# move byte-identical with zero call-site churn. The ceiling is that a section
# mixin is not independently instantiable (it calls shared helpers that live on
# WCMTemplateGenerator). Promote to explicit collaborator objects if a section ever
# needs to be used or tested standalone.

# Nothing under this package may import `stage_6_word_template`: that module
# imports this one, so a back-edge is an import cycle and fails at load rather
# than at render. Every name a section writer needs is importable from
# `unified_pipeline.stage6.*` or from the standard library.

from .clinical_practice import ClinicalPracticeSection  # noqa: F401
from .honors import HonorsSection  # noqa: F401
from .mentoring import MentoringSection  # noqa: F401
from .personal_data import PersonalDataSection  # noqa: F401
from .positions import PositionsSection  # noqa: F401
from .research_support import ResearchSupportSection  # noqa: F401
from .service import ServiceSection  # noqa: F401
