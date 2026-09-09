"""
S7 Validation Rules - Prevent Published Articles from Being Labeled as Unpublished

Based on Phase 2 evaluation results showing 0% accuracy for S7:
- All 35 S7 records in batch 1 were actually published (had DOI, journal, year)
- Main error: Published articles with DOI mislabeled as S7 (unpublished)

This validator implements hard validation rules to prevent S7 misclassification.
"""

import re


# Known published indicators
PUBLISHED_INDICATORS = {
    'doi_patterns': [
        r'\bdoi:\s*10\.\d{4,}',  # doi: 10.xxxx
        r'https?://doi\.org/10\.\d{4,}',  # doi.org URL
        r'doi\.org/10\.\d{4,}',
        r'/doi/10\.\d{4,}/[\w\.\-]+',  # DOI in any URL: /doi/10.xxxx/yyyy
        r'doi\.org/10\.\d{4,}/[\w\.\-]+',  # doi.org with full DOI
    ],
    'pmid_patterns': [
        r'\bPMID:\s*\d{7,}',
        r'PubMed:\s*\d{7,}',
    ],
    'journal_volume_patterns': [
        r'\.\s+\d{4};?\d+\(\d+\)',  # . 2020;45(2)
        r'[A-Z][a-z]+\s+[A-Z][a-z]+\.\s+\d{4}',  # Nature Med. 2020
        r'\d{4}\s+[A-Z][a-z]+\s+\d+;',  # 2020 Mar 15;
    ],
}

# Review/editorial journals and keywords
REVIEW_INDICATORS = {
    'journals': [
        'Annual Review',
        'Nature Reviews',
        'Current Opinion',
        'Yearbook of',
        'Yearb Med Inform',
    ],
    'keywords': [
        'Ten quick tips',
        'Commentary:',
        'Editorial:',
        'Perspective:',
        'Opinion:',
        'The case for',
        'case for',
        'Viewpoint',
        'Letter to',
    ],
}

# Protocol/methods indicators
PROTOCOL_INDICATORS = {
    'journals': [
        'Current Protocols',
        'Curr Protoc',
        'STAR Protocols',
        'JoVE',
        'Journal of Visualized Experiments',
        'Methods Mol Biol',
    ],
}

# Standards/guidelines indicators
GUIDELINES_INDICATORS = {
    'keywords': [
        'Minimal Information',
        'Guidelines for',
        'Consensus Statement',
        'Standards for',
        'Recommendations for',
    ],
}

# Book chapter series
BOOK_CHAPTER_SERIES = [
    'Advances in Experimental Medicine and Biology',
    'Adv Exp Med Biol',
    'Methods in Molecular Biology',
    'Methods Mol Biol',
]


def check_doi_present(text: str) -> bool:
    """Check if DOI is present in text."""
    for pattern in PUBLISHED_INDICATORS['doi_patterns']:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def check_pmid_present(text: str) -> bool:
    """Check if PMID is present in text."""
    for pattern in PUBLISHED_INDICATORS['pmid_patterns']:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def check_journal_citation(text: str) -> bool:
    """Check if text has journal volume/issue citation format."""
    for pattern in PUBLISHED_INDICATORS['journal_volume_patterns']:
        if re.search(pattern, text):
            return True
    return False


def check_year_present(text: str) -> tuple[bool, int | None]:
    """Check if publication year is present (1990-2030)."""
    year_match = re.search(r'\b(19\d{2}|20[0-3]\d)\b', text)
    if year_match:
        return True, int(year_match.group(1))
    return False, None


def is_review_journal(text: str) -> bool:
    """Check if text mentions a known review journal."""
    for journal in REVIEW_INDICATORS['journals']:
        if journal.lower() in text.lower():
            return True
    return False


def has_review_keywords(text: str) -> bool:
    """Check for review/editorial keywords."""
    for keyword in REVIEW_INDICATORS['keywords']:
        if keyword.lower() in text.lower():
            return True
    return False


def is_protocol_journal(text: str) -> bool:
    """Check if text is from a protocol journal."""
    for journal in PROTOCOL_INDICATORS['journals']:
        if journal.lower() in text.lower():
            return True
    return False


def has_guidelines_keywords(text: str) -> bool:
    """Check for guidelines/standards keywords."""
    for keyword in GUIDELINES_INDICATORS['keywords']:
        if keyword.lower() in text.lower():
            return True
    return False


def is_book_chapter_series(text: str) -> bool:
    """Check if text is from a known book chapter series."""
    for series in BOOK_CHAPTER_SERIES:
        if series.lower() in text.lower():
            return True
    return False


