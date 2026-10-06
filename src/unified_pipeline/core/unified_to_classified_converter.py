"""
Unified-to-Classified Converter

Converts unified pipeline Stage 2 (taxonomy mapping) output to the "classified" format
that legacy Stage 2C extractors expect.

This enables using the proven legacy extractors while keeping modern segmentation/mapping.

Input:
  - Stage 1 segmented.json (groups with entries)
  - Stage 2 mapped.json (taxonomy classifications)

Output:
  - classified.json (legacy format with wcm_section_type on groups)
"""

import json
from pathlib import Path
from typing import Any


# Map unified taxonomy IDs to legacy wcm_section_type values
# IMPORTANT: These must match what the legacy extractors actually look for!
TAXONOMY_TO_WCM_TYPE = {
    # WCM NUMERIC SECTION IDS (returned by LLM mapper)
    "1": "personal_data",  # Contact Information
    "2": "education",  # Education and Training
    "3": "postdoctoral",  # Postdoctoral Training
    "4": "professional_positions",  # Professional Positions & Employment
    "5": "other_employment",  # Employment Status
    "6": "licensure",  # Licensure and Certification
    "7": "institutional_affiliation",  # Institutional/Hospital Affiliation
    "8": "honors_awards",  # Honors and Awards
    "9": "professional_organizations",  # Professional Organizations/Societies
    "10": "percent_effort",  # Percent Effort/Responsibilities
    "11": "educational_contributions",  # Educational Contributions
    "12": "clinical_service",  # Clinical Practice Innovation/Leadership
    "13": "research",  # Research Overview
    "14": "mentoring",  # Mentoring
    "15": "institutional_leadership",  # Institutional Leadership
    "16": "institutional_administration",  # Institutional Administration (committee service - Section P)
    "17": "extramural_responsibilities",  # Extramural Professional Activities
    "18": "presentations",  # Invitations to Speak/Present
    "19": "bibliography",  # Bibliography

    # Personal/Contact (Section A)
    "contact_information": "personal_data",
    "name": "personal_data",
    "email_address": "personal_data",
    "phone_numbers": "personal_data",
    "address": "personal_data",
    "personal_information": "personal_data",
    "personal_data": "personal_data",

    # Education (Section B1: Academic Degree)
    "education": "education",
    "education_and_training": "education",
    "academic_degree": "education",
    "doctoral_degree": "education",
    "masters_degree": "education",
    "undergraduate_education": "education",
    "graduate_education": "education",

    # Other Education (Section B2, B2)
    "other_education": "education",
    "postdoctoral_training": "postdoctoral",  # Section C
    "postdoctoral": "postdoctoral",
    "postgraduate_training": "postdoctoral",
    "fellowships": "education",
    "professional_development": "certifications",  # Changed: was "certifications", now maps to licensure extractors (F)
    "continuing_education": "educational_contributions",  # Section K4
    "professional_development_continuing_education": "educational_contributions",
    "professional_development_and_continuing_education": "certifications",

    # Positions (Section D1: Academic, D2: Hospital, D3: Other, D4: Visiting)
    "professional_positions_employment": "professional_positions",  # FIXED: was "positions"
    "academic_positions": "professional_positions",  # FIXED: was "positions"
    "research_positions": "professional_positions",
    "hospital_appointments": "professional_positions",
    "clinical_appointments": "professional_positions",
    "hospital_positions": "professional_positions",
    "other_positions": "professional_positions",
    "visiting_positions": "professional_positions",
    "adjunct_positions": "professional_positions",
    "administrative_positions": "professional_positions",
    "positions": "professional_positions",  # FIXED: was "positions"

    # Other Employment (Section E)
    "other_employment": "other_employment",
    "non_academic_employment": "other_employment",

    # Licensure & Certification (Section F, F1, F2)
    "licensure": "licensure",
    "board_certification": "licensure",
    "certifications": "licensure",  # FIXED: was "certifications", now "licensure"
    "licenses": "licensure",  # FIXED: was "certifications", now "licensure"
    "professional_licenses": "licensure",

    # Institutional Affiliation (Section G)
    "institutional_affiliation": "institutional_affiliation",
    "hospital_affiliation": "institutional_affiliation",

    # Honors & Awards (Section H)
    "honors_and_awards": "honors_awards",
    "honors_awards": "honors_awards",
    "awards": "honors_awards",
    "distinctions": "honors_awards",
    "fellowships_awards": "honors_awards",
    "honors": "honors_awards",  # FIXED: was "honors"

    # Professional Organizations (Section I)
    "professional_memberships": "professional_organizations",
    "professional_organizations": "professional_organizations",
    "memberships": "professional_organizations",
    "professional_affiliations": "professional_organizations",

    # Percent Effort (Section J)
    "percent_effort": "percent_effort",
    "effort_allocation": "percent_effort",

    # Educational Contributions (Section K1-K9)
    "educational_contributions": "educational_contributions",
    "didactic_teaching": "educational_contributions",  # K1
    "clinical_teaching": "educational_contributions",  # K2
    "educational_leadership": "educational_contributions",  # K3
    "student_advising": "educational_contributions",  # K5
    "curriculum_development": "educational_contributions",  # K6
    "program_development": "educational_contributions",  # K7
    "educational_scholarship": "educational_contributions",  # K8
    "educational_materials": "educational_contributions",  # K9
    "teaching": "educational_contributions",

    # Clinical Service (Section L1-L4)
    "clinical_service": "clinical_service",
    "clinical_care": "clinical_service",
    "patient_care": "clinical_service",

    # Research (Section M, M1-M4)
    "research_overview": "research",
    "research": "research",
    "research_interests": "research",
    "objective": "research",  # Objective/research statement goes to M1
    "research_activities": "research",  # M1
    "research_support": "research",  # M2 - grants
    "grant_support_funding": "research",  # M2
    "grants": "research",  # M2
    "current_funding": "research",  # M2
    "pending_funding": "research",  # M2
    "patents_inventions": "research",  # M3
    "clinical_trials": "research",  # M4

    # Mentoring (Section N, N1-N6)
    "mentoring": "mentoring",
    "advising": "mentoring",
    "supervision": "mentoring",
    "research_mentoring": "mentoring",  # N subsections
    "clinical_mentoring": "mentoring",
    "educational_mentoring": "mentoring",

    # Institutional Leadership (Section O)
    "institutional_leadership": "institutional_leadership",
    "leadership": "institutional_leadership",
    "committee_service": "institutional_leadership",

    # Administrative Activities (Section P)
    "administrative_activities": "administrative",
    "institutional_administrative_activities": "administrative",
    "administrative": "administrative",
    "institutional_service": "service",

    # Extramural Responsibilities (Section Q1-Q5)
    "#17": "extramural_responsibilities",  # Q: External professional activities
    "extramural_responsibilities": "extramural_responsibilities",
    "extramural_professional_responsibilities": "extramural_responsibilities",
    "external_service": "extramural_responsibilities",
    "boards_committees": "extramural_responsibilities",  # Q1
    "editorial_activities": "extramural_responsibilities",  # Q2
    "reviewer_activities": "extramural_responsibilities",  # Q3

    # Presentations (Section R)
    "presentations": "presentations",
    "invitations_speak_present": "presentations",
    "invited_presentations": "presentations",
    "invited_lectures": "presentations",
    "talks": "presentations",
    "invited_talks": "presentations",

    # Bibliography (Section S, S1-S15)
    "peer_reviewed_articles": "bibliography",
    "publications": "bibliography",
    "bibliography": "bibliography",
    "peer_reviewed_research_articles": "bibliography",  # S1
    "reviews_editorials": "bibliography",  # S2
    "letters_commentaries": "bibliography",  # S3
    "chapters": "bibliography",  # S4
    "books": "bibliography",  # S5
    "case_reports": "bibliography",  # S6
    "review_submitted_preparation": "bibliography",  # S7
    "abstracts_conference_proceedings": "bibliography",  # S8
    "non_peer_reviewed_research": "bibliography",  # S9
    "preprints": "bibliography",  # S10
    "software_code": "bibliography",  # S11
    "protocols_methods": "bibliography",  # S13

    # Supplemental (Section T, T1-T7)
    "supplemental": "supplemental",
    "languages": "supplemental",  # T: Languages
    "references": "supplemental",  # T: References
    "technical_skills": "supplemental",  # T4: Technical Skills
    "conferences": "supplemental",  # T: Conference attendance
    "conference_attendance": "supplemental",
    "public_outreach_media_contributions": "supplemental",  # T3
    "media": "supplemental",

    # Service (Institutional Administration)
    "institutional_administration": "service",  # Committee service and administrative roles
    "service": "service",
    "university_service": "service",
    "community_service": "service",
    "public_service": "supplemental",  # T1
    "committee_memberships": "service",
}


