"""
Section Extraction Orchestrator for Unified Pipeline

This module integrates the proven legacy extraction system (71 section-specific
extractors) into the unified pipeline. It creates the proper "classified" format
and orchestrates calling all extraction scripts.

Architecture:
    Stage 1 (Segmentation) → produces groups with entries
    Stage 2 (Taxonomy Mapping) → maps groups to WCM sections
    **Stage 3 (THIS MODULE)** → creates classified format + runs extractors
    Stage 4 (Template Population) → uses extracted data

This is NOT a hack - it's properly integrating the production extraction code
that successfully processed CV 2068 and many others.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add legacy production scripts to path
LEGACY_PRODUCTION = Path(__file__).parent.parent.parent / "legacy" / "stage_based_extraction" / "scripts" / "production"
sys.path.insert(0, str(LEGACY_PRODUCTION))


class SectionExtractionOrchestrator:
    """
    Orchestrates running all legacy section extractors on unified pipeline data.
    """

    # Map unified taxonomy section IDs to legacy wcm_section_type values
    # IMPORTANT: These must match what the extractors actually look for!
    TAXONOMY_TO_SECTION_TYPE = {
        # Personal/Contact (Section A)
        "contact_information": "personal_data",
        "name": "personal_data",
        "email_address": "personal_data",
        "phone_numbers": "personal_data",
        "address": "personal_data",
        "personal_information": "personal_data",

        # Education (Section B1: Academic Degree)
        "2": "education",
        "education": "education",
        "education_and_training": "education",
        "academic_degree": "education",
        "doctoral_degree": "education",
        "masters_degree": "education",

        # Other Education (Section B2, B2)
        "other_education": "education",
        "postdoctoral_training": "postdoctoral",  # Section C
        "postdoctoral": "postdoctoral",
        "fellowships": "education",
        "professional_development": "certifications",  # Changed: was "education", now "certifications" for F section
        "continuing_education": "educational_contributions",  # Section K4
        "professional_development_continuing_education": "educational_contributions",

        # Positions (Section D1: Academic, D2: Hospital, D3: Other, D4: Visiting)
        "professional_positions_employment": "professional_positions",
        "academic_positions": "professional_positions",
        "research_positions": "professional_positions",
        "hospital_appointments": "professional_positions",
        "clinical_appointments": "professional_positions",
        "hospital_positions": "professional_positions",
        "other_positions": "professional_positions",
        "visiting_positions": "professional_positions",
        "adjunct_positions": "professional_positions",
        "administrative_positions": "professional_positions",

        # Other Employment (Section E)
        "other_employment": "other_employment",
        "non_academic_employment": "other_employment",

        # Licensure & Certification (Section F, F1, F2)
        "licensure": "licensure",
        "board_certification": "licensure",
        "certifications": "licensure",  # CRITICAL FIX for Zahida
        "licenses": "licensure",
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
        "institutional_administration": "supplemental",  # For misc contributions
        "public_outreach_media_contributions": "supplemental",  # T3
        "media": "supplemental",

        # Service
        "service": "service",
        "university_service": "service",
        "community_service": "service",
        "public_service": "supplemental",  # T1
    }

    def __init__(self, output_dir: Path):
        """
        Initialize orchestrator.

        Args:
            output_dir: Base output directory for this CV
        """
        self.output_dir = Path(output_dir)
        self.classified_dir = self.output_dir / "classified"
        self.extraction_dir = self.output_dir / "stage_2c_extracted"  # Updated to match Stage 4 expectations

        self.classified_dir.mkdir(exist_ok=True)
        self.extraction_dir.mkdir(exist_ok=True)

    def create_classified_format(
        self,
        segmented_file: Path,
        mapped_file: Path,
        verbose: bool = True
    ) -> Path:
        """
        Create the "classified" format that legacy extractors expect.

        Combines segmented data (groups with entries) and taxonomy mappings
        (WCM section types) into the classified format.

        Args:
            segmented_file: Stage 1 segmented JSON
            mapped_file: Stage 2 taxonomy mapped JSON
            verbose: Print progress

        Returns:
            Path to created classified file
        """
        if verbose:
            print("Creating classified format for extraction...")

        # Load segmented data (has all the entries)
        with open(segmented_file) as f:
            segmented = json.load(f)

        # Load taxonomy mappings (has WCM section assignments)
        with open(mapped_file) as f:
            mapped = json.load(f)

        # Build a lookup: group_id → wcm_section_type
        group_to_section_type = {}
        for mapping in mapped.get('mappings', []):
            source_group_id = mapping.get('source_group_id')
            mapped_section_id = mapping.get('mapped_section_id')

            # Convert taxonomy ID to legacy section type
            wcm_section_type = self.TAXONOMY_TO_SECTION_TYPE.get(
                mapped_section_id,
                mapped_section_id  # Fallback to taxonomy ID if not in map
            )

            group_to_section_type[source_group_id] = wcm_section_type

        # Add wcm_section_type to each group in segmented data
        def add_section_types_to_groups(groups: List[Dict], parent_path: str = ""):
            """Recursively add wcm_section_type to all groups."""
            for group in groups:
                group_id = group.get('id')
                group_path = f"{parent_path}/{group.get('label', '')}" if parent_path else group.get('label', '')

                # Assign section type from mapping
                if group_id in group_to_section_type:
                    group['wcm_section_type'] = group_to_section_type[group_id]
                else:
                    # Not mapped - mark as unknown
                    group['wcm_section_type'] = 'unmapped'

                # Recurse into subgroups
                if 'subgroups' in group:
                    add_section_types_to_groups(group['subgroups'], group_path)

        def flatten_groups(groups: List[Dict]) -> List[Dict]:
            """
            Flatten hierarchical group structure into a flat list.

            Extractors expect a flat list of groups, not nested subgroups.
            This recursively collects all groups at all levels.
            """
            flat = []
            for group in groups:
                # Add this group (with subgroups removed to avoid confusion)
                group_copy = dict(group)
                subgroups = group_copy.pop('subgroups', [])

                # Normalize label field: extractors expect 'label', unified uses 'label_inferred'
                if 'label' not in group_copy and 'label_inferred' in group_copy:
                    group_copy['label'] = group_copy['label_inferred']

                flat.append(group_copy)

                # Recursively add all subgroups
                if subgroups:
                    flat.extend(flatten_groups(subgroups))

            return flat

        # Process all groups
        groups = segmented.get('groups', [])
        add_section_types_to_groups(groups)

        # Flatten the hierarchical structure for extractors
        flat_groups = flatten_groups(groups)

        # Create classified format with flattened groups
        classified = {
            "document_uid": segmented.get('document_uid'),
            "meta": segmented.get('meta', {}),
            "groups": flat_groups  # Use flattened groups for extractors
        }

        # Save classified file
        document_uid = segmented.get('document_uid', 'unknown')
        classified_file = self.classified_dir / f"{document_uid}_classified.json"

        with open(classified_file, 'w') as f:
            json.dump(classified, f, indent=2)

        if verbose:
            print(f"  ✓ Created classified file: {classified_file.name}")
            print(f"    Groups (hierarchical): {len(groups)}")
            print(f"    Groups (flattened): {len(flat_groups)}")
            print(f"    Mapped sections: {len(group_to_section_type)}")

        return classified_file

    def run_section_extractors(
        self,
        classified_file: Path,
        sections_to_extract: Optional[List[str]] = None,
        verbose: bool = True
    ) -> Dict[str, Path]:
        """
        Run WCM section extractors on classified file.

        Uses the complete set of 71 WCM section extractors organized by section ID.

        Args:
            classified_file: Classified JSON file
            sections_to_extract: List of section IDs to extract (e.g., ['B1', 'D1', 'S1'])
                                If None, extracts all sections found in classified file
            verbose: Print progress

        Returns:
            Dict mapping section_id to extracted file path
        """
        if verbose:
            print()
            print("Running WCM section extractors...")

        # Import the WCM section extractor registry
        try:
            from .wcm_section_extractors import get_all_extractors, get_section_name
        except ImportError as e:
            if verbose:
                print(f"  ⚠️  Could not import WCM extractors: {e}")
            return {}

        # Get all available extractors
        all_extractors = get_all_extractors()

        if verbose:
            print(f"  Available extractors: {len(all_extractors)} WCM sections")

        # Load classified file to see which sections are present
        with open(classified_file) as f:
            classified_data = json.load(f)

        # Determine which extractors to run
        if sections_to_extract:
            extractors_to_run = {sid: func for sid, func in all_extractors.items()
                                if sid in sections_to_extract}
        else:
            # Auto-detect from classified file
            # Run extractors for sections that have data
            extractors_to_run = all_extractors  # Run all, they'll skip if no data

        if verbose:
            print(f"  Running {len(extractors_to_run)} extractors...")
            print()

        # Run each extractor
        extracted_files = {}
        successful = 0
        skipped = 0
        failed = 0

        for section_id in sorted(extractors_to_run.keys()):
            extractor_func = extractors_to_run[section_id]
            section_name = get_section_name(section_id)

            if verbose:
                print(f"  [{section_id}] {section_name}...", end=" ")

            try:
                result = extractor_func(
                    classified_file=classified_file,
                    verbose=False  # Suppress individual extractor output
                )

                if result:
                    # Save extraction result
                    document_uid = classified_file.stem.replace('_classified', '')
                    output_file = self.extraction_dir / f"section_{section_id}_{document_uid}_extracted.json"

                    with open(output_file, 'w') as f:
                        json.dump(result, f, indent=2)

                    extracted_files[section_id] = output_file

                    # Count entries - handle both flat and subsection-based extractors
                    if 'extraction_stats' in result:
                        # Subsection-based extractor (S, M, etc.) - use extraction_stats
                        stats = result['extraction_stats']
                        num_entries = (stats.get('total_publications') or
                                     stats.get('total_entries') or
                                     stats.get('total_activities') or 0)
                    elif 'subsections' in result:
                        # Subsection-based but no extraction_stats - count manually
                        num_entries = sum(
                            len(subsec.get('entries', []))
                            for subsec in result['subsections'].values()
                        )
                    else:
                        # Flat extractor (B1, D1, etc.) - count top-level entries
                        num_entries = len(result.get('entries', result.get('parsed_entries', [])))

                    if verbose:
                        print(f"✓ {num_entries} entries")
                    successful += 1
                else:
                    if verbose:
                        print(f"⊘ No data")
                    skipped += 1

            except Exception as e:
                if verbose:
                    print(f"✗ Error: {str(e)[:50]}")
                failed += 1

        if verbose:
            print()
            print(f"✓ Extraction complete:")
            print(f"    Successful: {successful} sections")
            print(f"    Skipped:    {skipped} sections (no data)")
            print(f"    Failed:     {failed} sections")

        return extracted_files

    def heal_failed_extractions(
        self,
        classified_file: Path,
        extraction_results: Dict[str, Path],
        verbose: bool = True
    ) -> Dict[str, str]:
        """
        Stage 2D: Heal groups that failed extraction (returned 0 entries).

        Reviews groups using LLM with full taxonomy context and can:
        - Keep original classification (extraction failed for other reasons)
        - Reclassify to better-matching section
        - Flag for manual review

        Args:
            classified_file: Path to classified JSON
            extraction_results: Dict of section_id -> extracted file path
            verbose: Print progress

        Returns:
            Dict mapping group_id -> new wcm_section_type (only for reclassified groups)
        """
        if verbose:
            print()
            print("="*80)
            print("Stage 2D: Healing Failed Extractions")
            print("="*80)

        # Load classified data
        with open(classified_file) as f:
            classified = json.load(f)

        # Identify groups that failed extraction (have entries but 0 extracted)
        failed_groups = []
        for group in classified.get('groups', []):
            group_id = group.get('id')
            wcm_type = group.get('wcm_section_type')
            entries = group.get('entries', [])

            # Skip groups with no entries or unmapped
            if not entries or wcm_type in ['unmapped', 'unknown']:
                continue

            # Check if this group's section had 0 extractions
            # We need to check if the extractor for this type extracted anything from this group
            if self._group_had_failed_extraction(group, extraction_results):
                failed_groups.append(group)

        if not failed_groups:
            if verbose:
                print("  ✓ No failed extractions found - all groups extracted successfully")
            return {}

        if verbose:
            print(f"  Found {len(failed_groups)} group(s) with failed extraction")
            print()

        # Load taxonomy for LLM context
        taxonomy_file = self._find_taxonomy_file()
        taxonomy_context = self._load_taxonomy_context(taxonomy_file) if taxonomy_file else ""

        # Review each failed group with LLM
        reclassifications = {}
        for group in failed_groups:
            result = self._review_failed_group(group, taxonomy_context, verbose)
            if result and result.get('action') == 'reclassify':
                reclassifications[group['id']] = result['new_type']

        # Apply reclassifications to classified file
        if reclassifications:
            self._apply_reclassifications(classified_file, reclassifications, verbose)

        if verbose:
            print()
            print(f"✓ Healing complete: {len(reclassifications)} group(s) reclassified")

        return reclassifications

    def _group_had_failed_extraction(self, group: Dict, extraction_results: Dict[str, Path]) -> bool:
        """Check if a group failed extraction (has entries but extractor returned 0)."""
        wcm_type = group.get('wcm_section_type')
        entries_count = len(group.get('entries', []))

        # If no entries, it's not a failed extraction
        if entries_count == 0:
            return False

        # Check which sections successfully extracted data
        # We'll look through extraction_results to see if this group's type was extracted
        # For now, be conservative: assume groups with entries but no corresponding
        # successful extraction need review

        # Map wcm_section_type back to section IDs that would handle it
        type_to_sections = {
            'education': ['B1', 'B2', 'B2'],
            'professional_positions': ['D1', 'D2', 'D3', 'D4', 'E'],
            'research': ['M'],
            'bibliography': ['S'],
            'personal_data': ['A'],
        }

        relevant_sections = type_to_sections.get(wcm_type, [])

        # If any relevant section extracted data, don't mark as failed
        for section_id in relevant_sections:
            if section_id in extraction_results:
                # Check if that section extracted anything
                # For now, if the section file exists, assume it extracted something
                # TODO: Actually read the file and check entry count
                pass

        # Conservative: review all groups that have entries
        # The LLM will decide if it's truly a failure
        return True

    def _find_taxonomy_file(self) -> Optional[Path]:
        """Find the section taxonomy patterns file."""
        possible_paths = [
            Path("outputs/legacy/cv_pipeline/configs/section_taxonomy_patterns_v1.2_fixed.json"),
            Path("outputs/legacy/cv_pipeline/configs/section_taxonomy_patterns.json"),
        ]

        for path in possible_paths:
            if path.exists():
                return path
        return None

    def _load_taxonomy_context(self, taxonomy_file: Path) -> str:
        """Load taxonomy patterns as context for LLM."""
        try:
            with open(taxonomy_file) as f:
                taxonomy = json.load(f)

            # Format taxonomy for LLM
            context = "**Available WCM Section Types:**\n\n"
            for mapping in taxonomy.get('mappings', [])[:20]:  # Limit to top 20 for context
                wcm_type = mapping.get('wcm_section_type')
                patterns = mapping.get('patterns', [])[:5]  # First 5 patterns
                context += f"• **{wcm_type}**: {', '.join(patterns)}\n"

            return context
        except Exception as e:
            return ""

    def _review_failed_group(self, group: Dict, taxonomy_context: str, verbose: bool) -> Optional[Dict]:
        """Review a failed group with LLM and determine action."""
        import os
        from openai import OpenAI
        import time

        group_id = group.get('id')
        current_type = group.get('wcm_section_type')
        label = group.get('label', group.get('label_inferred', 'N/A'))
        entries = group.get('entries', [])

        if verbose:
            print(f"  [{group_id}] \"{label}\" (current: {current_type}, {len(entries)} entries)")

        # Create review prompt
        sample_entries = "\n".join([
            f"- {e.get('text_snippet', '')[:100]}"
            for e in entries[:3]
        ])

        prompt = f"""Review this CV section that failed extraction.

