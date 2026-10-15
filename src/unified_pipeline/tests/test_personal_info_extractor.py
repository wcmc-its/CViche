"""Tests for `unified_pipeline/core/personal_info_extractor.py`."""

from unified_pipeline.core.personal_info_extractor import extract_name_regex


def test_a_credential_marks_a_name_line_without_name_structure():
    # Every word is two letters, so only the credential marks it a name, and
    # five words are too many for the last-resort fallback.
    assert extract_name_regex('Jo Ng Li Wu, MD') == 'Jo Ng Li Wu, MD'
    assert extract_name_regex('Jo Ng Li Wu, MX') is None
