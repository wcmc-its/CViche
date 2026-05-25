"""
CV Parsing Pipeline Orchestrator - Integrated Unified + Legacy System

This module orchestrates the complete CV processing pipeline, combining:
- Stages 1-3: Modern unified pipeline with LLM-powered extraction
- Stage 4: Legacy advanced handlers for WCM template formatting

Architecture
------------
Stage 1: Hierarchical Segmentation
    - Recursive traversal of nested CV sections (3+ levels)
    - Preserves document structure

Stage 2: LLM Taxonomy Mapping
    - Maps CV sections to WCM taxonomy using GPT-4o-mini
    - Confidence scoring
    - Example: "Research Experience" → "Professional Positions" (95%)

Stage 3: Intelligent Parsing
    - Uses Stage 2 taxonomy mappings (NOT keyword matching)
    - Structured data extraction with confidence scores
    - Publications, Education, Positions, Grants

Stage 4: WCM Template Population (Legacy Handlers)
    - Advanced bibliography formatting with subsection categorization
    - Author name abbreviation ("Zahida Y" not "Yaseen Zahida")
    - Author name bolding for CV owner
    - PMCID/PMID enrichment via PubMed API
    - Professional WCM formatting with Arial font

Usage
-----
CLI:
    from src.unified_pipeline.core.cv_pipeline import CVPipeline
    pipeline = CVPipeline("cv.docx")
    result = pipeline.run_full_pipeline()

Web App:
    pipeline = CVPipeline("cv.docx", progress_callback=async_callback)
    result = await pipeline.run_full_pipeline_async()

See Also
--------
- docs/guides/UNIFIED_LEGACY_INTEGRATION.md: Complete integration guide
- src/unified_pipeline/config.py: Centralized configuration
"""

import os
import sys
import json
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Awaitable
from datetime import datetime

# Import centralized configuration
from ..config import (
    get_template_path,
    calculate_cost,
    USE_LEGACY_HANDLERS,
    ENABLE_PMCID_ENRICHMENT,
    USE_TAXONOMY_MAPPING,
    OUTPUT_BASE
)

# Import pipeline stages
from .cv_segmenter import CVSegmenter
from .taxonomy_mapper import map_cv_sections
from ..cv_parser.taxonomy_utils import (
    get_section_by_code,
    section_id_to_code,
    get_section_by_id,
    validate_section_code,
    code_to_canonical_name
)
from ..parsers.publications_parser import parse_publications_section, extract_target_author_from_uid
from ..parsers.education_parser import parse_education_section
from ..parsers.positions_parser import parse_positions_section
from ..parsers.grants_parser import parse_grants_section
from ..parsers.certifications_parser import parse_certifications_section
from ..parsers.honors_parser import parse_honors_section
from ..parsers.memberships_parser import parse_memberships_section
from ..parsers.service_parser import parse_service_section
from ..parsers.licensure_parser import parse_licensure_section
from ..parsers.mentoring_parser import parse_mentoring_section
from .personal_info_extractor import extract_personal_info

# Import legacy handlers for advanced formatting
import sys
legacy_path = Path(__file__).parent.parent.parent / "legacy" / "stage_based_extraction" / "scripts" / "production"
if str(legacy_path) not in sys.path:
    sys.path.insert(0, str(legacy_path))

try:
    from template_navigator import load_template, insert_entry_list, find_section, find_table_after_section
    from section_special_handlers import (
        populate_section_s_bibliography,
        populate_section_a_personal_data,
        populate_section_m_research,
        format_author_name
    )
    from wcm_formatter import format_entry_to_wcm
    from enrich_publication_ids import PubMedEnricher
    LEGACY_HANDLERS_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Legacy handlers not available: {e}")
    LEGACY_HANDLERS_AVAILABLE = False
    from .wcm_template_filler_v2 import WCMTemplateFiller


