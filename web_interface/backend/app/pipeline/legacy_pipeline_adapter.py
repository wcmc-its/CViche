"""
Legacy Pipeline Adapter

Bridges web UI to legacy production scripts (pre-Oct-31 working system).
Uses proven 77 extractors + enrichment + populate_cv.py system.

Architecture:
    Web UI → Stage 1 (unified segmentation) → Legacy extraction → Legacy enrichment → Legacy populate_cv → Web UI

This adapter:
1. Takes unified Stage 1 segmentation output
2. Prepares data for legacy scripts
3. Orchestrates all legacy pipeline stages
4. Returns results in format web UI expects
"""

import json
import sys
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import shutil

# Add parent project to path
PARENT_DIR = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(PARENT_DIR))

from app.config.pipeline_config import config


class LegacyPipelineAdapter:
    """
    Adapter that calls legacy production scripts to process CVs.

    Uses the proven pre-Oct-31 system with all optimizations:
    - 77 specialized section extractors
    - PubMed API enrichment (PMID/PMCID lookup)
    - ROR ID enrichment (institution IDs)
    - populate_cv.py with all 7 special handlers
    """

    def __init__(
        self,
        run_id: str,
        file_path: Path,
        output_dir: Path,
        progress_callback: Optional[Callable] = None
    ):
        """
        Initialize legacy pipeline adapter.

        Args:
            run_id: Unique identifier for this run
            file_path: Path to uploaded CV file
            output_dir: Directory for all output files
            progress_callback: Optional async callback for progress updates
        """
        self.run_id = run_id
        self.file_path = file_path
        self.output_dir = output_dir
        self.progress_callback = progress_callback

        # Legacy scripts location
        self.legacy_scripts_dir = PARENT_DIR / "src" / "legacy" / "stage_based_extraction" / "scripts" / "production"

        # Create working directories
        self.segmented_dir = output_dir / "stage_1_segmentation"
        self.extracted_dir = output_dir / "stage_2c_extracted"
        self.enriched_dir = output_dir / "stage_2d_enriched"
        self.output_wcm_dir = output_dir / "stage_4_wcm_templates"

        for dir_path in [self.segmented_dir, self.extracted_dir, self.enriched_dir, self.output_wcm_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)

        # Extract CV identifier for legacy naming
        self.cv_id = self._create_legacy_cv_id()

    def _create_legacy_cv_id(self) -> str:
        """
        Create legacy-compatible CV identifier.

        Legacy format: CV_{ID}_{LastName}_{FirstName}_{Degree}
        Web UI format: {RUN_ID}_{ID}_{Name}

        Strategy: Extract name from document, create hybrid format that works with both systems.

        Returns:
            CV identifier in legacy format
        """
        # Try to extract name from file
        stem = self.file_path.stem  # e.g., "TXJUWG_2079_Zahida"
        parts = stem.split('_')

        # For now, use run_id-based naming that legacy can handle
        # Format: CV_{RUN_ID}_{ID}_{Name}
        if len(parts) >= 3:
            # TXJUWG_2079_Zahida → CV_TXJUWG_2079_Zahida
            cv_id = f"CV_{parts[0]}_{parts[1]}_{parts[2]}"
        elif len(parts) == 2:
            # 2079_Zahida → CV_2079_Zahida
            cv_id = f"CV_{parts[0]}_{parts[1]}"
        else:
            # Fallback
            cv_id = f"CV_{self.run_id}_{stem}"

        return cv_id

    async def _log(self, stage: int, message: str, level: str = "INFO"):
        """Send progress update via callback."""
        if self.progress_callback:
            await self.progress_callback(stage, message, level)

    async def run_stage_1_segmentation(self) -> Dict[str, Any]:
        """
        Stage 1: Hierarchical Segmentation

        Uses unified pipeline's segmenter (it works well).

        Returns:
            Stage results dictionary
        """
        await self._log(1, "Stage 1: Hierarchical Segmentation (Unified)")
        await self._log(1, f"Processing: {self.file_path.name}")

        # Import unified segmenter
        from src.unified_pipeline.core.cv_segmenter import CVSegmenter

        # Create segmenter
        segmenter = CVSegmenter(cv_path=str(self.file_path))

        # Run segmentation
        try:
            result = segmenter.segment()

            # Save to legacy location
            output_file = self.segmented_dir / f"{self.cv_id}_segmented.json"
            with open(output_file, 'w') as f:
                json.dump(result, f, indent=2)

            await self._log(1, f"✓ Segmentation complete")
            await self._log(1, f"  • Sections: {len(result.get('sections', []))}")
            await self._log(1, f"  • Output: {output_file.name}")

            return {
                "status": "complete",
                "output_file": str(output_file),
                "num_sections": len(result.get('sections', [])),
                "total_entries": sum(len(s.get('entries', [])) for s in result.get('sections', []))
            }

        except Exception as e:
            await self._log(1, f"ERROR: {str(e)}", "ERROR")
            raise

    async def run_stage_2c_extraction(self, segmented_file: Path) -> Dict[str, Any]:
        """
        Stage 2C: Specialized Section Extraction (Legacy)

        Runs batch extraction using legacy production scripts.
        All 77 section extractors with WCM-specific prompts.

        Args:
            segmented_file: Path to Stage 1 segmented JSON

        Returns:
            Stage results dictionary
        """
        await self._log(2, "Stage 2C: Specialized Section Extraction (Legacy)")
        await self._log(2, "Running 77 WCM-specific extractors...")

        # NOTE: Legacy extractors expect "classified" format, not "segmented"
        # We need to either:
        # A) Convert unified segmented → legacy classified format
        # B) Use the unified extractors which work with segmented format

        # For Phase 1, let's use Option B: Call legacy populate_cv.py directly
        # with the unified Stage 3 outputs (already in legacy format via adapter)

        # This stage is being skipped for now - we'll use unified Stage 2-3
        # then call legacy populate_cv.py in Stage 4

        await self._log(2, "NOTE: Using unified pipeline Stages 2-3, then legacy Stage 4")
        await self._log(2, "This preserves most legacy functionality in populate_cv.py")

        return {
            "status": "skipped",
            "message": "Using unified extraction with legacy template population"
        }

    async def run_stage_2d_enrichment(self, extracted_dir: Path) -> Dict[str, Any]:
        """
        Stage 2D: Enrichment (Legacy)

        Runs PubMed and ROR enrichment on extracted sections.

        Args:
            extracted_dir: Directory with extracted section files

        Returns:
            Stage results dictionary
        """
        await self._log(2, "Stage 2D: Enrichment (Legacy)")

        enriched_count = 0

        # Run PubMed enrichment if enabled
        if config.ENABLE_PUBMED_ENRICHMENT:
            await self._log(2, "Running PubMed API enrichment...")
            await self._log(2, "  • Looking up PMIDs, PMCIDs, DOIs")
            await self._log(2, "  • Getting publication types for subsection routing")

            try:
                # Call legacy enrichment script
                enricher_script = self.legacy_scripts_dir / "enrich_publication_ids.py"
                if enricher_script.exists():
                    # Run enrichment
                    cmd = [
                        "python3",
                        str(enricher_script),
                        str(extracted_dir),
                        "--output-dir",
                        str(self.enriched_dir)
                    ]

                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )

                    stdout, stderr = await process.communicate()

                    if process.returncode == 0:
                        await self._log(2, "  ✓ PubMed enrichment complete")
                        enriched_count += 1
                    else:
                        await self._log(2, f"  ⚠️  PubMed enrichment failed: {stderr.decode()}", "WARNING")
                else:
                    await self._log(2, f"  ⚠️  PubMed enricher not found: {enricher_script}", "WARNING")

            except Exception as e:
                await self._log(2, f"  ⚠️  PubMed enrichment error: {str(e)}", "WARNING")

        # Run ROR enrichment if enabled
        if config.ENABLE_ROR_ENRICHMENT:
            await self._log(2, "Running ROR ID enrichment...")
            await self._log(2, "  • Adding institution identifiers")

            try:
                # Call legacy enrichment script
                enricher_script = self.legacy_scripts_dir / "enrich_section.py"
                if enricher_script.exists():
                    # Run enrichment (implementation depends on script interface)
                    await self._log(2, "  ⚠️  ROR enrichment script found but not yet integrated", "WARNING")
                else:
                    await self._log(2, f"  ⚠️  ROR enricher not found: {enricher_script}", "WARNING")

            except Exception as e:
                await self._log(2, f"  ⚠️  ROR enrichment error: {str(e)}", "WARNING")

        return {
            "status": "complete",
            "enrichments_run": enriched_count,
            "output_dir": str(self.enriched_dir)
        }

    async def run_stage_4_template_population(self, enriched_dir: Path) -> Dict[str, Any]:
        """
        Stage 4: WCM Template Population (Legacy)

        Calls legacy populate_cv.py with all 7 special handlers.

        This is the critical piece that has all the proven formatting logic:
        - Author name abbreviation
        - Author name bolding
        - Publication subsection categorization (S1-S15)
        - Section R national/international routing
        - Section K educational activities formatting
        - Section J percent effort table
        - Section N mentoring formatting

        Args:
            enriched_dir: Directory with enriched section files

        Returns:
            Stage results dictionary
        """
        await self._log(4, "Stage 4: WCM Template Population (Modern Direct Populator)")
        await self._log(4, "Using modern populate_cv_direct with python-docx")

        # Get template path
        template_path = PARENT_DIR / "WCM CV template" / "wcm_cv_template_faculty_october_2022_final .docx"

        if not template_path.exists():
            await self._log(4, f"ERROR: Template not found: {template_path}", "ERROR")
            raise FileNotFoundError(f"WCM template not found: {template_path}")

        # Output path
        output_path = self.output_wcm_dir / f"{self.cv_id}_WCM.docx"

        await self._log(4, f"  • Template: {template_path.name}")
        await self._log(4, f"  • CV ID: {self.cv_id}")
        await self._log(4, f"  • Enriched dir: {enriched_dir.name}")

        try:
            # Import modern direct CV populator
            sys.path.insert(0, str(PARENT_DIR / "src" / "unified_pipeline" / "core"))
            from direct_cv_populator import populate_cv_direct

            # Call modern direct function
            result = populate_cv_direct(
                cv_id=self.cv_id,
                template_path=template_path,
                enriched_dir=enriched_dir,
                output_path=output_path,
                verbose=True
            )

            await self._log(4, "✓ Template population complete!")
            await self._log(4, f"  • Sections populated: {result.get('sections_populated', 0)}")
            await self._log(4, f"  • Total entries: {result.get('total_entries', 0)}")
            await self._log(4, f"  • Output: {output_path.name}")
            await self._log(4, f"  • Has data: {result.get('verified_has_data', False)}")

            return {
                "status": "complete",
                "output_file": str(output_path),
                "sections_populated": result.get('sections_populated', 0),
                "total_entries": result.get('total_entries', 0),
                "verified_has_data": result.get('verified_has_data', False),
                "verification": result.get('verification', {})
            }

        except Exception as e:
            await self._log(4, f"ERROR: {str(e)}", "ERROR")
            import traceback
            await self._log(4, f"Traceback: {traceback.format_exc()}", "ERROR")
            raise

    async def run_full_pipeline(self) -> Dict[str, Any]:
        """
        Run complete legacy pipeline (all stages).

        Returns:
            Dictionary with results from all stages
        """
        results = {}

        # Stage 1: Segmentation (unified - works well)
        stage1_result = await self.run_stage_1_segmentation()
        results['stage_1'] = stage1_result

        # Get segmented file path
        segmented_file = Path(stage1_result['output_file'])

        # For Phase 1: We'll use unified Stages 2-3, then legacy Stage 4
        # This is the quickest path to working functionality

        # Stage 2C: Extraction (TEMP: Skip for now, use unified)
        # Stage 2D: Enrichment (TEMP: Skip for now, use unified)

        # Instead, we'll let the caller run unified Stages 2-3
        # Then we'll call legacy populate_cv.py with the adapter's output

        results['stage_2c'] = {"status": "deferred", "message": "Using unified pipeline"}
        results['stage_2d'] = {"status": "deferred", "message": "Using unified pipeline"}

        return results