def validate_s7_assignment(text: str, current_section: str) -> dict:
    """
    Validate if S7 (Unpublished) assignment is correct.

    Returns dict with:
    - is_valid: bool
    - should_be: str (alternative section if invalid)
    - reason: str (explanation)
    - confidence: int (1-5)
    """
    # If not currently S7, no validation needed
    if current_section != 'S7':
        return {'is_valid': True, 'should_be': current_section, 'reason': 'Not S7', 'confidence': 5}

    # Check for published indicators
    has_doi = check_doi_present(text)
    has_pmid = check_pmid_present(text)
    has_journal = check_journal_citation(text)
    has_year, year = check_year_present(text)

    # HARD RULE 1: DOI or PMID present → NOT unpublished
    if has_doi or has_pmid:
        # Determine correct section
        if is_protocol_journal(text):
            return {
                'is_valid': False,
                'should_be': 'S13',
                'reason': 'Has DOI/PMID and is from protocol journal (Current Protocols, etc.)',
                'confidence': 5
            }
        elif is_review_journal(text) or has_review_keywords(text):
            return {
                'is_valid': False,
                'should_be': 'S2',
                'reason': 'Has DOI/PMID and appears to be review/editorial',
                'confidence': 5
            }
        elif has_guidelines_keywords(text):
            return {
                'is_valid': False,
                'should_be': 'S14',
                'reason': 'Has DOI/PMID and contains guidelines/standards keywords',
                'confidence': 5
            }
        elif is_book_chapter_series(text):
            return {
                'is_valid': False,
                'should_be': 'S4',
                'reason': 'Has DOI/PMID and is from book chapter series',
                'confidence': 5
            }
        else:
            return {
                'is_valid': False,
                'should_be': 'S1',
                'reason': 'Has DOI/PMID - clearly published article',
                'confidence': 5
            }

    # HARD RULE 2: Journal citation format + year → published
    if has_journal and has_year:
        if is_protocol_journal(text):
            return {
                'is_valid': False,
                'should_be': 'S13',
                'reason': 'Has journal citation and is from protocol journal',
                'confidence': 5
            }
        elif is_review_journal(text) or has_review_keywords(text):
            return {
                'is_valid': False,
                'should_be': 'S2',
                'reason': 'Has journal citation and appears to be review/editorial',
                'confidence': 4
            }
        elif is_book_chapter_series(text):
            return {
                'is_valid': False,
                'should_be': 'S4',
                'reason': 'Has citation format and is from book chapter series',
                'confidence': 4
            }
        else:
            return {
                'is_valid': False,
                'should_be': 'S1',
                'reason': 'Has journal citation format with year - likely published',
                'confidence': 4
            }

    # If no published indicators found, S7 might be valid
    return {
        'is_valid': True,
        'should_be': 'S7',
        'reason': 'No DOI, PMID, or journal citation found',
        'confidence': 3
    }


def batch_validate_records(records: list[dict]) -> dict:
    """
    Validate a batch of records.

    Returns:
    - validation_results: List of validation results
    - summary: Stats about errors found
    """
    results = []
    errors_by_section = {}

    for record in records:
        text = record.get('text_snippet', record.get('raw_text', ''))
        current_section = record.get('section_id', '')
        entry_id = record.get('entry_id', record.get('id', ''))

        validation = validate_s7_assignment(text, current_section)

        if not validation['is_valid']:
            should_be = validation['should_be']
            if should_be not in errors_by_section:
                errors_by_section[should_be] = []
            errors_by_section[should_be].append({
                'entry_id': entry_id,
                'current': current_section,
                'should_be': should_be,
                'reason': validation['reason'],
                'confidence': validation['confidence']
            })

        results.append({
            'entry_id': entry_id,
            'validation': validation
        })

    return {
        'validation_results': results,
        'summary': {
            'total_checked': len(records),
            'errors_found': sum(len(errs) for errs in errors_by_section.values()),
            'errors_by_target_section': {
                section: len(errs) for section, errs in errors_by_section.items()
            }
        },
        'errors': errors_by_section
    }


def apply_corrections(mapped_data: dict, validation_results: dict) -> dict:
    """
    Apply validation corrections to mapped data.

    Only corrects high-confidence (4-5) errors.
    """
    corrections_applied = 0

    for result in validation_results['validation_results']:
        if not result['validation']['is_valid'] and result['validation']['confidence'] >= 4:
            entry_id = result['entry_id']
            new_section = result['validation']['should_be']

            # Find and update the record in mapped_data
            for mapping in mapped_data.get('mappings', []):
                # Find matching entry in this group
                # (Implementation depends on your data structure)
                pass  # TODO: Implement based on actual data structure

            corrections_applied += 1

    return {
        'corrected_data': mapped_data,
        'corrections_applied': corrections_applied
    }


if __name__ == '__main__':
    # Test with examples from batch 1
    test_cases = [
        {
            'text': 'Haendel MA, Chute C. (2020) The National COVID Cohort Collaborative. Journal of Medical Informatics. doi: 10.1093/jamia/ocaa196',
            'current': 'S7',
            'expected': 'S1'
        },
        {
            'text': 'Rubinstein YR. The case for open science. JAMIA Open. https://doi.org/10.1093/jamiaopen/ooaa030',
            'current': 'S7',
            'expected': 'S2'
        },
        {
            'text': 'Sobreira NLM. Matchmaker Exchange. Curr Protoc Hum Genet. 2017 Oct 18;95:9.31.1-9.31.15. doi: 10.1002/cphg.50.',
            'current': 'S7',
            'expected': 'S13'
        },
        {
            'text': 'Baynam G. Improved Diagnosis for Rare Diseases. Adv Exp Med Biol. 2017;1031:55-94. doi: 10.1007/978-3-319-67144-4_4',
            'current': 'S7',
            'expected': 'S4'
        },
    ]

    print("Testing S7 Validator\n" + "="*50)
    for i, test in enumerate(test_cases, 1):
        result = validate_s7_assignment(test['text'], test['current'])
        status = "✓" if result['should_be'] == test['expected'] else "✗"
        print(f"\n{status} Test {i}:")
        print(f"  Current: {test['current']}")
        print(f"  Expected: {test['expected']}")
        print(f"  Got: {result['should_be']}")
        print(f"  Reason: {result['reason']}")
        print(f"  Confidence: {result['confidence']}")