def convert_unified_to_classified(
    segmented_file: Path,
    mapped_file: Path,
    output_file: Path,
    verbose: bool = True
) -> dict[str, Any]:
    """
    Convert unified Stage 1+2 output to legacy classified format.

    Args:
        segmented_file: Path to unified Stage 1 segmented.json
        mapped_file: Path to unified Stage 2 mapped.json
        output_file: Path to write classified.json
        verbose: Print progress

    Returns:
        Classified data dictionary
    """
    if verbose:
        print("="*80)
        print("CONVERTING UNIFIED → CLASSIFIED FORMAT")
        print("="*80)
        print(f"Segmented: {segmented_file.name}")
        print(f"Mapped: {mapped_file.name}")
        print()

    # Load unified outputs
    with open(segmented_file, encoding="utf-8") as f:
        segmented_data = json.load(f)

    with open(mapped_file, encoding="utf-8") as f:
        mapped_data = json.load(f)

    # Build mapping from group_id → taxonomy
    group_to_taxonomy = {}
    for mapping in mapped_data.get('mappings', []):
        group_id = mapping.get('source_group_id')
        taxonomy_id = mapping.get('mapped_section_id')
        confidence = mapping.get('confidence', 0.0)

        if group_id and taxonomy_id:
            # Map to legacy wcm_section_type
            wcm_type = TAXONOMY_TO_WCM_TYPE.get(taxonomy_id, taxonomy_id)
            group_to_taxonomy[group_id] = {
                'wcm_section_type': wcm_type,
                'taxonomy_id': taxonomy_id,
                'canonical_name': mapping.get('mapped_canonical_name', ''),
                'confidence': confidence
            }

    # Recursively add wcm_section_type to all groups
    def classify_group(group: dict) -> dict:
        """Add wcm_section_type to a group and its subgroups."""
        group_id = group.get('id')  # Segmented data uses 'id' not 'group_id'

        # Ensure 'label' field exists (extractors expect 'label' not 'label_inferred')
        if 'label' not in group and 'label_inferred' in group:
            group['label'] = group['label_inferred']

        # Add classification if we have one
        if group_id in group_to_taxonomy:
            taxonomy_info = group_to_taxonomy[group_id]
            wcm_type = taxonomy_info['wcm_section_type']  # Already mapped at line 248

            # Skip unmapped taxonomy IDs (those that start with # or are still numeric after mapping)
            # Note: Valid numeric IDs like "2", "16" are now mapped to proper types (e.g., "education", "supplemental")
            if wcm_type.startswith('#') or wcm_type.isdigit():
                # Don't add wcm_section_type for unmapped IDs
                pass
            else:
                group['wcm_section_type'] = wcm_type
                group['wcm_taxonomy_id'] = taxonomy_info['taxonomy_id']
                group['wcm_canonical_name'] = taxonomy_info['canonical_name']
                group['wcm_confidence'] = taxonomy_info['confidence']

        # Recursively classify subgroups
        if 'subgroups' in group:
            group['subgroups'] = [classify_group(sg) for sg in group['subgroups']]

        return group

    # Create classified data structure
    # Segmented data has hierarchical structure - need to flatten subgroups to top-level groups
    sections = segmented_data.get('groups', [])

    # Flatten the hierarchy - extract all subgroups as top-level groups
    def flatten_groups(group: dict) -> list[dict]:
        """Recursively flatten group hierarchy."""
        # Make a copy to avoid mutating original
        import copy
        group_copy = copy.deepcopy(group)

        groups = []

        # Add this group (without subgroups)
        subgroups = group_copy.pop('subgroups', [])
        groups.append(group_copy)

        # Recursively flatten subgroups
        for subgroup in subgroups:
            groups.extend(flatten_groups(subgroup))

        return groups

    all_groups = []
    for section in sections:
        all_groups.extend(flatten_groups(section))

    # Now classify all flattened groups
    classified_groups = [classify_group(g) for g in all_groups]

    classified_data = {
        'document_uid': segmented_data.get('document_uid'),
        'source_file': str(segmented_file),
        'classification_source': 'unified_taxonomy_mapping',
        'groups': classified_groups,
        'meta': {
            **segmented_data.get('meta', {}),
            'taxonomy_stats': mapped_data.get('meta', {}).get('mapping_stats', {}),
            'groups_classified': len(group_to_taxonomy),
            'converter_version': '1.2'
        }
    }

    # Extract CV owner name if possible
    cv_owner = extract_cv_owner_from_classified(classified_data)
    if cv_owner:
        classified_data['cv_owner_name'] = cv_owner

    # Save classified output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, 'w', encoding="utf-8") as f:
        json.dump(classified_data, f, indent=2)

    if verbose:
        print(f"✓ Converted {len(group_to_taxonomy)} groups to classified format")
        if cv_owner:
            print(f"✓ Extracted CV owner: {cv_owner}")
        print(f"✓ Saved: {output_file.name}")
        print()

    return classified_data


