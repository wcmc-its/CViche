"""Computes stable hashes of prompt templates for version tracking."""
import hashlib
import re


def hash_prompt_template(template: str) -> str:
    """Hash a prompt template, stripping variable content for stability.

    Replaces content between common template markers ({...} and <...>)
    with placeholders so that the hash remains stable across different
    variable values.

    Returns the first 16 hex characters of the SHA-256 digest.
    """
    # Remove content between common template markers
    normalized = re.sub(r'\{[^}]+\}', '{VAR}', template)
    normalized = re.sub(r'<[^>]+>', '<VAR>', normalized)
    normalized = normalized.strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