class CVPipeline:
    """
    Orchestrates the complete CV parsing pipeline.

    Supports both synchronous (CLI) and asynchronous (web app) execution.
    """

    def __init__(
        self,
        cv_path: str,
        output_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None
    ):
        """
        Initialize pipeline with input CV path.

        Args:
            cv_path: Path to CV file (.docx or .pdf)
            output_dir: Optional base output directory (defaults to config OUTPUT_BASE)
            progress_callback: Optional async callback for progress updates
                              Signature: async def callback(stage: int, message: str, level: str = "INFO")
        """
        self.cv_path = Path(cv_path)

        if not self.cv_path.exists():
            raise FileNotFoundError(f"CV file not found: {cv_path}")

        # Set base output directory
        if output_dir:
            base_output_dir = Path(output_dir)
        else:
            # Use centralized config
            base_output_dir = OUTPUT_BASE

        # Progress callback for web app integration
        self.progress_callback = progress_callback

        # Entity type to section code mapping
        # Maps legacy entity types to their primary WCM section codes
        self.entity_to_section_code = {
            "publications": "S",   # Bibliography
            "education": "B",      # Education
            "positions": "D",      # Professional Positions
            "grants": "M",         # Research/Grants
            "certifications": "F", # Licensure and Certification
            "honors": "I",         # Honors and Awards
            "memberships": "H",    # Professional Organizations
            "service": "P",        # Institutional Administrative Activities
            "licensure": "F",      # Licensure (same as certifications)
            "mentoring": "N"       # Mentoring
        }

        # Create stage-specific output directories
        # NOTE: Stage numbering now includes 2a and 2b for entry delimitation/extraction
        self.stage_dirs = {
            "stage_1": base_output_dir / "stage_1_segmentation",
            "stage_2a": base_output_dir / "stage_2a_entry_delimitation",
            "stage_2b": base_output_dir / "stage_2b_extract_entries_from_delimiters",
            "stage_3": base_output_dir / "stage_3_taxonomy_mapping",
            "stage_3_parsing": base_output_dir / "stage_3_parsing",
            "stage_4": base_output_dir / "stage_4_wcm_templates",
            # Legacy compatibility - old "stage_2" pointed to taxonomy mapping
            "stage_2_legacy": base_output_dir / "stage_3_taxonomy_mapping"
        }
        # Per-entity-type parsing subdirs consumed by run_stage_3_section_parsing
        # and the entity-files fallback reader in run_stage_4_template_generation.
        for entity_type in self.entity_to_section_code:
            self.stage_dirs[f"stage_3_{entity_type}"] = (
                self.stage_dirs["stage_3_parsing"] / entity_type
            )

        # Create all directories
        for stage_dir in self.stage_dirs.values():
            stage_dir.mkdir(parents=True, exist_ok=True)

        # For backwards compatibility
        self.output_dir = base_output_dir

        # Initialize result storage
        self.results = {
            "input_file": str(self.cv_path),
            "pipeline_run_time": datetime.now().isoformat(),
            "output_base_dir": str(base_output_dir),
            "stages": {}
        }

        # Storage for extracted personal info (extracted early to filter from other sections)
        self.personal_info = None

    async def _log(self, stage: int, message: str, level: str = "INFO"):
        """
        Log a message. Calls progress_callback if provided, otherwise prints.

        Args:
            stage: Pipeline stage number (1-4)
            message: Log message
            level: Log level (INFO, WARNING, ERROR)
        """
        if self.progress_callback:
            await self.progress_callback(stage, message, level)
        else:
            print(message)

    def run_stage_1_segmentation(self) -> Dict[str, Any]:
        """
        Stage 1: Segment CV into structured sections and entries.

        Returns:
            Segmented CV data
        """
        print("="*80)
        print("STAGE 1: CV SEGMENTATION")
        print("="*80)
        print(f"Input: {self.cv_path}")
        print(f"Output: {self.stage_dirs['stage_1']}")
        print()

        # Run segmentation using CVSegmenter
        segmenter = CVSegmenter()
        result = segmenter.segment(
            file_path=str(self.cv_path),
            output_dir=str(self.stage_dirs["stage_1"])
        )

        segmented_path = result['output_file']

        # Load segmented data
        with open(segmented_path, 'r') as f:
            segmented_data = json.load(f)

        self.results["stages"]["stage_1_segmentation"] = {
            "output_file": str(segmented_path),
            "num_groups": segmented_data.get("meta", {}).get("num_top_level_groups", 0),
            "total_entries": segmented_data.get("meta", {}).get("total_entries", 0)
        }

        print(f"✓ Segmentation complete: {segmented_path}")
        print()

        return segmented_data

    def run_stage_3_taxonomy_mapping(self, segmented_path: str) -> Dict[str, Any]:
        """
        Stage 3: Map sections to WCM taxonomy.

        NOTE: This was previously "Stage 2" but is now Stage 3 after adding
        Stage 2a (entry delimitation) and Stage 2b (entry extraction).

        Args:
            segmented_path: Path to segmented CV JSON

        Returns:
            Mapped sections data
        """
        print("="*80)
        print("STAGE 3: TAXONOMY MAPPING")
        print("="*80)
        print(f"Output: {self.stage_dirs['stage_3']}")
        print()

        # Use OutputManager for consistent output paths
        from .output_manager import OutputManager
        om = OutputManager(segmented_path)
        output_path = om.get_stage3_path()

        # Run taxonomy mapping
        result = map_cv_sections(
            segmented_cv_path=segmented_path,
            output_path=str(output_path)
        )

        self.results["stages"]["stage_3_taxonomy_mapping"] = {
            "output_file": result["output_file"],
            "total_sections": result["stats"]["total_sections"],
            "high_confidence": result["stats"]["high_confidence"],
            "avg_confidence": result["stats"]["avg_confidence"]
        }

        print(f"✓ Taxonomy mapping complete: {result['output_file']}")
        print()

        # Load mapped data
        with open(result["output_file"], 'r') as f:
            mapped_data = json.load(f)

        return mapped_data

    # Backward compatibility alias
    def run_stage_2_taxonomy_mapping(self, segmented_path: str) -> Dict[str, Any]:
        """Deprecated: Use run_stage_3_taxonomy_mapping instead."""
        import warnings
        warnings.warn("run_stage_2_taxonomy_mapping is deprecated, use run_stage_3_taxonomy_mapping", DeprecationWarning)
        return self.run_stage_3_taxonomy_mapping(segmented_path)

    def run_stage_3_section_parsing(self, segmented_path: str, mapped_data: Dict[str, Any], taxonomy_path: str = None) -> Dict[str, Any]:
        """
        Stage 3: Parse ALL WCM sections using 71 specialized extractors.

        Uses the integrated section_extraction_orchestrator which:
        - Creates classified format from segmented + taxonomy data
        - Runs all 71 WCM section extractors
        - Returns structured data for ALL sections (not just 4 entity types)

        Args:
            segmented_path: Path to segmented CV JSON
            mapped_data: Mapped sections data (from Stage 2)
            taxonomy_path: Path to taxonomy mapping JSON (optional)

        Returns:
            Dictionary with all parsed section data organized by section ID
        """
        print("="*80)
        print("STAGE 3: FULL WCM SECTION EXTRACTION")
        print("="*80)
        print("Using 71 specialized extractors organized by WCM section ID")
        print()

        # Import the section extraction orchestrator
        from .section_extraction_orchestrator import SectionExtractionOrchestrator

        # Initialize orchestrator
        orchestrator = SectionExtractionOrchestrator(self.output_dir)

        # Create classified format from segmented + mapped data
        segmented_file = Path(segmented_path)

        # Save mapped data to temp file for orchestrator
        mapped_file = self.output_dir / "stage_3_taxonomy_mapping" / f"{segmented_file.stem.replace('_segmented', '')}_mapped.json"
        if not mapped_file.exists():
            # mapped_data is already loaded, save it
            mapped_file.parent.mkdir(exist_ok=True, parents=True)
            with open(mapped_file, 'w') as f:
                json.dump(mapped_data, f, indent=2)

        print("Step 1: Creating classified format...")
        classified_file = orchestrator.create_classified_format(
            segmented_file=segmented_file,
            mapped_file=mapped_file,
            verbose=True
        )

        print()
        print("Step 2: Running WCM section extractors...")
        extracted_files = orchestrator.run_section_extractors(
            classified_file=classified_file,
            sections_to_extract=None,  # Extract all sections
            verbose=True
        )

        # Load all extracted data from extractor outputs
        print()
        print("Step 3: Organizing extracted data...")

        # Map WCM section IDs to legacy entity type names for backward compatibility
        # Includes both letter codes (B1, D1), canonical names (doctoral_degree), and numeric taxonomy IDs (1, 2, 3...)
        SECTION_TO_ENTITY_MAP = {
            # Numeric taxonomy IDs (from WCM taxonomy wcm_section_number)
            '1': 'personal_data',  # Contact Information
            '2': 'education',      # Education and Training
            '3': 'education',      # Postdoctoral Training
            '4': 'positions',      # Professional Positions
            '5': 'positions',      # Employment Status
            '6': 'certifications', # Licensure & Certification
            '7': 'positions',      # Institutional Affiliation
            '8': 'honors',         # Honors & Awards
            '9': 'memberships',    # Professional Organizations
            '10': 'positions',     # Percent Effort
            '11': 'teaching',      # Educational Contributions
            '12': 'clinical',      # Clinical Practice
            '13': 'grants',        # Research/Grants
            '14': 'mentoring',     # Mentoring
            '15': 'service',       # Institutional Leadership
            '16': 'service',       # Institutional Administration
            '17': 'service',       # Extramural Professional Activities
            '18': 'presentations', # Invitations to Speak
            '19': 'publications',  # Bibliography

            # Letter codes
            'B1': 'education',  # Academic Degree
            'B2': 'education',  # Other Educational Experiences
            'B2': 'education',
            'C': 'licensure',   # Medical Licensure
            'C1': 'licensure',
            'D1': 'positions',  # Academic Appointments
            'D2': 'positions',  # Hospital Appointments
            'D3': 'positions',  # Other Professional Positions
        }

        # Group extracted data by entity type for backward compatibility
        parsed_data = {
            "publications": [],
            "education": [],
            "positions": [],
            "grants": [],
            "certifications": [],
            "honors": [],
            "memberships": [],
            "service": [],
            "licensure": [],
            "mentoring": []
        }

        total_entries_extracted = 0

        # Load WCM extractor outputs (structured data from extractors)
        for section_id, extracted_file in extracted_files.items():
            # Load extracted data
            with open(extracted_file) as f:
                section_data = json.load(f)

            # Try both keys: 'entries' (from WCM extractors) and 'parsed_entries' (from legacy)
            entries = section_data.get('entries', section_data.get('parsed_entries', []))
            if not entries:
                continue

            total_entries_extracted += len(entries)

            # Map to entity type if known
            entity_type = SECTION_TO_ENTITY_MAP.get(section_id)
            if entity_type:
                # Convert to unified format
                for entry in entries:
                    parsed_data[entity_type].append(entry.get('structured_data', entry))
                print(f"  [Section {section_id}] → {entity_type}: {len(entries)} entries")

        # ALSO load segmented entries grouped by taxonomy section
        # This gives us the raw text_snippet data for LLM parsing
        print()
        print("Loading segmented data grouped by taxonomy...")
        segmented_by_section = self._group_segmented_by_taxonomy(segmented_path, taxonomy_path or mapped_file)
        for section_id, section_info in segmented_by_section.items():
            entries = section_info.get('entries', [])

            if not entries:
                continue

            total_entries_extracted += len(entries)

            # Map to entity type if known
            entity_type = SECTION_TO_ENTITY_MAP.get(section_id)
            if entity_type:
                # These entries should already have text_snippet fields
                parsed_data[entity_type].extend(entries)
                print(f"  [Section {section_id}] → {entity_type}: {len(entries)} entries (from segmentation)")

        print()
        print(f"Total entries extracted: {total_entries_extracted}")
        for entity_type, data in parsed_data.items():
            print(f"  {entity_type}: {len(data)} entries")

        # ============================================================================
        # STEP 3: Parse extracted data with LLM parsers
        # ============================================================================
        print()
        print("=" * 80)
        print("Step 3: Parsing extracted data with LLM parsers...")
        print("=" * 80)

        # Prepare entries for parsing (convert from legacy format to parser format)
        def prepare_entries_for_parsing(entries):
            """Convert legacy extracted entries to format expected by parsers."""

            # Get extracted CV owner name for filtering
            cv_owner_name = self.personal_info.get('full_name', '') if self.personal_info else ''

            def is_cv_owner_name(text: str) -> bool:
                """Check if entry is the CV owner's name (should be filtered out)."""
                if not cv_owner_name or not text:
                    return False

                text_clean = text.strip()

                # Exact match
                if text_clean == cv_owner_name:
                    return True

                # Fuzzy match: remove credentials and compare
                # "Carlos Domínguez, PhD" matches "Carlos Domínguez"
                text_no_creds = text_clean.split(',')[0].strip() if ',' in text_clean else text_clean
                name_no_creds = cv_owner_name.split(',')[0].strip() if ',' in cv_owner_name else cv_owner_name

                if text_no_creds == name_no_creds:
                    return True

                # Also remove titles like "Dr." for comparison
                text_no_title = text_no_creds.replace('Dr. ', '').replace('Prof. ', '').strip()
                name_no_title = name_no_creds.replace('Dr. ', '').replace('Prof. ', '').strip()

                return text_no_title == name_no_title

            prepared = []
            for idx, entry in enumerate(entries):
                # Check if entry is already structured (from extractors)
                # Structured entries have fields like 'Degree', 'Title', etc. but no text_snippet
                if isinstance(entry, dict):
                    # Has structured fields but no text_snippet -> already parsed by extractor
                    if 'text' not in entry and 'text_snippet' not in entry and len(entry) > 2:
                        # Entry is already structured, keep as-is
                        prepared.append(entry)
                        continue

                # Legacy extractors produce: {"text": "...", "parsed": false, "notes": "..."}
                # Segmented entries produce: {"text_snippet": "...", "entry_type": "..."}
                # Parsers expect: {"text_snippet": "...", "id": "..."}
                text = entry.get('text', entry.get('text_snippet', ''))
                if not text:
                    continue

                # Skip entries that match the CV owner's name
                if is_cv_owner_name(text):
                    print(f"  ⊘ Skipping CV owner name: {text[:60]}...")
                    continue

                prepared.append({
                    "text_snippet": text,
                    "id": f"entry_{idx}",
                    "original_entry": entry
                })
            return prepared

        # Parse education
        if parsed_data["education"]:
            print(f"\nParsing education entries ({len(parsed_data['education'])} entries)...")
            try:
                education_entries = prepare_entries_for_parsing(parsed_data["education"])
                # Filter for entries that need parsing (have text_snippet)
                entries_to_parse = [e for e in education_entries if 'text_snippet' in e]
                # Keep already-structured entries
                structured_entries = [e for e in education_entries if 'text_snippet' not in e]

                if entries_to_parse:
                    result = parse_education_section(entries_to_parse)
                    education_list = result.get("education", result) if isinstance(result, dict) else result
                    parsed_data["education"] = structured_entries + education_list
                    print(f"  ✓ Parsed {len(education_list)} education entries ({len(structured_entries)} already structured)")
                else:
                    # All entries are already structured, normalize field names
                    normalized_education = []
                    for entry in structured_entries:
                        normalized = {
                            "degree": entry.get("Degree", ""),
                            "major_field": entry.get("Field of Study", entry.get("major_field", "")),
                            "institution": entry.get("Institution", entry.get("institution", "")),
                            "start_year": entry.get("start_year", ""),
                            "end_year": entry.get("Year Awarded", entry.get("end_year", "")),
                            "location": entry.get("location", ""),
                            "confidence": 0.9  # High confidence for extractor data
                        }
                        normalized_education.append(normalized)
                    parsed_data["education"] = normalized_education
                    print(f"  ✓ Normalized {len(normalized_education)} pre-structured education entries")
            except Exception as e:
                print(f"  ✗ Error parsing education: {e}")

        # Parse positions
        if parsed_data["positions"]:
            print(f"\nParsing positions entries ({len(parsed_data['positions'])} entries)...")
            try:
                position_entries = prepare_entries_for_parsing(parsed_data["positions"])
                result = parse_positions_section(position_entries)
                parsed_data["positions"] = result.get("positions", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['positions'])} position entries")
            except Exception as e:
                print(f"  ✗ Error parsing positions: {e}")

        # Parse grants
        if parsed_data["grants"]:
            print(f"\nParsing grants entries ({len(parsed_data['grants'])} entries)...")
            try:
                grant_entries = prepare_entries_for_parsing(parsed_data["grants"])
                result = parse_grants_section(grant_entries)
                parsed_data["grants"] = result.get("grants", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['grants'])} grant entries")
            except Exception as e:
                print(f"  ✗ Error parsing grants: {e}")

        # Parse publications (more complex due to subsections)
        if parsed_data["publications"]:
            print(f"\nParsing publication entries ({len(parsed_data['publications'])} entries)...")
            try:
                publication_entries = prepare_entries_for_parsing(parsed_data["publications"])

                # Extract target author from document UID
                cv_owner_name = None
                if mapped_data:
                    cv_owner_name = extract_target_author_from_uid(
                        Path(segmented_path).stem.replace("_segmented", "")
                    )

                result = parse_publications_section(
                    publication_entries,
                    target_author=cv_owner_name
                )
                parsed_data["publications"] = result.get("publications", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['publications'])} publication entries")
            except Exception as e:
                print(f"  ✗ Error parsing publications: {e}")

        # Parse certifications
        if parsed_data["certifications"]:
            print(f"\nParsing certification entries ({len(parsed_data['certifications'])} entries)...")
            try:
                cert_entries = prepare_entries_for_parsing(parsed_data["certifications"])
                result = parse_certifications_section(cert_entries)
                parsed_data["certifications"] = result.get("certifications", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['certifications'])} certification entries")
            except Exception as e:
                print(f"  ✗ Error parsing certifications: {e}")

        # Parse honors
        if parsed_data["honors"]:
            print(f"\nParsing honors/awards entries ({len(parsed_data['honors'])} entries)...")
            try:
                honor_entries = prepare_entries_for_parsing(parsed_data["honors"])
                result = parse_honors_section(honor_entries)
                parsed_data["honors"] = result.get("honors", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['honors'])} honors/awards entries")
            except Exception as e:
                print(f"  ✗ Error parsing honors: {e}")

        # Parse memberships
        if parsed_data["memberships"]:
            print(f"\nParsing membership entries ({len(parsed_data['memberships'])} entries)...")
            try:
                membership_entries = prepare_entries_for_parsing(parsed_data["memberships"])
                result = parse_memberships_section(membership_entries)
                parsed_data["memberships"] = result.get("memberships", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['memberships'])} membership entries")
            except Exception as e:
                print(f"  ✗ Error parsing memberships: {e}")

        # Parse service
        if parsed_data["service"]:
            print(f"\nParsing service entries ({len(parsed_data['service'])} entries)...")
            try:
                service_entries = prepare_entries_for_parsing(parsed_data["service"])
                result = parse_service_section(service_entries)
                parsed_data["service"] = result.get("service", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['service'])} service entries")
            except Exception as e:
                print(f"  ✗ Error parsing service: {e}")

        # Parse licensure
        if parsed_data["licensure"]:
            print(f"\nParsing licensure entries ({len(parsed_data['licensure'])} entries)...")
            try:
                licensure_entries = prepare_entries_for_parsing(parsed_data["licensure"])
                result = parse_licensure_section(licensure_entries)
                parsed_data["licensure"] = result.get("licensure", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['licensure'])} licensure entries")
            except Exception as e:
                print(f"  ✗ Error parsing licensure: {e}")

        # Parse mentoring
        if parsed_data["mentoring"]:
            print(f"\nParsing mentoring entries ({len(parsed_data['mentoring'])} entries)...")
            try:
                mentoring_entries = prepare_entries_for_parsing(parsed_data["mentoring"])
                result = parse_mentoring_section(mentoring_entries)
                parsed_data["mentoring"] = result.get("mentoring", result) if isinstance(result, dict) else result
                print(f"  ✓ Parsed {len(parsed_data['mentoring'])} mentoring entries")
            except Exception as e:
                print(f"  ✗ Error parsing mentoring: {e}")

        print()
        print("=" * 80)
        print("LLM Parsing Complete")
        print("=" * 80)
        for entity_type, data in parsed_data.items():
            high_conf = sum(1 for item in data if item.get("confidence", 0) >= 0.8)
            print(f"  {entity_type}: {len(data)} entries ({high_conf} high confidence)")
        print()

        # Save entity-specific files with section codes
        base_name = Path(segmented_path).stem.replace("_segmented", "")

        for section_type, data in parsed_data.items():
            # Get section code for this entity type
            section_code = self.entity_to_section_code.get(section_type, section_type.upper())

            # Get section-specific output directory (now using section codes)
            section_dir_key = f"stage_3_{section_type}"
            # Use section code in filename: S_publications_CV_2050_parsed.json
            output_path = self.stage_dirs[section_dir_key] / f"{section_code}_{section_type}_{base_name}_parsed.json"

            # Calculate stats
            high_conf = sum(1 for item in data if item.get("confidence", 0) >= 0.8)

            output_data = {
                "source_file": segmented_path,
                "section_code": section_code,  # Add section code to output
                f"total_{section_type}": len(data),
                "high_confidence": high_conf,
                section_type: data
            }

            with open(output_path, 'w') as f:
                json.dump(output_data, f, indent=2)

            self.results["stages"][f"stage_3_{section_type}_parsing"] = {
                "section_code": section_code,
                "output_file": str(output_path),
                "total_items": len(data),
                "high_confidence": high_conf
            }

        # Generate individual section files based on taxonomy mappings (if taxonomy_path provided)
        if taxonomy_path:
            print()
            print("Generating individual section files based on taxonomy...")
            section_files = self._generate_section_specific_files(
                segmented_path=segmented_path,
                taxonomy_path=taxonomy_path,
                parsed_data=parsed_data,
                base_name=base_name
            )
            print(f"  ✓ Generated {len(section_files)} section-specific files")
            print()

        return parsed_data

    def _group_segmented_by_taxonomy(
        self,
        segmented_path: str,
        taxonomy_path: str
    ) -> Dict[str, Dict]:
        """
        Group segmented CV entries by taxonomy section.

        Returns:
            Dictionary mapping section_id to {"entries": [...], "section_name": "..."}
        """
        from collections import defaultdict

        # Load taxonomy mappings
        with open(taxonomy_path, 'r') as f:
            taxonomy_data = json.load(f)

        # Load segmented CV
        with open(segmented_path, 'r') as f:
            segmented_cv = json.load(f)

        # Build group_id -> taxonomy mapping
        group_to_taxonomy = {}
        for mapping in taxonomy_data.get("mappings", []):
            group_id = mapping.get("source_group_id")
            if group_id:
                group_to_taxonomy[group_id] = {
                    "section_id": mapping.get("mapped_section_id"),
                    "canonical_name": mapping.get("mapped_canonical_name"),
                    "confidence": mapping.get("confidence"),
                    "source_label": mapping.get("source_label")
                }

        # Build group_id -> entries mapping
        def build_group_map(group, group_map):
            """Recursively build map of group_id -> entries."""
            group_id = group.get("id")
            if group_id:
                group_map[group_id] = {
                    "entries": group.get("entries", []),
                    "label": group.get("label_inferred", ""),
                    "subgroups": group.get("subgroups", [])
                }
            for subgroup in group.get("subgroups", []):
                build_group_map(subgroup, group_map)

        group_map = {}
        for group in segmented_cv.get("groups", []):
            build_group_map(group, group_map)

        # Group data by taxonomy section_id
        section_data = defaultdict(lambda: {
            "section_id": "",
            "section_name": "",
            "entries": []
        })

        # Helper to recursively collect all entries from a group and its subgroups
        def collect_all_entries(group_id, group_map):
            """Recursively collect entries from a group and all its subgroups."""
            all_entries = []
            if group_id in group_map:
                group_info = group_map[group_id]
                # Add this group's entries
                all_entries.extend(group_info.get("entries", []))
                # Recursively collect from subgroups
                for subgroup in group_info.get("subgroups", []):
                    subgroup_id = subgroup.get("id")
                    if subgroup_id:
                        all_entries.extend(collect_all_entries(subgroup_id, group_map))
            return all_entries

        # Process each taxonomy mapping
        for group_id, taxonomy_info in group_to_taxonomy.items():
            section_id = taxonomy_info["section_id"]
            section_name = taxonomy_info["canonical_name"]

            if group_id in group_map:
                # Recursively collect all entries from this group and subgroups
                entries = collect_all_entries(group_id, group_map)

                if entries:
                    section_data[section_id]["section_id"] = section_id
                    section_data[section_id]["section_name"] = section_name
                    section_data[section_id]["entries"].extend(entries)

        return dict(section_data)

    def _generate_section_specific_files(
        self,
        segmented_path: str,
        taxonomy_path: str,
        parsed_data: Dict[str, List],
        base_name: str
    ) -> List[str]:
        """
        Generate individual files for each WCM taxonomy section.

        This creates files like:
        - section_contact_information_{cv_id}_parsed.json
        - section_education_{cv_id}_parsed.json
        - section_bibliography_{cv_id}_parsed.json
        etc.

        Args:
            segmented_path: Path to segmented CV
            taxonomy_path: Path to taxonomy mapping
            parsed_data: Parsed data from Stage 3 (publications, education, etc.)
            base_name: Base filename for output files

        Returns:
            List of output file paths created
        """
        from collections import defaultdict

        # Load taxonomy mappings
        with open(taxonomy_path, 'r') as f:
            taxonomy_data = json.load(f)

        # Load segmented CV
        with open(segmented_path, 'r') as f:
            segmented_cv = json.load(f)

        # Build group_id -> taxonomy mapping
        group_to_taxonomy = {}
        for mapping in taxonomy_data.get("mappings", []):
            group_id = mapping.get("source_group_id")
            if group_id:
                group_to_taxonomy[group_id] = {
                    "section_id": mapping.get("mapped_section_id"),
                    "canonical_name": mapping.get("mapped_canonical_name"),
                    "confidence": mapping.get("confidence"),
                    "source_label": mapping.get("source_label")
                }

        # Build group_id -> entries mapping
        def build_group_map(group, group_map):
            """Recursively build map of group_id -> entries."""
            group_id = group.get("id")
            if group_id:
                group_map[group_id] = {
                    "entries": group.get("entries", []),
                    "label": group.get("label_inferred", ""),
                    "subgroups": group.get("subgroups", [])
                }
            for subgroup in group.get("subgroups", []):
                build_group_map(subgroup, group_map)

        group_map = {}
        for group in segmented_cv.get("groups", []):
            build_group_map(group, group_map)

        # Group data by taxonomy section_id
        section_data = defaultdict(lambda: {
            "section_id": "",
            "section_name": "",
            "entries": [],
            "source_groups": [],
            "avg_confidence": 0.0
        })

        # Build a lookup map: entry_id -> parsed structured data
        # AND text -> parsed data (for fallback matching)
        # parsed_data is organized by entity type (service, education, etc.)
        entry_id_to_parsed = {}
        text_to_parsed = {}
        for entity_type, parsed_entries in parsed_data.items():
            for parsed_entry in parsed_entries:
                # Each parsed entry should have an 'id' or we can match by text
                entry_id = parsed_entry.get('id') or parsed_entry.get('entry_id')
                if entry_id:
                    entry_id_to_parsed[entry_id] = parsed_entry

                # Also index by original_text for fallback matching
                original_text = parsed_entry.get('original_text', '').strip()
                if original_text:
                    text_to_parsed[original_text] = parsed_entry

        # Process each taxonomy mapping
        for group_id, taxonomy_info in group_to_taxonomy.items():
            section_id = taxonomy_info["section_id"]
            section_name = taxonomy_info["canonical_name"]

            if group_id in group_map:
                group_info = group_map[group_id]

                # Get entries for this group (raw text from segmentation)
                raw_entries = group_info["entries"]

                if raw_entries:
                    # CRITICAL: Merge LLM-parsed structured data with raw entries
                    enhanced_entries = []
                    for raw_entry in raw_entries:
                        entry_id = raw_entry.get("id")
                        text_snippet = raw_entry.get("text_snippet", "").strip()

                        parsed_entry = None

                        # Try matching by ID first
                        if entry_id and entry_id in entry_id_to_parsed:
                            parsed_entry = entry_id_to_parsed[entry_id]

                        # Fallback: Match by text
                        elif text_snippet and text_snippet in text_to_parsed:
                            parsed_entry = text_to_parsed[text_snippet]

                        # If we found parsed data, merge it
                        if parsed_entry:
                            # Merge: keep raw entry structure but add parsed structured data
                            enhanced_entry = {
                                **raw_entry,
                                "structured_data": parsed_entry,  # Add LLM-parsed fields
                                "has_structured_data": True
                            }
                            enhanced_entries.append(enhanced_entry)
                        else:
                            # No parsed data available, keep raw entry
                            enhanced_entries.append({
                                **raw_entry,
                                "has_structured_data": False
                            })

                    section_data[section_id]["section_id"] = section_id
                    section_data[section_id]["section_name"] = section_name
                    section_data[section_id]["entries"].extend(enhanced_entries)
                    section_data[section_id]["source_groups"].append({
                        "group_id": group_id,
                        "label": taxonomy_info["source_label"],
                        "confidence": taxonomy_info["confidence"],
                        "num_entries": len(enhanced_entries)
                    })

        # Calculate average confidence for each section
        for section_id, data in section_data.items():
            if data["source_groups"]:
                total_conf = sum(g["confidence"] for g in data["source_groups"])
                data["avg_confidence"] = total_conf / len(data["source_groups"])

        # Create output directory for section-specific files
        section_output_dir = self.output_dir / "stage_3_sections"
        section_output_dir.mkdir(exist_ok=True)

        # Save individual section files
        output_files = []
        for section_id, data in section_data.items():
            if not data["entries"]:
                continue

            # Get section code from taxonomy (primary identifier)
            section_code = section_id_to_code(section_id)
            if not section_code:
                # Fallback for unmapped sections (shouldn't happen with complete taxonomy)
                section_code = section_id.replace("/", "_").replace(" ", "_").upper()
                print(f"  WARNING: No section code found for '{section_id}', using fallback: {section_code}")

            # Use section code in filename: P_institutional_admin_CV_2050_parsed.json
            # Keep section_id in filename for readability but code comes first
            section_name_slug = section_id.replace("/", "_").replace(" ", "_")[:30]  # Truncate for short names
            output_path = section_output_dir / f"{section_code}_{section_name_slug}_{base_name}_parsed.json"

            output_data = {
                "document_uid": base_name,
                "section_code": section_code,  # PRIMARY identifier
                "section_id": section_id,  # Legacy compatibility
                "section_name": data["section_name"],
                "num_entries": len(data["entries"]),
                "num_source_groups": len(data["source_groups"]),
                "avg_taxonomy_confidence": round(data["avg_confidence"], 3),
                "source_groups": data["source_groups"],
                "entries": data["entries"]
            }

            with open(output_path, 'w') as f:
                json.dump(output_data, f, indent=2)

            output_files.append(str(output_path))
            print(f"  • {section_code} ({section_id}): {len(data['entries'])} entries → {output_path.name}")

        # Store in results for orchestrator
        self.results["stages"]["stage_3_sections"] = {
            "output_files": output_files,
            "num_sections": len(section_data),
            "output_dir": str(section_output_dir)
        }

        return output_files

    def _abbreviate_author_name(self, full_name: str) -> str:
        """
        Abbreviate author name to WCM format: LastName Initials

        Handles:
        - "FirstName LastName" → "LastName F"
        - "FirstName MiddleName LastName" → "LastName FM"
        - "LastName, F." → "LastName F"
        - Already abbreviated → unchanged

        Args:
            full_name: Full or partial author name

        Returns:
            Abbreviated name in WCM format
        """
        import re

        name = full_name.strip()

        # Handle special cases
        if name.lower() in ['et al', 'et al.', 'et al,']:
            return 'et al'

        # Remove periods
        name = name.replace('.', '')

        # Split by comma (handles "LastName, FirstName" format)
        if ',' in name:
            parts = name.split(',', 1)
            last_name = parts[0].strip()
            first_part = parts[1].strip()
            # Get initials from first part
            initials = ''.join([word[0].upper() for word in first_part.split() if word])
            return f"{last_name} {initials}" if initials else last_name

        # Split by whitespace
        words = name.split()

        if len(words) == 1:
            return name  # Just a last name
        elif len(words) == 2:
            # Could be "FirstName LastName" or "F LastName" or "LastName F"
            # Check if first word is an initial (1-2 letters)
            if len(words[0]) <= 2 and words[0][0].isupper():
                # "F LastName" → "LastName F"
                return f"{words[1]} {words[0]}"
            elif len(words[1]) <= 2 and words[1][0].isupper():
                # "LastName F" → "LastName F"
                return name
            else:
                # "FirstName LastName" → "LastName F"
                return f"{words[1]} {words[0][0]}"
        else:
            # Multiple words: assume "FirstName [MiddleNames...] LastName"
            last_name = words[-1]
            initials = ''.join([w[0] for w in words[:-1]])
            return f"{last_name} {initials}"

    def _convert_to_legacy_format(self, parsed_data: Dict[str, List]) -> Dict[str, Any]:
        """
        Convert unified pipeline parsed data to legacy format for advanced handlers.

        Args:
            parsed_data: Dict with keys: publications, education, positions, grants

        Returns:
            Legacy-formatted section data structure
        """
        legacy_sections = {}

        # Convert publications to Section S format with subsections
        if parsed_data.get("publications"):
            # Categorize publications by type into subsections
            subsections = {
                'S1': {'entries': [], 'label': 'Peer-Reviewed Research Articles'},
                'S2': {'entries': [], 'label': 'Reviews and Editorials'},
                'S4': {'entries': [], 'label': 'Chapters'},
                'S5': {'entries': [], 'label': 'Books'},
                'S6': {'entries': [], 'label': 'Case Reports'},
                'S7': {'entries': [], 'label': 'In review'},
                'S8': {'entries': [], 'label': 'Abstracts'},
            }

            for pub in parsed_data["publications"]:
                pub_type = pub.get('publication_type', 'journal_article').lower()
                title = pub.get('title', '').lower()
                journal = pub.get('journal', '').lower()

                # PRIORITY 1: Use PubMed subsection if available (most authoritative)
                # This comes from PubMed enrichment which fetches actual publication types
                if pub.get('wcm_subsection_from_pubmed'):
                    subsection_id = pub['wcm_subsection_from_pubmed']
                    confidence = pub.get('pubmed_classification_confidence', 0.0)
                    conf_display = f" (conf: {confidence:.2f})" if confidence > 0 else ""
                    conf_emoji = "🔵" if confidence >= 0.9 else "🟢" if confidence >= 0.7 else "🟡" if confidence > 0 else "✅"
                    print(f"  {conf_emoji} Using PubMed type: {title[:50]}... → {subsection_id}{conf_display}")

                else:
                    # PRIORITY 2: Check for preprints via DOI or manuscript status terms
                    doi = pub.get('doi', '')

                    # Preprint DOI patterns
                    is_preprint = (doi.startswith('10.1101/') or  # bioRxiv/medRxiv
                                  'arXiv:' in doi or
                                  'OSF Preprints' in str(pub.get('journal', '')))

                    # Manuscript status terms in title or notes
                    manuscript_status_terms = ['in preparation', 'submitted', 'under review',
                                              'revise and resubmit', 'in press']
                    has_status_term = any(term in title for term in manuscript_status_terms)

                    if is_preprint or has_status_term:
                        subsection_id = 'S7'  # In review / Manuscripts (Submitted/In Press)
                        print(f"  ℹ️  Detected preprint/manuscript: {title[:50]}... → S7")

                    else:
                        # PRIORITY 3: Enhanced keyword detection for publication types
                        # Check title and journal for review indicators
                        review_keywords = ['review', 'meta-analysis', 'systematic review', 'meta analysis',
                                           'scoping review', 'literature review', 'umbrella review']
                        editorial_keywords = ['editorial', 'commentary', 'letter to editor', 'opinion',
                                              'perspective', 'letter to the editor']

                        is_review = any(keyword in title for keyword in review_keywords) or \
                                   any(keyword in journal for keyword in ['review', 'reviews'])
                        is_editorial = any(keyword in title for keyword in editorial_keywords)

                        # Override LLM classification if keywords strongly indicate a specific type
                        if is_review and pub_type not in ['review', 'editorial']:
                            print(f"  ℹ️  Reclassifying as review (keyword match): {title[:50]}...")
                            pub_type = 'review'
                        elif is_editorial and pub_type not in ['review', 'editorial']:
                            print(f"  ℹ️  Reclassifying as editorial (keyword match): {title[:50]}...")
                            pub_type = 'editorial'

                        # PRIORITY 4: Map LLM-extracted publication types to subsections
                        if pub_type in ['journal_article', 'article', 'research_article']:
                            subsection_id = 'S1'
                        elif pub_type in ['review', 'editorial']:
                            subsection_id = 'S2'
                        elif pub_type in ['chapter', 'book_chapter']:
                            subsection_id = 'S4'
                        elif pub_type in ['book']:
                            subsection_id = 'S5'
                        elif pub_type in ['case_report']:
                            subsection_id = 'S6'
                        elif pub_type in ['preprint', 'in_press', 'submitted']:
                            subsection_id = 'S7'
                        elif pub_type in ['abstract', 'conference', 'conference_paper']:
                            subsection_id = 'S8'
                        else:
                            # No default - let LLM classification stand or return None for manual review
                            subsection_id = 'S1'  # Fallback to peer-reviewed (but log it)

                # Convert authors list to comma-separated string for legacy handler
                # AND abbreviate author names to WCM format
                authors = pub.get('authors', [])
                if isinstance(authors, list):
                    abbreviated_authors = [self._abbreviate_author_name(auth) for auth in authors]
                    authors_str = ', '.join(abbreviated_authors)
                else:
                    authors_str = self._abbreviate_author_name(str(authors))

                # Convert to legacy entry format
                legacy_entry = {
                    'structured_data': {
                        'title': pub.get('title', ''),
                        'authors': authors_str,  # String, not list
                        'journal': pub.get('journal', ''),
                        'year': pub.get('year', ''),
                        'volume': pub.get('volume', ''),
                        'issue': pub.get('issue', ''),
                        'pages': pub.get('pages', ''),
                        'doi': pub.get('doi', ''),
                        'pmid': pub.get('pmid', ''),
                        'pmcid': pub.get('pmcid', ''),
                    },
                    'original_text': pub.get('original_text', ''),
                    'confidence': pub.get('confidence', 0.0)
                }

                subsections[subsection_id]['entries'].append(legacy_entry)

            # Remove empty subsections
            subsections = {k: v for k, v in subsections.items() if v['entries']}

            legacy_sections['bibliography'] = {
                'subsections': subsections,
                'section_id': 'S'
            }

        # Convert education to legacy format (Section B)
        if parsed_data.get("education"):
            education_entries = []
            for edu in parsed_data["education"]:
                # VALIDATION: Only include entries that have a degree field
                # This filters out misclassified professional positions
                if not edu.get('degree', '').strip():
                    print(f"  ⚠️  Skipping education entry without degree: {edu.get('institution', 'Unknown')}")
                    continue

                # Parse location into City, State/Province, Country
                location = edu.get('location', '').strip()
                city = ''
                state = ''
                country = ''

                if location:
                    # Split location by comma
                    parts = [p.strip() for p in location.split(',')]

                    if len(parts) == 1:
                        # Just city (e.g., "Boston")
                        city = parts[0]
                    elif len(parts) == 2:
                        # City, State or City, Country (e.g., "Boston, MA" or "London, UK")
                        city = parts[0]
                        # Check if second part is a US state (2 letters) or country
                        if len(parts[1]) == 2:
                            state = parts[1]  # Likely a US state
                        else:
                            country = parts[1]
                    elif len(parts) >= 3:
                        # City, State, Country (e.g., "Boston, MA, USA")
                        city = parts[0]
                        state = parts[1]
                        country = parts[2]

                # Convert to legacy entry format with WCM field names
                legacy_entry = {
                    'Degree': edu.get('degree', ''),
                    'Major/Field': edu.get('major_field', ''),
                    'Institution': edu.get('institution', ''),
                    'City': city,
                    'State/Province': state,
                    'Country': country,
                    'Dates': f"{edu.get('start_year', '')}-{edu.get('end_year', '')}" if edu.get('start_year') else '',
                    'Year Awarded': str(edu.get('end_year', '')) if edu.get('end_year') else ''
                }
                education_entries.append(legacy_entry)

            legacy_sections['education'] = {
                'entries': education_entries,
                'section_id': 'B'
            }

        # Convert positions to legacy format (Section D/E)
        if parsed_data.get("positions"):
            position_entries = []
            for pos in parsed_data["positions"]:
                # Determine if academic or other position
                title = pos.get('title', '').lower()
                is_academic = any(kw in title for kw in ['professor', 'instructor', 'lecturer', 'fellow', 'assistant', 'associate'])

                legacy_entry = {
                    'Title': pos.get('title', ''),
                    'Institution': pos.get('institution', ''),
                    'City': pos.get('location', '').split(',')[0].strip() if ',' in pos.get('location', '') else '',
                    'State/Province': '',
                    'Country': pos.get('location', '').split(',')[-1].strip() if ',' in pos.get('location', '') else pos.get('location', ''),
                    'Dates': f"{pos.get('start_year', '')}-{'Present' if pos.get('is_current') else pos.get('end_year', '')}" if pos.get('start_year') else '',
                    '_is_academic': is_academic
                }
                position_entries.append(legacy_entry)

            legacy_sections['positions'] = {
                'entries': position_entries,
                'section_id': 'D'
            }

        # Convert grants to legacy format (Section M)
        if parsed_data.get("grants"):
            grant_entries = []
            for grant in parsed_data["grants"]:
                legacy_entry = {
                    'Grant Title': grant.get('title', grant.get('grant_title', '')),
                    'Agency': grant.get('agency', grant.get('funding_agency', '')),
                    'Role': grant.get('role', grant.get('investigator_role', '')),
                    'Dates': f"{grant.get('start_year', '')}-{grant.get('end_year', '')}" if grant.get('start_year') else '',
                    'Amount': grant.get('amount', '')
                }
                grant_entries.append(legacy_entry)

            legacy_sections['grants'] = {
                'entries': grant_entries,
                'section_id': 'M'
            }

        return legacy_sections

    def run_stage_4_template_generation(self) -> Dict[str, Any]:
        """
        Stage 4: Generate WCM Template

        COMPLETE LEGACY INTEGRATION FLOW:
            1. Convert unified (Stages 1-2) → classified.json format
            2. Run 71 legacy specialized extractors (Stage 2C)
            3. Run legacy enrichment (Stage 2D) - PubMed + ROR
            4. Run legacy populate_cv.py (Stage 3) with ALL 7 special handlers
            5. Result: Perfect WCM CV with all optimizations

        This architecture preserves all pre-Oct-31 functionality while using
        modern segmentation and taxonomy mapping from unified pipeline.

        Returns:
            Stage results dictionary
        """
        print("="*80)
        print("STAGE 4: WCM TEMPLATE GENERATION (COMPLETE LEGACY INTEGRATION)")
        print("="*80)
        print(f"Output: {self.stage_dirs['stage_4']}")
        print()
        print("Using FULL legacy system:")
        print("  • 71 specialized extractors (Stage 2C)")
        print("  • PubMed + ROR enrichment (Stage 2D)")
        print("  • populate_cv.py with ALL 7 special handlers (Stage 3)")
        print()

        # Get the CV name from the path
        cv_name = self.cv_path.stem

        # Create output directories
        template_dir = self.stage_dirs["stage_4"]
        template_dir.mkdir(exist_ok=True)

        classified_dir = self.output_dir / "classified"
        classified_dir.mkdir(parents=True, exist_ok=True)

        extracted_dir = self.output_dir / "stage_2c_extracted"
        extracted_dir.mkdir(parents=True, exist_ok=True)

        enriched_dir = self.output_dir / "stage_2d_enriched"
        enriched_dir.mkdir(parents=True, exist_ok=True)

        # ========================================================================
        # STEP 1: Convert unified → classified format
        # ========================================================================
        print("STEP 1: Converting unified → classified format")
        print("="*80)

        from .unified_to_classified_converter import convert_unified_to_classified

        segmented_file = self.stage_dirs["stage_1"] / f"{cv_name}_segmented.json"
        mapped_file = self.stage_dirs["stage_3"] / f"{cv_name}_mapped.json"
        classified_file = classified_dir / f"{cv_name}_classified.json"

        convert_unified_to_classified(
            segmented_file=segmented_file,
            mapped_file=mapped_file,
            output_file=classified_file,
            verbose=True
        )

        # Extract CV ID
        with open(classified_file) as f:
            classified_data = json.load(f)
            cv_id = classified_data.get('document_uid', cv_name)
            if not cv_id.startswith('CV_'):
                cv_id = f"CV_{cv_id}"

        # ========================================================================
        # STEP 2: Run legacy extractors (71 specialized scripts)
        # ========================================================================
        print("STEP 2: Running legacy extractors")
        print("="*80)

        from .legacy_extractor_orchestrator import LegacyExtractorOrchestrator

        extractor = LegacyExtractorOrchestrator(
            classified_file=classified_file,
            output_dir=extracted_dir,
            verbose=True
        )

        extraction_result = extractor.run_all_extractors(max_workers=5)

        # ========================================================================
        # STEP 3: Run legacy enrichment (PubMed + ROR)
        # ========================================================================
        print("STEP 3: Running legacy enrichment")
        print("="*80)

        from .legacy_enrichment_orchestrator import LegacyEnrichmentOrchestrator

        enrichment_orch = LegacyEnrichmentOrchestrator(
            extracted_dir=extracted_dir,
            output_dir=enriched_dir,
            verbose=True
        )

        enrichment_result = enrichment_orch.run_all_enrichment(max_workers=5)

        # ========================================================================
        # STEP 3.5: Convert unified Stage 3 parsed data to legacy format
        # ========================================================================
        from datetime import datetime
        step_3_5_start = datetime.now()
        print("STEP 3.5: Converting unified parsed data to legacy format")
        print("="*80)
        print(f"[TRACE] Step 3.5 started at: {step_3_5_start.strftime('%H:%M:%S.%f')[:-3]}")

        from .legacy_format_adapter import UnifiedToLegacyAdapter

        adapter = UnifiedToLegacyAdapter()

        # FIRST: Convert section-specific files (stage_3_sections/)
        # These contain the taxonomy-mapped data organized by WCM section ID
        # This is the preferred source as it preserves the exact taxonomy mapping
        stage3_sections_dir = self.stage_dirs.get("stage_3_sections", self.output_dir / "stage_3_sections")
        section_files = []

        print(f"[TRACE] stage3_sections_dir: {stage3_sections_dir}")
        print(f"[TRACE] Directory exists: {stage3_sections_dir.exists()}")

        if stage3_sections_dir.exists():
            # Match both old (section_*) and new ({code}_*) naming patterns
            glob_pattern = f"*_{cv_name}_parsed.json"
            print(f"[TRACE] Glob pattern: {glob_pattern}")
            section_files = list(stage3_sections_dir.glob(glob_pattern))
            print(f"[TRACE] Found {len(section_files)} matching files:")
            for f in section_files:
                print(f"[TRACE]   - {f.name}")

            if section_files:
                print(f"Converting {len(section_files)} section files from unified pipeline...")
                print("  (Using taxonomy-mapped sections as primary data source)")

                # Log enriched_dir status BEFORE conversion
                print(f"[TRACE] enriched_dir BEFORE conversion: {enriched_dir}")
                print(f"[TRACE] enriched_dir exists: {enriched_dir.exists()}")
                if enriched_dir.exists():
                    existing_enriched = list(enriched_dir.glob("*.json"))
                    print(f"[TRACE] Existing enriched files BEFORE: {len(existing_enriched)}")
                    for f in existing_enriched:
                        print(f"[TRACE]   - {f.name}")

                # Run adapter conversion
                conversion_start = datetime.now()
                print(f"[TRACE] Starting adapter.convert_all_sections() at: {conversion_start.strftime('%H:%M:%S.%f')[:-3]}")

                created_files = adapter.convert_all_sections(
                    unified_sections_dir=stage3_sections_dir,
                    cv_id=cv_id,
                    output_dir=enriched_dir,
                    verbose=True
                )

                conversion_end = datetime.now()
                print(f"[TRACE] Adapter conversion completed at: {conversion_end.strftime('%H:%M:%S.%f')[:-3]}")
                print(f"[TRACE] Conversion took: {(conversion_end - conversion_start).total_seconds():.3f}s")
                print(f"[TRACE] Adapter returned {len(created_files) if created_files else 0} created files")

                # Log enriched_dir status AFTER conversion
                if enriched_dir.exists():
                    enriched_after = list(enriched_dir.glob("*.json"))
                    print(f"[TRACE] Enriched files AFTER conversion: {len(enriched_after)}")
                    for f in enriched_after:
                        print(f"[TRACE]   - {f.name}")
                else:
                    print(f"[TRACE] ERROR: enriched_dir does not exist after conversion!")
        else:
            print(f"[TRACE] stage3_sections_dir does NOT exist")

        # SECOND: Only convert entity-type files if NO section-specific files exist
        # Entity-type files are a fallback for when taxonomy mapping didn't run
        entity_files = {}

        if not section_files:
            stage3_parsing_dir = self.stage_dirs.get("stage_3_parsing", self.output_dir / "stage_3_parsing")

            for entity_type in ["publications", "education", "positions", "grants", "certifications",
                               "honors", "memberships", "service", "licensure", "mentoring"]:
                entity_dir = stage3_parsing_dir / entity_type
                if entity_dir.exists():
                    entity_file = entity_dir / f"{cv_name}_{entity_type}_parsed.json"
                    if entity_file.exists():
                        entity_files[entity_type] = entity_file

            if entity_files:
                print(f"Converting {len(entity_files)} entity types from unified pipeline...")
                print("  (Fallback mode - no taxonomy-mapped sections found)")
                adapter.convert_entity_parser_files(
                    entity_files=entity_files,
                    cv_id=cv_id,
                    output_dir=enriched_dir,
                    verbose=True
                )
        else:
            print("  Skipping entity-type files (using taxonomy-mapped sections instead)")

        if not section_files and len(entity_files) == 0:
            print("No unified parsed data found to convert")

        step_3_5_end = datetime.now()
        print(f"[TRACE] Step 3.5 completed at: {step_3_5_end.strftime('%H:%M:%S.%f')[:-3]}")
        print(f"[TRACE] Step 3.5 total time: {(step_3_5_end - step_3_5_start).total_seconds():.3f}s")
        print()

        # ========================================================================
        # STEP 4: Run legacy populate_cv.py (with all 7 special handlers)
        # ========================================================================
        step_4_start = datetime.now()
        print("STEP 4: Populating WCM template")
        print("="*80)
        print(f"[TRACE] Step 4 started at: {step_4_start.strftime('%H:%M:%S.%f')[:-3]}")

        # Get WCM template path
        template_path = get_template_path()

        # Use NEW direct python-docx populator (transparent, verifiable)
        from .direct_cv_populator import populate_cv_direct

        output_path = template_dir / f"{cv_name}_WCM.docx"

        print(f"Using direct python-docx CV populator...")
        print(f"  CV ID: {cv_id}")
        print(f"  Template: {template_path.name}")
        print(f"  Enriched files: {enriched_dir}")

        # CRITICAL: Check what enriched files exist RIGHT BEFORE populate_cv runs
        print(f"[TRACE] Checking enriched_dir status BEFORE populate_cv...")
        print(f"[TRACE] enriched_dir: {enriched_dir}")
        print(f"[TRACE] enriched_dir exists: {enriched_dir.exists()}")
        if enriched_dir.exists():
            enriched_files_pre_populate = list(enriched_dir.glob("*.json"))
            print(f"[TRACE] Enriched files available for populate_cv: {len(enriched_files_pre_populate)}")
            for f in enriched_files_pre_populate:
                file_size = f.stat().st_size
                print(f"[TRACE]   - {f.name} ({file_size} bytes)")
        else:
            print(f"[TRACE] ERROR: enriched_dir does NOT exist before populate_cv!")
        print()

        populate_start = datetime.now()
        print(f"[TRACE] Calling populate_cv_direct() at: {populate_start.strftime('%H:%M:%S.%f')[:-3]}")

        result = populate_cv_direct(
            cv_id=cv_id,
            template_path=template_path,
            enriched_dir=enriched_dir,
            output_path=output_path,
            verbose=True
        )

        populate_end = datetime.now()
        print(f"[TRACE] populate_cv_direct() returned at: {populate_end.strftime('%H:%M:%S.%f')[:-3]}")
        print(f"[TRACE] populate_cv_direct() took: {(populate_end - populate_start).total_seconds():.3f}s")

        # NEW: Result includes verification data
        verification = result.get('verification', {})

        print()
        print("="*80)
        print("WCM TEMPLATE GENERATION COMPLETE!")
        print("="*80)
        print(f"✓ Sections populated: {result.get('sections_populated', 0)}")
        print(f"✓ Total entries: {result.get('total_entries', 0)}")

        # NEW: Show verification results
        print(f"✓ Verified has data: {result.get('verified_has_data', False)}")
        print(f"✓ Name found: {verification.get('name_found', False)} - '{verification.get('name_text', '')}'")
        print(f"✓ Data rows in tables: {verification.get('data_rows', 0)}")

        # CHECK IF FILE ACTUALLY EXISTS AND HAS DATA
        if output_path.exists():
            if result.get('verified_has_data'):
                print(f"✓ Output: {output_path.name} (VERIFIED WITH DATA)")
            else:
                print(f"⚠️  Output: {output_path.name} (FILE EXISTS BUT MAY BE EMPTY)")
        else:
            print(f"✗ Output file NOT created: {output_path.name}")

        print("="*80)
        print()

        # Extract record counts
        records_processed = {}  # Not used in new implementation
        total_records = result.get('total_entries', 0)

        # Save metadata (only include output_file if it actually exists)
        metadata = {
            "template_source": str(template_path),
            "generation_timestamp": datetime.now().isoformat(),
            "records_processed": records_processed,
            "total_records": total_records,
            "sections_populated": result.get('sections_populated', 0),
            "sections_extracted": extraction_result.get('sections_extracted', 0),
            "publications_enriched": enrichment_result.get('publications', {}).get('total_enriched', 0),
            "integration_method": "direct_python_docx",
            "verification": verification,
            "pipeline_stages": {
                "classified": str(classified_file),
                "extracted": str(extracted_dir),
                "enriched": str(enriched_dir)
            }
        }

        # Only add output_file to metadata if it actually exists AND has data
        if output_path.exists() and result.get('verified_has_data'):
            metadata["output_file"] = str(output_path)
            metadata["pipeline_stages"]["template"] = str(output_path)
            metadata["success"] = True
        else:
            metadata["output_file"] = str(output_path) if output_path.exists() else None
            metadata["success"] = False
            if not output_path.exists():
                metadata["errors"] = ['No output file created']
            else:
                metadata["errors"] = ['Output file created but contains no data']

        metadata_path = template_dir / f"{cv_name}_template_metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        # Store results
        self.results["stages"]["stage_4_template_generation"] = {
            "output_file": str(output_path),
            "metadata_file": str(metadata_path),
            "records_processed": metadata["records_processed"],
            "total_records": total_records
        }

        return self.results["stages"]["stage_4_template_generation"]

    def _OLD_run_stage_4_template_generation_DEPRECATED(self) -> Dict[str, Any]:
        """
        OLD DEPRECATED IMPLEMENTATION - KEPT FOR REFERENCE ONLY

        This was the unified pipeline approach that tried to use generic parsers
        and adapt data to legacy format. Replaced with full legacy integration.
        """
        # Old code preserved but not executed
        if False:  # Disabled old inline approach
            # Old approach - kept for reference but disabled
            print("Using advanced legacy handlers for template population...")
            print()

            # This code is preserved but not executed
            parsed_data = {}
            if False and parsed_data.get("publications"):
                print("Enriching publications with PMID/PMCID lookup...")
                enricher = PubMedEnricher(verbose=False)
                enriched_count = 0

                for pub in parsed_data["publications"]:
                    # Convert to format expected by enricher
                    pub_for_enrichment = {
                        'structured_data': {
                            'title': pub.get('title', ''),
                            'authors': ', '.join(pub.get('authors', [])) if isinstance(pub.get('authors'), list) else pub.get('authors', ''),
                            'journal': pub.get('journal', ''),
                            'year': pub.get('year', ''),
                            'volume': pub.get('volume', ''),
                            'doi': pub.get('doi', ''),
                            'pmid': pub.get('pmid', ''),
                            'pmcid': pub.get('pmcid', '')
                        }
                    }

                    enriched_pub, was_enriched = enricher.enrich_publication(pub_for_enrichment)

                    if was_enriched:
                        # Update original publication with enriched IDs
                        pub['doi'] = enriched_pub['structured_data'].get('doi', pub.get('doi', ''))
                        pub['pmid'] = enriched_pub['structured_data'].get('pmid', pub.get('pmid', ''))
                        pub['pmcid'] = enriched_pub['structured_data'].get('pmcid', pub.get('pmcid', ''))
                        enriched_count += 1

                if enriched_count > 0:
                    print(f"  ✓ Enriched {enriched_count} publications with identifiers")
                else:
                    print(f"  ℹ️  No publications needed enrichment")
                print()

            # Load template using legacy navigator
            doc = load_template(str(template_path))

            # Use personal information extracted at pipeline start (Section A)
            print("Using personal information from pipeline initialization...")
            personal_info = self.personal_info
            if not personal_info:
                # Fallback: extract now if not already done (shouldn't happen)
                print("  ⚠️  Personal info not found, extracting now...")
                personal_info = extract_personal_info(str(self.cv_path))
                self.personal_info = personal_info
            print(f"  ✓ Name: {personal_info.get('full_name', 'NOT FOUND')}")
            print()

            # Convert personal_info to expected format for populate function
            # populate_section_a_personal_data expects: {'parsed_entries': [{'Full Name': '...'}]}
            # But extract_personal_info returns: {'full_name': '...', 'work_email': '...'}
            personal_data_formatted = {
                'parsed_entries': [{
                    'Full Name': personal_info.get('full_name', ''),
                    'Work Email': personal_info.get('work_email', ''),
                    'Office Phone': personal_info.get('office_phone', ''),
                    'Office Address': personal_info.get('office_address', ''),
                    'Cell Phone': personal_info.get('cell_phone', ''),
                    'Personal Email': personal_info.get('personal_email', ''),
                    'Home Address': personal_info.get('home_address', ''),
                    'ORCID': personal_info.get('orcid', ''),
                    'Department': personal_info.get('department', ''),
                    'Institution': personal_info.get('institution', '')
                }]
            }

            # Populate Section A (Personal Data) using legacy handler
            print("Populating Section A: Personal Data...")
            personal_result = populate_section_a_personal_data(
                doc,
                personal_data_formatted,
                verbose=True
            )
            if personal_result.get('success'):
                print(f"  ✓ Personal data populated successfully")
            else:
                print(f"  ⚠️  {personal_result.get('error', 'Unknown error')}")
            print()

            # Convert parsed data to legacy format
            legacy_data = self._convert_to_legacy_format(parsed_data)

            # Extract CV owner name for author bolding
            # Prefer extracted personal_info, fall back to filename parsing
            # Legacy handler expects format like "Yount, Kathryn" or "LastName, FirstName"
            cv_owner_name_raw = personal_info.get('full_name', '').strip()

            # Convert name format: "FirstName LastName" → "LastName, FirstName"
            cv_owner_name = None
            if cv_owner_name_raw:
                # Try to parse the name
                name_parts = cv_owner_name_raw.split()
                if len(name_parts) >= 2:
                    # Assume last part is last name, everything else is first/middle
                    last_name = name_parts[-1]
                    first_names = ' '.join(name_parts[:-1])
                    cv_owner_name = f"{last_name}, {first_names}"
                    print(f"Converted name: '{cv_owner_name_raw}' → '{cv_owner_name}'")
                elif len(name_parts) == 1:
                    # Single name - use as-is
                    cv_owner_name = name_parts[0]
                else:
                    cv_owner_name = cv_owner_name_raw

            # If no name from extraction, try parsing from filename
            if not cv_owner_name and hasattr(self, 'cv_path'):
                name_parts = self.cv_path.stem.split('_')
                if len(name_parts) >= 2:
                    # Assume format like "2079_Zahida" or "CV_2079_LastName_FirstName"
                    if name_parts[0].isdigit():
                        # Format: "2079_Zahida" -> use "Zahida" as last name
                        cv_owner_name = name_parts[1]
                    elif len(name_parts) >= 4:
                        # Format: "CV_2079_LastName_FirstName" -> "LastName, FirstName"
                        last_name = name_parts[2]
                        first_name = name_parts[3]
                        cv_owner_name = f"{last_name}, {first_name}"
                    else:
                        cv_owner_name = name_parts[-1]  # Use last part as name
                print(f"Extracted name from filename: '{cv_owner_name}'")

            if cv_owner_name:
                print(f"✓ CV Owner Name: {cv_owner_name}")
            else:
                print("⚠️  Warning: Could not extract CV owner name for author bolding")

            # Populate bibliography with advanced features (categorization, bolding, etc.)
            if 'bibliography' in legacy_data:
                print("Populating bibliography with subsection categorization...")
                result = populate_section_s_bibliography(
                    doc,
                    legacy_data['bibliography'],
                    cv_owner_name=cv_owner_name,
                    verbose=True
                )
                if result.get('success'):
                    print(f"  ✓ Inserted {result['entries_inserted']} publications across {len(result.get('subsections_populated', {}))} subsections")
                print()

            # Populate education using generic table insertion
            if 'education' in legacy_data and legacy_data['education']['entries']:
                print(f"Populating education ({len(legacy_data['education']['entries'])} entries)...")
                section_title = "EDUCATION"
                fields_order = ['Degree', 'Major/Field', 'Institution', 'City', 'State/Province', 'Country', 'Dates', 'Year Awarded']

                # Format entries to semicolon-separated strings
                formatted_entries = []
                for entry in legacy_data['education']['entries']:
                    formatted = format_entry_to_wcm(entry, fields_order, use_enrichment=False)
                    formatted_entries.append(formatted)

                # Insert using legacy handler
                result = insert_entry_list(
                    doc,
                    section_title,
                    formatted_entries,
                    section_id='B',
                    enriched_entries=legacy_data['education']['entries']
                )
                if result.get('success'):
                    print(f"  ✓ Inserted {result['entries_inserted']} education entries")
                else:
                    print(f"  ⚠️  {result.get('error', 'Unknown error')}")
                print()

            # Populate positions
            if 'positions' in legacy_data and legacy_data['positions']['entries']:
                print(f"Populating positions ({len(legacy_data['positions']['entries'])} entries)...")

                # Split into academic and other positions
                academic_positions = [p for p in legacy_data['positions']['entries'] if p.get('_is_academic')]
                other_positions = [p for p in legacy_data['positions']['entries'] if not p.get('_is_academic')]

                fields_order = ['Title', 'Institution', 'City', 'State/Province', 'Country', 'Dates']

                # Populate Academic Appointments
                if academic_positions:
                    formatted_entries = [format_entry_to_wcm(p, fields_order, use_enrichment=False) for p in academic_positions]
                    result = insert_entry_list(
                        doc,
                        "Academic Appointments",
                        formatted_entries,
                        section_id='D1',
                        enriched_entries=academic_positions
                    )
                    if result.get('success'):
                        print(f"  ✓ Inserted {result['entries_inserted']} academic positions")

                # Populate Other Professional Positions
                if other_positions:
                    formatted_entries = [format_entry_to_wcm(p, fields_order, use_enrichment=False) for p in other_positions]
                    result = insert_entry_list(
                        doc,
                        "Other Professional Positions",
                        formatted_entries,
                        section_id='E',
                        enriched_entries=other_positions
                    )
                    if result.get('success'):
                        print(f"  ✓ Inserted {result['entries_inserted']} other positions")
                print()

            # Populate grants using Section M handler
            if 'grants' in legacy_data and legacy_data['grants']['entries']:
                print(f"Populating grants ({len(legacy_data['grants']['entries'])} entries)...")
                result = populate_section_m_research(doc, legacy_data['grants'], verbose=False)
                if result.get('success'):
                    print(f"  ✓ Inserted {result.get('entries_inserted', 0)} grant entries")
                else:
                    print(f"  ⚠️  {result.get('error', 'Unknown error')}")
                print()

            # Save populated template
            output_path = template_dir / f"{cv_name}_wcm_template.docx"
            doc.save(str(output_path))
            print(f"✓ Template saved: {output_path.name}")
            print()

        else:
            # Fallback to simple filler
            print("Using simple template filler (legacy handlers not available)...")
            print()

            from .wcm_template_filler_v2 import WCMTemplateFiller
            filler = WCMTemplateFiller(str(template_path))

            # Fill each section with its data
            if parsed_data.get("education"):
                filler.fill_education(parsed_data["education"])

            if parsed_data.get("positions"):
                filler.fill_positions(parsed_data["positions"])

            if parsed_data.get("publications"):
                filler.fill_bibliography(parsed_data["publications"])

            if parsed_data.get("grants"):
                filler.fill_research_support(parsed_data["grants"])

            # Save populated template
            output_path = template_dir / f"{cv_name}_wcm_template.docx"
            filler.save(str(output_path))
            print(f"✓ Template saved: {output_path.name}")
            print()

        # Save metadata
        metadata = {
            "template_source": str(template_path),
            "output_file": str(output_path),
            "generation_timestamp": datetime.now().isoformat(),
            "records_processed": records_processed,
            "total_records": total_records,
            "sections_populated": result.get('sections_populated', 0) if LEGACY_HANDLERS_AVAILABLE else 0,
            "integration_method": "legacy_populate_cv" if LEGACY_HANDLERS_AVAILABLE else "simple_filler"
        }

        metadata_path = template_dir / f"{cv_name}_template_metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        # Store results
        self.results["stages"]["stage_4_template_generation"] = {
            "output_file": str(output_path),
            "metadata_file": str(metadata_path),
            "records_processed": metadata["records_processed"],
            "total_records": total_records
        }

        return self.results["stages"]["stage_4_template_generation"]

    def generate_summary_report(self) -> str:
        """
        Generate a summary report of the pipeline execution.

        Returns:
            Path to summary report JSON
        """
        print("="*80)
        print("GENERATING SUMMARY REPORT")
        print("="*80)
        print()

        summary_path = self.output_dir / f"{self.cv_path.stem}_pipeline_summary.json"

        with open(summary_path, 'w') as f:
            json.dump(self.results, f, indent=2)

        print(f"✓ Summary report: {summary_path}")
        print()

        return str(summary_path)

    def run(self) -> Dict[str, Any]:
        """
        Execute the complete pipeline.

        Returns:
            Pipeline results dictionary
        """
        print("="*80)
        print("CV PARSING PIPELINE")
        print("="*80)
        print(f"Input: {self.cv_path}")
        print(f"Output directory: {self.output_dir}")
        print()

        try:
            # Extract personal info FIRST (used to filter name from other sections)
            print("="*80)
            print("PRELIMINARY: EXTRACTING PERSONAL INFORMATION")
            print("="*80)
            print("Extracting name and contact info from first page...")
            self.personal_info = extract_personal_info(str(self.cv_path))
            print(f"✓ Extracted name: {self.personal_info.get('full_name', 'NOT FOUND')}")
            print()

            # Stage 1: Segmentation
            segmented_data = self.run_stage_1_segmentation()
            segmented_path = self.results["stages"]["stage_1_segmentation"]["output_file"]

            # Stage 2a & 2b: Entry Delimitation and Extraction (future implementation)
            # TODO: Add explicit stage 2a/2b when entry delimitation logic is integrated
            # For now, these are implicit within the segmentation stage

            # Stage 3: Taxonomy Mapping (formerly Stage 2)
            mapped_data = self.run_stage_3_taxonomy_mapping(segmented_path)
            taxonomy_path = self.results["stages"]["stage_3_taxonomy_mapping"]["output_file"]

            # Stage 3: Section-Specific Parsing
            parsed_data = self.run_stage_3_section_parsing(segmented_path, mapped_data, taxonomy_path)

            # Stage 4: WCM Template Generation
            template_result = self.run_stage_4_template_generation()

            # Generate Summary Report
            summary_path = self.generate_summary_report()

            # Print final summary
            print("="*80)
            print("PIPELINE COMPLETE")
            print("="*80)
            print(f"Stage 1 - Segmentation: {self.results['stages']['stage_1_segmentation']['num_groups']} groups, {self.results['stages']['stage_1_segmentation']['total_entries']} entries")
            print(f"Stage 3 - Taxonomy Mapping: {self.results['stages']['stage_3_taxonomy_mapping']['total_sections']} sections, avg confidence {self.results['stages']['stage_3_taxonomy_mapping']['avg_confidence']:.2f}")
            print()
            print("Stage 4 - Section Parsing:")
            for section_type in ["publications", "education", "positions", "grants", "certifications", "honors", "memberships", "service", "licensure", "mentoring"]:
                key = f"stage_3_{section_type}_parsing"
                if key in self.results["stages"]:
                    print(f"  {section_type.capitalize()}: {self.results['stages'][key]['total_items']} items ({self.results['stages'][key]['high_confidence']} high confidence)")
            print()
            print(f"Stage 4 - WCM Template Generation:")
            if "stage_4_template_generation" in self.results["stages"]:
                stage_4 = self.results["stages"]["stage_4_template_generation"]
                print(f"  Template: {Path(stage_4['output_file']).name}")
                print(f"  Total records: {stage_4['total_records']}")
            print()
            print(f"Summary report: {summary_path}")
            print()

            return self.results

        except Exception as e:
            print(f"✗ Pipeline failed: {e}")
            import traceback
            traceback.print_exc()
            raise

    # ========================================================================
    # Async Wrappers for Web App Integration
    # ========================================================================

    async def run_async(self) -> Dict[str, Any]:
        """
        Execute the complete pipeline asynchronously (for web app).

        This is an async wrapper around the synchronous run() method.
        Uses run_in_executor to avoid blocking the event loop.

        Returns:
            Pipeline results dictionary
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.run)

    async def run_stage_1_async(self) -> Dict[str, Any]:
        """Run Stage 1 (Segmentation) asynchronously."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.run_stage_1_segmentation)

        # Return enriched result with file paths
        return {
            **result,
            "output_file": self.results["stages"]["stage_1_segmentation"]["output_file"],
            "num_sections": self.results["stages"]["stage_1_segmentation"]["num_groups"],
            "total_entries": self.results["stages"]["stage_1_segmentation"]["total_entries"],
        }

    async def run_stage_2_async(self, stage1_result: Dict[str, Any]) -> Dict[str, Any]:
        """Run Stage 2 (Taxonomy Mapping) asynchronously."""
        segmented_path = stage1_result["output_file"]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.run_stage_3_taxonomy_mapping, segmented_path)

        # Return enriched result
        return {
            **result,
            "output_file": self.results["stages"]["stage_2_taxonomy_mapping"]["output_file"],
            "total_sections": self.results["stages"]["stage_2_taxonomy_mapping"]["total_sections"],
            "avg_confidence": self.results["stages"]["stage_2_taxonomy_mapping"]["avg_confidence"],
        }

    async def run_stage_3_async(
        self,
        stage1_result: Dict[str, Any],
        stage2_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run Stage 3 (Section Parsing) asynchronously."""
        segmented_path = stage1_result["output_file"]
        taxonomy_path = stage2_result["output_file"]

        # Load mapped data from stage 2
        with open(taxonomy_path) as f:
            mapped_data = json.load(f)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self.run_stage_3_section_parsing,
            segmented_path,
            mapped_data,
            taxonomy_path
        )

        # Return enriched result with individual file paths
        stage3_results = {}
        total_cost = 0.0

        for section_type in ["publications", "education", "positions", "grants", "certifications", "honors", "memberships", "service", "licensure", "mentoring"]:
            key = f"stage_3_{section_type}_parsing"
            if key in self.results["stages"]:
                stage_info = self.results["stages"][key]
                stage3_results[section_type] = {
                    "output_file": stage_info["output_file"],
                    "total_items": stage_info["total_items"],
                    "high_confidence": stage_info["high_confidence"],
                }

                # Estimate cost (approximate)
                total_cost += stage_info.get("total_items", 0) * 0.001  # rough estimate

        # Include section-specific files
        section_files = []
        if "stage_3_sections" in self.results["stages"]:
            section_info = self.results["stages"]["stage_3_sections"]
            section_files = section_info.get("output_files", [])

        return {
            **result,
            **stage3_results,
            "cost": total_cost,
            "section_files": section_files,
            "num_section_files": len(section_files)
        }

    async def run_stage_4_async(self, stage3_results: Dict[str, Any]) -> Dict[str, Any]:
        """Run Stage 4 (WCM Template Generation) asynchronously."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self.run_stage_4_template_generation)

        # Return enriched result
        return {
            **result,
            "output_file": self.results["stages"]["stage_4_template_generation"]["output_file"],
            "metadata_file": self.results["stages"]["stage_4_template_generation"]["metadata_file"],
            "records_processed": self.results["stages"]["stage_4_template_generation"]["records_processed"],
            "cost": 0.01,  # Nominal cost for template generation
        }


def main():
    """
    Command-line interface for CV pipeline.
    """
    parser = argparse.ArgumentParser(
        description="CV Parsing Pipeline - Processes CVs through segmentation, taxonomy mapping, and section-specific parsing"
    )
    parser.add_argument(
        "cv_path",
        type=str,
        help="Path to CV file (.docx or .pdf)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory for results (defaults to same directory as input)"
    )

    args = parser.parse_args()

    # Validate input
    if not os.path.exists(args.cv_path):
        print(f"Error: CV file not found: {args.cv_path}", file=sys.stderr)
        sys.exit(1)

    # Run pipeline
    try:
        pipeline = CVPipeline(cv_path=args.cv_path, output_dir=args.output_dir)
        results = pipeline.run()
        sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