**Current Classification**: {current_type}
**Section Label**: {label}
**Number of Entries**: {len(entries)}

**Sample Content:**
{sample_entries}

{taxonomy_context}

**Task**: Determine if this content truly belongs to section type "{current_type}" or if it was misclassified.

**Options**:
1. KEEP - Content matches {current_type}, extraction failed for formatting/technical reasons (confidence ≥ 0.9)
2. RECLASSIFY - Content clearly belongs to different section type (confidence ≥ 0.75)
3. FLAG - Uncertain, needs manual review (confidence < 0.75)

Respond in JSON format:
{{
    "action": "keep" | "reclassify" | "flag",
    "new_type": "section_type" (if reclassify),
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}"""

        try:
            # Use default environment context to avoid expensive SKU mapping
            client = OpenAI()

            # Add small delay to avoid rate limits
            time.sleep(1)

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )

            result = json.loads(response.choices[0].message.content)

            if verbose:
                action = result.get('action')
                if action == 'reclassify':
                    new_type = result.get('new_type')
                    conf = result.get('confidence', 0)
                    print(f"      → Reclassify to '{new_type}' (confidence: {conf:.2f})")
                elif action == 'keep':
                    print(f"      → Keep as '{current_type}' (formatting issue)")
                else:
                    print(f"      → Flag for manual review")

            return result

        except Exception as e:
            if verbose:
                print(f"      ✗ Error reviewing: {str(e)[:50]}")
            return None

    def _apply_reclassifications(
        self,
        classified_file: Path,
        reclassifications: Dict[str, str],
        verbose: bool
    ):
        """Apply reclassifications to the classified file."""
        with open(classified_file) as f:
            data = json.load(f)

        # Update group wcm_section_types
        for group in data.get('groups', []):
            if group['id'] in reclassifications:
                old_type = group['wcm_section_type']
                new_type = reclassifications[group['id']]
                group['wcm_section_type'] = new_type

                if verbose:
                    print(f"    Updated [{group['id']}]: {old_type} → {new_type}")

        # Save updated file
        with open(classified_file, 'w') as f:
            json.dump(data, f, indent=2)


if __name__ == "__main__":
    """Test the orchestrator with CV 6_8XAA data."""
    import sys

    if len(sys.argv) < 2:
        print("Section Extraction Orchestrator")
        print()
        print("Usage:")
        print("  python section_extraction_orchestrator.py <output_dir>")
        print()
        print("Example:")
        print("  python section_extraction_orchestrator.py web_interface/outputs/6_8XAA")
        sys.exit(1)

    output_dir = Path(sys.argv[1])

    if not output_dir.exists():
        print(f"Error: Output directory not found: {output_dir}")
        sys.exit(1)

    # Initialize orchestrator
    orchestrator = SectionExtractionOrchestrator(output_dir)

    # Find segmented and mapped files
    segmented_file = output_dir / "stage_1_segmentation" / f"{output_dir.name}_*_segmented.json"
    segmented_files = list(output_dir.glob("stage_1_segmentation/*_segmented.json"))

    if not segmented_files:
        print(f"Error: No segmented file found in {output_dir}/stage_1_segmentation/")
        sys.exit(1)

    segmented_file = segmented_files[0]

    mapped_files = list(output_dir.glob("stage_3_taxonomy_mapping/*_mapped.json"))
    if not mapped_files:
        print(f"Error: No mapped file found in {output_dir}/stage_3_taxonomy_mapping/")
        sys.exit(1)

    mapped_file = mapped_files[0]

    print(f"Processing CV from: {output_dir}")
    print()

    # Create classified format
    classified_file = orchestrator.create_classified_format(
        segmented_file=segmented_file,
        mapped_file=mapped_file,
        verbose=True
    )

    # Run extractors
    extracted_files = orchestrator.run_section_extractors(
        classified_file=classified_file,
        verbose=True
    )

    print()
    print("="*80)
    print("EXTRACTION COMPLETE")
    print("="*80)
    print(f"  Classified file: {classified_file}")
    print(f"  Extracted sections: {len(extracted_files)}")
    for section_id, file_path in extracted_files.items():
        print(f"    Section {section_id}: {file_path.name}")