def extract_cv_owner_from_classified(classified_data: dict) -> str:
    """
    Extract CV owner name from classified data.

    Looks for personal_data/contact_information groups and extracts the name.

    Returns:
        Name in "LastName, FirstName" format, or empty string
    """
    def find_name_in_group(group: dict) -> str:
        """Recursively search for name in group."""
        # Check if this is a personal data group
        wcm_type = group.get('wcm_section_type', '')
        if wcm_type in ['personal_data', 'contact_information']:
            # Look for name in entries
            for entry in group.get('entries', []):
                text = entry.get('text_snippet', '')
                # Simple heuristics to find name
                # Usually the first non-email, non-phone line
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                for line in lines:
                    # Skip emails and phones
                    if '@' in line or any(c.isdigit() for c in line):
                        continue
                    # This might be the name
                    if len(line.split()) >= 2:  # At least first and last name
                        parts = line.split()
                        # Convert "FirstName LastName" → "LastName, FirstName"
                        if len(parts) >= 2:
                            last_name = parts[-1]
                            first_names = ' '.join(parts[:-1])
                            return f"{last_name}, {first_names}"

        # Check subgroups
        for subgroup in group.get('subgroups', []):
            name = find_name_in_group(subgroup)
            if name:
                return name

        return ""

    # Search all groups
    for group in classified_data.get('groups', []):
        name = find_name_in_group(group)
        if name:
            return name

    return ""


if __name__ == '__main__':
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='Convert unified output to classified format')
    parser.add_argument('segmented_file', help='Path to Stage 1 segmented.json')
    parser.add_argument('mapped_file', help='Path to Stage 2 mapped.json')
    parser.add_argument('--output', '-o', required=True, help='Output classified.json path')

    args = parser.parse_args()

    convert_unified_to_classified(
        Path(args.segmented_file),
        Path(args.mapped_file),
        Path(args.output)
    )
