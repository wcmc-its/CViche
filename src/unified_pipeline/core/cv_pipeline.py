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
import logging
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Awaitable
from datetime import datetime

logger = logging.getLogger(__name__)

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
    logger.warning(f"Legacy handlers not available: {e}")
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
            logger.info(message)

    def run_stage_1_segmentation(self) -> Dict[str, Any]:
        """
        Stage 1: Segment CV into structured sections and entries.

        Returns:
            Segmented CV data
        """
        logger.info("="*80)
        logger.info("STAGE 1: CV SEGMENTATION")
        logger.info("="*80)
        logger.info(f"Input: {self.cv_path}")
        logger.info(f"Output: {self.stage_dirs['stage_1']}")
        logger.info("")

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

        logger.info(f"✓ Segmentation complete: {segmented_path}")
        logger.info("")

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
        logger.info("="*80)
        logger.info("STAGE 3: TAXONOMY MAPPING")
        logger.info("="*80)
        logger.info(f"Output: {self.stage_dirs['stage_3']}")
        logger.info("")

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

        logger.info(f"✓ Taxonomy mapping complete: {result['output_file']}")
        logger.info("")

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
        logger.info("="*80)
        logger.info("STAGE 3: FULL WCM SECTION EXTRACTION")
        logger.info("="*80)
        logger.info("Using 71 specialized extractors organized by WCM section ID")
        logger.info("")

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

        logger.info("Step 1: Creating classified format...")
        classified_file = orchestrator.create_classified_format(
            segmented_file=segmented_file,
            mapped_file=mapped_file,
            verbose=True
        )

        logger.info("")
        logger.info("Step 2: Running WCM section extractors...")
        extracted_files = orchestrator.run_section_extractors(
            classified_file=classified_file,
            sections_to_extract=None,  # Extract all sections
            verbose=True
        )

        # Load all extracted data from extractor outputs
        logger.info("")
        logger.info("Step 3: Organizing extracted data...")

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
                logger.info(f"  [Section {section_id}] → {entity_type}: {len(entries)} entries")

        # ALSO load segmented entries grouped by taxonomy section
        # This gives us the raw text_snippet data for LLM parsing
        logger.info("")
        logger.info("Loading segmented data grouped by taxonomy...")
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
                logger.info(f"  [Section {section_id}] → {entity_type}: {len(entries)} entries (from segmentation)")

        logger.info("")
        logger.info(f"Total entries extracted: {total_entries_extracted}")
        for entity_type, data in parsed_data.items():
            logger.info(f"  {entity_type}: {len(data)} entries")

        # ============================================================================
        # STEP 3: Parse extracted data with LLM parsers
        # ============================================================================
        logger.info("")
        logger.info("=" * 80)
        logger.info("Step 3: Parsing extracted data with LLM parsers...")
        logger.info("=" * 80)

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
                    logger.info(f"  ⊘ Skipping CV owner name: {text[:60]}...")
                    continue

                prepared.append({
                    "text_snippet": text,
                    "id": f"entry_{idx}",
                    "original_entry": entry
                })
            return prepared

        # Parse education
        if parsed_data["education"]:
            logger.info(f"\nParsing education entries ({len(parsed_data['education'])} entries)...")
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
                    logger.info(f"  ✓ Parsed {len(education_list)} education entries ({len(structured_entries)} already structured)")
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
                    logger.info(f"  ✓ Normalized {len(normalized_education)} pre-structured education entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing education: {e}")

        # Parse positions
        if parsed_data["positions"]:
            logger.info(f"\nParsing positions entries ({len(parsed_data['positions'])} entries)...")
            try:
                position_entries = prepare_entries_for_parsing(parsed_data["positions"])
                result = parse_positions_section(position_entries)
                parsed_data["positions"] = result.get("positions", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['positions'])} position entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing positions: {e}")

        # Parse grants
        if parsed_data["grants"]:
            logger.info(f"\nParsing grants entries ({len(parsed_data['grants'])} entries)...")
            try:
                grant_entries = prepare_entries_for_parsing(parsed_data["grants"])
                result = parse_grants_section(grant_entries)
                parsed_data["grants"] = result.get("grants", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['grants'])} grant entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing grants: {e}")

        # Parse publications (more complex due to subsections)
        if parsed_data["publications"]:
            logger.info(f"\nParsing publication entries ({len(parsed_data['publications'])} entries)...")
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
                logger.info(f"  ✓ Parsed {len(parsed_data['publications'])} publication entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing publications: {e}")

        # Parse certifications
        if parsed_data["certifications"]:
            logger.info(f"\nParsing certification entries ({len(parsed_data['certifications'])} entries)...")
            try:
                cert_entries = prepare_entries_for_parsing(parsed_data["certifications"])
                result = parse_certifications_section(cert_entries)
                parsed_data["certifications"] = result.get("certifications", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['certifications'])} certification entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing certifications: {e}")

        # Parse honors
        if parsed_data["honors"]:
            logger.info(f"\nParsing honors/awards entries ({len(parsed_data['honors'])} entries)...")
            try:
                honor_entries = prepare_entries_for_parsing(parsed_data["honors"])
                result = parse_honors_section(honor_entries)
                parsed_data["honors"] = result.get("honors", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['honors'])} honors/awards entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing honors: {e}")

        # Parse memberships
        if parsed_data["memberships"]:
            logger.info(f"\nParsing membership entries ({len(parsed_data['memberships'])} entries)...")
            try:
                membership_entries = prepare_entries_for_parsing(parsed_data["memberships"])
                result = parse_memberships_section(membership_entries)
                parsed_data["memberships"] = result.get("memberships", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['memberships'])} membership entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing memberships: {e}")

        # Parse service
        if parsed_data["service"]:
            logger.info(f"\nParsing service entries ({len(parsed_data['service'])} entries)...")
            try:
                service_entries = prepare_entries_for_parsing(parsed_data["service"])
                result = parse_service_section(service_entries)
                parsed_data["service"] = result.get("service", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['service'])} service entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing service: {e}")

        # Parse licensure
        if parsed_data["licensure"]:
            logger.info(f"\nParsing licensure entries ({len(parsed_data['licensure'])} entries)...")
            try:
                licensure_entries = prepare_entries_for_parsing(parsed_data["licensure"])
                result = parse_licensure_section(licensure_entries)
                parsed_data["licensure"] = result.get("licensure", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['licensure'])} licensure entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing licensure: {e}")

        # Parse mentoring
        if parsed_data["mentoring"]:
            logger.info(f"\nParsing mentoring entries ({len(parsed_data['mentoring'])} entries)...")
            try:
                mentoring_entries = prepare_entries_for_parsing(parsed_data["mentoring"])
                result = parse_mentoring_section(mentoring_entries)
                parsed_data["mentoring"] = result.get("mentoring", result) if isinstance(result, dict) else result
                logger.info(f"  ✓ Parsed {len(parsed_data['mentoring'])} mentoring entries")
            except Exception as e:
                logger.exception(f"  ✗ Error parsing mentoring: {e}")

        logger.info("")
        logger.info("=" * 80)
        logger.info("LLM Parsing Complete")
        logger.info("=" * 80)
        for entity_type, data in parsed_data.items():
            high_conf = sum(1 for item in data if item.get("confidence", 0) >= 0.8)
            logger.info(f"  {entity_type}: {len(data)} entries ({high_conf} high confidence)")
        logger.info("")

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
            logger.info("")
            logger.info("Generating individual section files based on taxonomy...")
            section_files = self._generate_section_specific_files(
                segmented_path=segmented_path,
                taxonomy_path=taxonomy_path,
                parsed_data=parsed_data,
                base_name=base_name
            )
            logger.info(f"  ✓ Generated {len(section_files)} section-specific files")
            logger.info("")

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
                logger.info(f"  WARNING: No section code found for '{section_id}', using fallback: {section_code}")

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
            logger.info(f"  • {section_code} ({section_id}): {len(data['entries'])} entries → {output_path.name}")

        # Store in results for orchestrator
        self.results["stages"]["stage_3_sections"] = {
            "output_files": output_files,
            "num_sections": len(section_data),
            "output_dir": str(section_output_dir)
        }

        return output_files

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
        logger.info("="*80)
        logger.info("STAGE 4: WCM TEMPLATE GENERATION (COMPLETE LEGACY INTEGRATION)")
        logger.info("="*80)
        logger.info(f"Output: {self.stage_dirs['stage_4']}")
        logger.info("")
        logger.info("Using FULL legacy system:")
        logger.info("  • 71 specialized extractors (Stage 2C)")
        logger.info("  • PubMed + ROR enrichment (Stage 2D)")
        logger.info("  • populate_cv.py with ALL 7 special handlers (Stage 3)")
        logger.info("")

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
        logger.info("STEP 1: Converting unified → classified format")
        logger.info("="*80)

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
        logger.info("STEP 2: Running legacy extractors")
        logger.info("="*80)

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
        logger.info("STEP 3: Running legacy enrichment")
        logger.info("="*80)

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
        logger.info("STEP 3.5: Converting unified parsed data to legacy format")
        logger.info("="*80)
        logger.info(f"[TRACE] Step 3.5 started at: {step_3_5_start.strftime('%H:%M:%S.%f')[:-3]}")

        from .legacy_format_adapter import UnifiedToLegacyAdapter

        adapter = UnifiedToLegacyAdapter()

        # FIRST: Convert section-specific files (stage_3_sections/)
        # These contain the taxonomy-mapped data organized by WCM section ID
        # This is the preferred source as it preserves the exact taxonomy mapping
        stage3_sections_dir = self.stage_dirs.get("stage_3_sections", self.output_dir / "stage_3_sections")
        section_files = []

        logger.info(f"[TRACE] stage3_sections_dir: {stage3_sections_dir}")
        logger.info(f"[TRACE] Directory exists: {stage3_sections_dir.exists()}")

        if stage3_sections_dir.exists():
            # Match both old (section_*) and new ({code}_*) naming patterns
            glob_pattern = f"*_{cv_name}_parsed.json"
            logger.info(f"[TRACE] Glob pattern: {glob_pattern}")
            section_files = list(stage3_sections_dir.glob(glob_pattern))
            logger.info(f"[TRACE] Found {len(section_files)} matching files:")
            for f in section_files:
                logger.info(f"[TRACE]   - {f.name}")

            if section_files:
                logger.info(f"Converting {len(section_files)} section files from unified pipeline...")
                logger.info("  (Using taxonomy-mapped sections as primary data source)")

                # Log enriched_dir status BEFORE conversion
                logger.info(f"[TRACE] enriched_dir BEFORE conversion: {enriched_dir}")
                logger.info(f"[TRACE] enriched_dir exists: {enriched_dir.exists()}")
                if enriched_dir.exists():
                    existing_enriched = list(enriched_dir.glob("*.json"))
                    logger.info(f"[TRACE] Existing enriched files BEFORE: {len(existing_enriched)}")
                    for f in existing_enriched:
                        logger.info(f"[TRACE]   - {f.name}")

                # Run adapter conversion
                conversion_start = datetime.now()
                logger.info(f"[TRACE] Starting adapter.convert_all_sections() at: {conversion_start.strftime('%H:%M:%S.%f')[:-3]}")

                created_files = adapter.convert_all_sections(
                    unified_sections_dir=stage3_sections_dir,
                    cv_id=cv_id,
                    output_dir=enriched_dir,
                    verbose=True
                )

                conversion_end = datetime.now()
                logger.info(f"[TRACE] Adapter conversion completed at: {conversion_end.strftime('%H:%M:%S.%f')[:-3]}")
                logger.info(f"[TRACE] Conversion took: {(conversion_end - conversion_start).total_seconds():.3f}s")
                logger.info(f"[TRACE] Adapter returned {len(created_files) if created_files else 0} created files")

                # Log enriched_dir status AFTER conversion
                if enriched_dir.exists():
                    enriched_after = list(enriched_dir.glob("*.json"))
                    logger.info(f"[TRACE] Enriched files AFTER conversion: {len(enriched_after)}")
                    for f in enriched_after:
                        logger.info(f"[TRACE]   - {f.name}")
                else:
                    logger.info(f"[TRACE] ERROR: enriched_dir does not exist after conversion!")
        else:
            logger.info(f"[TRACE] stage3_sections_dir does NOT exist")

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
                logger.info(f"Converting {len(entity_files)} entity types from unified pipeline...")
                logger.info("  (Fallback mode - no taxonomy-mapped sections found)")
                adapter.convert_entity_parser_files(
                    entity_files=entity_files,
                    cv_id=cv_id,
                    output_dir=enriched_dir,
                    verbose=True
                )
        else:
            logger.info("  Skipping entity-type files (using taxonomy-mapped sections instead)")

        if not section_files and len(entity_files) == 0:
            logger.info("No unified parsed data found to convert")

        step_3_5_end = datetime.now()
        logger.info(f"[TRACE] Step 3.5 completed at: {step_3_5_end.strftime('%H:%M:%S.%f')[:-3]}")
        logger.info(f"[TRACE] Step 3.5 total time: {(step_3_5_end - step_3_5_start).total_seconds():.3f}s")
        logger.info("")

        # ========================================================================
        # STEP 4: Run legacy populate_cv.py (with all 7 special handlers)
        # ========================================================================
        step_4_start = datetime.now()
        logger.info("STEP 4: Populating WCM template")
        logger.info("="*80)
        logger.info(f"[TRACE] Step 4 started at: {step_4_start.strftime('%H:%M:%S.%f')[:-3]}")

        # Get WCM template path
        template_path = get_template_path()

        # Use NEW direct python-docx populator (transparent, verifiable)
        from .direct_cv_populator import populate_cv_direct

        output_path = template_dir / f"{cv_name}_WCM.docx"

        logger.info(f"Using direct python-docx CV populator...")
        logger.info(f"  CV ID: {cv_id}")
        logger.info(f"  Template: {template_path.name}")
        logger.info(f"  Enriched files: {enriched_dir}")

        # CRITICAL: Check what enriched files exist RIGHT BEFORE populate_cv runs
        logger.info(f"[TRACE] Checking enriched_dir status BEFORE populate_cv...")
        logger.info(f"[TRACE] enriched_dir: {enriched_dir}")
        logger.info(f"[TRACE] enriched_dir exists: {enriched_dir.exists()}")
        if enriched_dir.exists():
            enriched_files_pre_populate = list(enriched_dir.glob("*.json"))
            logger.info(f"[TRACE] Enriched files available for populate_cv: {len(enriched_files_pre_populate)}")
            for f in enriched_files_pre_populate:
                file_size = f.stat().st_size
                logger.info(f"[TRACE]   - {f.name} ({file_size} bytes)")
        else:
            logger.info(f"[TRACE] ERROR: enriched_dir does NOT exist before populate_cv!")
        logger.info("")

        populate_start = datetime.now()
        logger.info(f"[TRACE] Calling populate_cv_direct() at: {populate_start.strftime('%H:%M:%S.%f')[:-3]}")

        result = populate_cv_direct(
            cv_id=cv_id,
            template_path=template_path,
            enriched_dir=enriched_dir,
            output_path=output_path,
            verbose=True
        )

        populate_end = datetime.now()
        logger.info(f"[TRACE] populate_cv_direct() returned at: {populate_end.strftime('%H:%M:%S.%f')[:-3]}")
        logger.info(f"[TRACE] populate_cv_direct() took: {(populate_end - populate_start).total_seconds():.3f}s")

        # NEW: Result includes verification data
        verification = result.get('verification', {})

        logger.info("")
        logger.info("="*80)
        logger.info("WCM TEMPLATE GENERATION COMPLETE!")
        logger.info("="*80)
        logger.info(f"✓ Sections populated: {result.get('sections_populated', 0)}")
        logger.info(f"✓ Total entries: {result.get('total_entries', 0)}")

        # NEW: Show verification results
        logger.info(f"✓ Verified has data: {result.get('verified_has_data', False)}")
        logger.info(f"✓ Name found: {verification.get('name_found', False)} - '{verification.get('name_text', '')}'")
        logger.info(f"✓ Data rows in tables: {verification.get('data_rows', 0)}")

        # CHECK IF FILE ACTUALLY EXISTS AND HAS DATA
        if output_path.exists():
            if result.get('verified_has_data'):
                logger.info(f"✓ Output: {output_path.name} (VERIFIED WITH DATA)")
            else:
                logger.warning(f"⚠️  Output: {output_path.name} (FILE EXISTS BUT MAY BE EMPTY)")
        else:
            logger.error(f"✗ Output file NOT created: {output_path.name}")

        logger.info("="*80)
        logger.info("")

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

    def generate_summary_report(self) -> str:
        """
        Generate a summary report of the pipeline execution.

        Returns:
            Path to summary report JSON
        """
        logger.info("="*80)
        logger.info("GENERATING SUMMARY REPORT")
        logger.info("="*80)
        logger.info("")

        summary_path = self.output_dir / f"{self.cv_path.stem}_pipeline_summary.json"

        with open(summary_path, 'w') as f:
            json.dump(self.results, f, indent=2)

        logger.info(f"✓ Summary report: {summary_path}")
        logger.info("")

        return str(summary_path)

    def run(self) -> Dict[str, Any]:
        """
        Execute the complete pipeline.

        Returns:
            Pipeline results dictionary
        """
        logger.info("="*80)
        logger.info("CV PARSING PIPELINE")
        logger.info("="*80)
        logger.info(f"Input: {self.cv_path}")
        logger.info(f"Output directory: {self.output_dir}")
        logger.info("")

        try:
            # Extract personal info FIRST (used to filter name from other sections)
            logger.info("="*80)
            logger.info("PRELIMINARY: EXTRACTING PERSONAL INFORMATION")
            logger.info("="*80)
            logger.info("Extracting name and contact info from first page...")
            self.personal_info = extract_personal_info(str(self.cv_path))
            logger.info(f"✓ Extracted name: {self.personal_info.get('full_name', 'NOT FOUND')}")
            logger.info("")

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
            logger.info("="*80)
            logger.info("PIPELINE COMPLETE")
            logger.info("="*80)
            logger.info(f"Stage 1 - Segmentation: {self.results['stages']['stage_1_segmentation']['num_groups']} groups, {self.results['stages']['stage_1_segmentation']['total_entries']} entries")
            logger.info(f"Stage 3 - Taxonomy Mapping: {self.results['stages']['stage_3_taxonomy_mapping']['total_sections']} sections, avg confidence {self.results['stages']['stage_3_taxonomy_mapping']['avg_confidence']:.2f}")
            logger.info("")
            logger.info("Stage 4 - Section Parsing:")
            for section_type in ["publications", "education", "positions", "grants", "certifications", "honors", "memberships", "service", "licensure", "mentoring"]:
                key = f"stage_3_{section_type}_parsing"
                if key in self.results["stages"]:
                    logger.info(f"  {section_type.capitalize()}: {self.results['stages'][key]['total_items']} items ({self.results['stages'][key]['high_confidence']} high confidence)")
            logger.info("")
            logger.info(f"Stage 4 - WCM Template Generation:")
            if "stage_4_template_generation" in self.results["stages"]:
                stage_4 = self.results["stages"]["stage_4_template_generation"]
                logger.info(f"  Template: {Path(stage_4['output_file']).name}")
                logger.info(f"  Total records: {stage_4['total_records']}")
            logger.info("")
            logger.info(f"Summary report: {summary_path}")
            logger.info("")

            return self.results

        except Exception:
            logger.exception("✗ Pipeline failed")
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
        logger.error(f"Error: CV file not found: {args.cv_path}")
        sys.exit(1)

    # Run pipeline
    try:
        pipeline = CVPipeline(cv_path=args.cv_path, output_dir=args.output_dir)
        results = pipeline.run()
        sys.exit(0)

    except Exception as e:
        logger.exception(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
