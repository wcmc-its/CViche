"""
Book/Chapter Validator

Distinguishes published books/chapters (S3/S4) from teaching materials (K).
Critical for entries like "Introduction to Statistics" that could be either.
"""

import re
from .base_validator import BaseValidator, ValidatorGuidance


class BookChapterValidator(BaseValidator):
    """
    Disambiguates books/chapters (S3/S4) from teaching materials (K).

    Key distinctions:
    - S3/S4: Published books with ISBN, publisher, DOI, citation counts
    - K: Course materials with course codes, syllabi, curricula, lecture notes

    Patterns detected:
    - ISBN numbers → S3/S4
    - Major publishers (Oxford, Cambridge, Springer, Wiley) → S3/S4
    - Course codes (BIOL 101, PSY 301) → K
    - Teaching keywords (syllabus, curriculum, lecture) → K
    - Textbook vs reference book distinction
    """

    name = "BookChapterValidator"

    def applies_to(self) -> list[str]:
        """Applies to bibliography and teaching sections."""
        return ['S', 'bibliography', 'K', 'teaching']

    def priority(self) -> int:
        """High priority - critical for S3/S4 vs K disambiguation."""
        return 85

    # Published book/chapter indicators
    ISBN_PATTERNS = [
        r'\bISBN[\s:-]*\d{1,5}[-\s]?\d{1,7}[-\s]?\d{1,7}[-\s]?[\dX]\b',
        r'\bISBN[\s:-]*\d{13}\b',
    ]

    PUBLISHER_PATTERNS = [
        # Major academic publishers
        r'\b(Oxford University Press|OUP)\b',
        r'\b(Cambridge University Press|CUP)\b',
        r'\bSpringer\b',
        r'\bWiley(?:-Blackwell)?\b',
        r'\bElsevier\b',
        r'\bSage Publications?\b',
        r'\bTaylor & Francis\b',
        r'\bRoutledge\b',
        r'\bPearson\b',
        r'\bMcGraw-?Hill\b',
        r'\bAcademic Press\b',
        r'\bMIT Press\b',
        r'\bUniversity of Chicago Press\b',
        r'\bPrinceton University Press\b',
        r'\bYale University Press\b',
        r'\bHarvard University Press\b',
        r'\bColumbia University Press\b',

        # Publisher city indicators
        r'\bOxford\b.*\b(?:UK|England)\b',
        r'\bCambridge\b.*\b(?:UK|MA)\b',
        r'\bNew York\b.*\bNY\b',
    ]

    BOOK_STRUCTURE_PATTERNS = [
        r'\bChapter\s+\d+\b',
        r'\bpp?\.\s*\d+-\d+\b',  # Page ranges
        r'\bEdited by\b',
        r'\bEditor(?:s)?:\s*[A-Z]',
        r'\b\d+th\s+Edition\b',
        r'\bvolume\s+\d+\b',
    ]

    DOI_PATTERN = r'\bdoi:\s*10\.\d+/[^\s]+\b'

    # Teaching material indicators
    COURSE_CODE_PATTERNS = [
        r'\b[A-Z]{2,4}[\s-]?\d{3,4}[A-Z]?\b',  # BIOL 101, PSY-301A
        r'\bCourse\s+(?:Code|Number):\s*[A-Z]+[\s-]?\d+\b',
    ]

    TEACHING_KEYWORDS = [
        r'\bsyllabus\b',
        r'\bcurriculum\b',
        r'\bcourse\s+materials?\b',
        r'\blecture\s+notes\b',
        r'\bteaching\s+materials?\b',
        r'\binstructional\s+materials?\b',
        r'\bworkshop\s+materials?\b',
        r'\blab\s+manual\b',
        r'\bstudy\s+guide\b',
        r'\blesson\s+plan\b',
    ]

    TEXTBOOK_INDICATORS = [
        r'\btextbook\b',
        r'\bteaching\s+(?:text|resource)\b',
        r'\bfor\s+(?:students|undergraduates|graduates)\b',
    ]

    # Ambiguous: Could be either
    GENERIC_BOOK_TITLES = [
        r'\bIntroduction to\b',
        r'\bPrinciples of\b',
        r'\bFundamentals of\b',
        r'\bHandbook of\b',
        r'\bGuide to\b',
    ]

    def analyze(self, entry_text: str) -> ValidatorGuidance:
        """
        Analyze entry to distinguish books/chapters from teaching materials.

        Returns:
            Guidance for S3/S4 vs K classification
        """
        # DEFENSIVE: Handle dict entries (extract text if needed)
        if isinstance(entry_text, dict):
            entry_text = entry_text.get('text_snippet', '')

        # Ensure it's a string
        if not isinstance(entry_text, str):
            entry_text = str(entry_text) if entry_text else ''

        text_lower = entry_text.lower()

        # Check for published book indicators
        has_isbn = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.ISBN_PATTERNS
        )

        has_publisher = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.PUBLISHER_PATTERNS
        )

        has_book_structure = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.BOOK_STRUCTURE_PATTERNS
        )

        has_doi = bool(re.search(self.DOI_PATTERN, entry_text, re.IGNORECASE))

        # Check for teaching material indicators
        has_course_code = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.COURSE_CODE_PATTERNS
        )

        has_teaching_keywords = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.TEACHING_KEYWORDS
        )

        has_textbook_indicators = any(
            re.search(pattern, text_lower, re.IGNORECASE)
            for pattern in self.TEXTBOOK_INDICATORS
        )

        has_generic_title = any(
            re.search(pattern, entry_text, re.IGNORECASE)
            for pattern in self.GENERIC_BOOK_TITLES
        )

        # HARD ROUTING: Clear course material (course code + teaching keywords)
        if has_course_code and has_teaching_keywords:
            return ValidatorGuidance(
                exclude_sections=['S3', 'S4', 'S'],
                recommend_sections=['K', 'K1'],
                hints=[
                    'Entry contains course code + teaching keywords → Course materials (K)',
                    'Published books/chapters (S3/S4) would have ISBN or publisher',
                    'Not a scholarly publication'
                ],
                confidence=0.92,
                severity='hard',
                allow_override=True,
                deterministic_signals=['course_code_with_teaching_keywords']
            )

        # HARD ROUTING: Clear published book (ISBN or major publisher)
        if has_isbn or has_publisher:
            deterministic_signals = []
            hints = []

            if has_isbn:
                deterministic_signals.append('isbn_present')
                hints.append('ISBN present → Published book/chapter (S3/S4)')

            if has_publisher:
                deterministic_signals.append('major_publisher')
                hints.append('Major academic publisher → Scholarly publication (S3/S4)')

            # Distinguish book (S3) vs chapter (S4)
            is_chapter = bool(re.search(r'\bChapter\s+\d+\b', entry_text, re.IGNORECASE))

            if is_chapter:
                recommended = ['S4']  # Book chapter
                hints.append('Chapter number detected → Book Chapter (S4)')
            else:
                recommended = ['S3']  # Authored/edited book
                hints.append('No chapter number → Full book (S3)')

            return ValidatorGuidance(
                exclude_sections=['K', 'K1'],
                recommend_sections=recommended,
                hints=hints + [
                    'Not teaching materials (K) - this is a published scholarly work'
                ],
                confidence=0.95,
                severity='hard',
                allow_override=False,
                deterministic_signals=deterministic_signals
            )

        # SOFT GUIDANCE: Teaching keywords without course code
        if has_teaching_keywords and not has_publisher:
            return ValidatorGuidance(
                recommend_sections=['K', 'K1'],
                hints=[
                    'Teaching keywords detected → Likely course materials (K)',
                    'If published with ISBN/publisher, would be S3/S4'
                ],
                confidence=0.75,
                severity='soft',
                deterministic_signals=['teaching_keywords_no_publisher']
            )

        # SOFT GUIDANCE: Book structure without ISBN/publisher (possibly unpublished)
        if has_book_structure and not has_isbn and not has_publisher:
            return ValidatorGuidance(
                recommend_sections=['S3', 'S4'],
                hints=[
                    'Book structure detected (chapters, pages) but no ISBN/publisher',
                    'May be unpublished manuscript or teaching resource',
                    'Check for course context (K) vs publication context (S3/S4)'
                ],
                confidence=0.60,
                severity='soft',
                deterministic_signals=['book_structure_no_publisher']
            )

        # AMBIGUOUS: Textbook (could be S3 if published, K if course material)
        if has_textbook_indicators:
            if has_publisher or has_isbn:
                # Published textbook → S3
                return ValidatorGuidance(
                    recommend_sections=['S3'],
                    hints=[
                        'Published textbook → Authored Book (S3)',
                        'Has publication metadata (ISBN/publisher)'
                    ],
                    confidence=0.80,
                    severity='soft',
                    deterministic_signals=['published_textbook']
                )
            else:
                # Textbook reference without publication info → ambiguous
                return ValidatorGuidance(
                    recommend_sections=['K', 'S3'],
                    hints=[
                        'Textbook mentioned but unclear if published (S3) or course material (K)',
                        'Check for ISBN/publisher (S3) or course code (K)'
                    ],
                    confidence=0.50,
                    severity='soft',
                    deterministic_signals=['textbook_ambiguous']
                )

        # SOFT GUIDANCE: Generic title (could be either)
        if has_generic_title and not has_isbn and not has_course_code:
            return ValidatorGuidance(
                hints=[
                    'Generic title ("Introduction to...", "Principles of...") - ambiguous',
                    'Look for ISBN/publisher (S3/S4) or course code/syllabus (K)'
                ],
                confidence=0.50,
                severity='soft',
                deterministic_signals=['generic_title_ambiguous']
            )

        # DOI presence suggests publication (though rare for books)
        if has_doi:
            return ValidatorGuidance(
                recommend_sections=['S3', 'S4'],
                hints=['DOI present → Likely published work (S3/S4)'],
                confidence=0.70,
                severity='soft',
                deterministic_signals=['doi_present']
            )

        # No clear signals
        return ValidatorGuidance()

    def get_description(self) -> str:
        return "Distinguishes published books/chapters (S3/S4) from teaching materials (K)"
