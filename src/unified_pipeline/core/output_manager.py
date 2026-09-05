"""
Output Manager for CV Processing Pipeline

Provides centralized management of output paths and file naming across all pipeline stages.
Ensures consistent naming based on file handles and organized stage-based directory structure.

Usage:
    from core.output_manager import OutputManager

    om = OutputManager("2071_Zuschlag_Cv.docx")

    # Get output paths for each stage
    stage1a_path = om.get_stage1a_json_path()  # stage_1a_segmentation/2071_Zuschlag_Cv_segmented.json
    stage1b_path = om.get_stage1b_path()        # stage_1b_hierarchy_mapping/2071_Zuschlag_Cv_hierarchy_mapped.json
    stage2_path = om.get_stage2_path()          # stage_2_entry_extraction/2071_Zuschlag_Cv_entries.json
    stage3_path = om.get_stage3_path()          # stage_3_taxonomy_mapping/2071_Zuschlag_Cv_mapped.json
"""

import re
from pathlib import Path


class OutputManager:
    """
    Manages output file paths and naming conventions for CV processing pipeline.

    Ensures all stages use consistent file handles and organized directory structure.
    """

    def __init__(self, input_path: str, base_output_dir: Path | None = None):
        """
        Initialize output manager with input CV file.

        Args:
            input_path: Path to input CV file or file handle string
            base_output_dir: Optional base directory for outputs (defaults to config OUTPUT_BASE)
        """
        self.input_path = Path(input_path) if not isinstance(input_path, Path) else input_path

        # Extract file handle (e.g., "2071_Zuschlag_Cv" from various input formats)
        self.file_handle = self._extract_file_handle()

        # Set base output directory
        if base_output_dir:
            self.base_dir = Path(base_output_dir)
        else:
            # Import here to avoid circular dependency
            try:
                from ..config import OUTPUT_BASE
                self.base_dir = OUTPUT_BASE
            except ImportError:
                # Fallback for when module is used standalone
                # __file__ is in src/unified_pipeline/core/output_manager.py
                # parent is core/, parent.parent is unified_pipeline/, parent.parent.parent is src/
                project_root = Path(__file__).parent.parent.parent.parent
                self.base_dir = project_root / "src" / "unified_pipeline" / "outputs"

        # Ensure base directory exists
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Define active stage directories (these get created)
        self._active_stage_dirs = {
            "stage_1a_segmentation": self.base_dir / "stage_1a_segmentation",
            "stage_1b_hierarchy_mapping": self.base_dir / "stage_1b_hierarchy_mapping",
            "stage_2_entry_extraction": self.base_dir / "stage_2_entry_extraction",
            "stage_3_taxonomy_mapping": self.base_dir / "stage_3_taxonomy_mapping",
            "archive": self.base_dir / "archive"
        }

        # Legacy directory mappings (for backward compatibility lookups only, NOT created)
        self._legacy_stage_dirs = {
            "stage_1_segmentation": self.base_dir / "stage_1a_segmentation",  # Redirect to 1a
            "stage_2a_entry_delimitation": self.base_dir / "stage_2_entry_extraction",  # Redirect to 2
            "stage_2b_extract_entries_from_delimiters": self.base_dir / "stage_2_entry_extraction",  # Redirect to 2
        }

        # Combined for lookups
        self.stage_dirs = {**self._active_stage_dirs, **self._legacy_stage_dirs}

        # Only create active stage directories
        for stage_dir in self._active_stage_dirs.values():
            stage_dir.mkdir(parents=True, exist_ok=True)

    def _extract_file_handle(self) -> str:
        """
        Extract clean file handle from input path.

        Handles various input formats:
        - "2071_Zuschlag_Cv.docx" → "2071_Zuschlag_Cv"
        - "2071_Zuschlag_Cv_signature_segmented.json" → "2071_Zuschlag_Cv"
        - "2071_Zuschlag_Cv_segmented.json" → "2071_Zuschlag_Cv"
        - "/path/to/2071_Zuschlag_Cv_mapped.json" → "2071_Zuschlag_Cv"

        Returns:
            Clean file handle without extensions or processing suffixes
        """
        # Get filename without path
        filename = self.input_path.name if self.input_path.suffix else str(self.input_path)

        # Remove file extension
        stem = Path(filename).stem

        # Remove common processing suffixes
        suffixes_to_remove = [
            "_signature_segmented",
            "_segmented",
            "_mapped_v2",
            "_mapped_gpt51_corrected",
            "_mapped_gpt51_hybrid",
            "_mapped_gpt51",
            "_mapped",
            "_delimiters",
            "_entries",
            "_parsed",
            "_classified",
            "_three_pass"
        ]

        for suffix in suffixes_to_remove:
            if stem.endswith(suffix):
                stem = stem[:-len(suffix)]
                break

        return stem

    # Stage 1a: Segmentation
    def get_stage1a_json_path(self) -> Path:
        """Get output path for Stage 1a segmentation JSON file."""
        return self.stage_dirs["stage_1a_segmentation"] / f"{self.file_handle}_segmented.json"

    def get_stage1a_txt_path(self) -> Path:
        """Get output path for Stage 1a segmentation TXT file (human-readable)."""
        return self.stage_dirs["stage_1a_segmentation"] / f"{self.file_handle}_segmented.txt"

    # Legacy Stage 1 methods (deprecated, use get_stage1a_*)
    def get_stage1_json_path(self) -> Path:
        """Get output path for Stage 1 segmentation JSON file. DEPRECATED: Use get_stage1a_json_path()."""
        return self.get_stage1a_json_path()

    def get_stage1_txt_path(self) -> Path:
        """Get output path for Stage 1 segmentation TXT file. DEPRECATED: Use get_stage1a_txt_path()."""
        return self.get_stage1a_txt_path()

    # Stage 1b: Hierarchy Mapping
    def get_stage1b_path(self) -> Path:
        """Get output path for Stage 1b hierarchy mapping."""
        return self.stage_dirs["stage_1b_hierarchy_mapping"] / f"{self.file_handle}_hierarchy_mapped.json"

    # Stage 2: Entry Extraction (combined 2a + 2b)
    def get_stage2_path(self) -> Path:
        """Get output path for Stage 2 entry extraction."""
        return self.stage_dirs["stage_2_entry_extraction"] / f"{self.file_handle}_entries.json"

    # Legacy Stage 2a: Entry Delimitation (deprecated, use get_stage2_path)
    def get_stage2a_path(self) -> Path:
        """Get output path for Stage 2a delimiter detection. DEPRECATED: Use get_stage2_path()."""
        return self.stage_dirs["stage_2a_entry_delimitation"] / f"{self.file_handle}_delimiters.json"

    # Legacy Stage 2b: Entry Extraction (deprecated, use get_stage2_path)
    def get_stage2b_path(self) -> Path:
        """Get output path for Stage 2b entry extraction. DEPRECATED: Use get_stage2_path()."""
        return self.stage_dirs["stage_2b_extract_entries_from_delimiters"] / f"{self.file_handle}_entries.json"

    # Stage 3: Taxonomy Mapping
    def get_stage3_path(self) -> Path:
        """Get output path for Stage 3 taxonomy mapping."""
        return self.stage_dirs["stage_3_taxonomy_mapping"] / f"{self.file_handle}_mapped.json"

    # Utility methods
    def get_stage_dir(self, stage_name: str) -> Path:
        """
        Get directory path for a specific stage.

        Args:
            stage_name: One of: "stage_1_segmentation", "stage_2a_entry_delimitation",
                       "stage_2b_extract_entries_from_delimiters", "stage_3_taxonomy_mapping"

        Returns:
            Path to stage directory

        Raises:
            ValueError: If stage_name is not recognized
        """
        if stage_name not in self.stage_dirs:
            raise ValueError(f"Unknown stage: {stage_name}. Valid stages: {list(self.stage_dirs.keys())}")
        return self.stage_dirs[stage_name]

    def validate_stage_output(self, stage_name: str) -> bool:
        """
        Check if output file exists for a given stage.

        Args:
            stage_name: Stage identifier (e.g., "stage_1a_segmentation")

        Returns:
            True if stage output file exists, False otherwise
        """
        stage_to_method = {
            "stage_1a_segmentation": self.get_stage1a_json_path,
            "stage_1b_hierarchy_mapping": self.get_stage1b_path,
            "stage_2_entry_extraction": self.get_stage2_path,
            "stage_3_taxonomy_mapping": self.get_stage3_path,
            # Legacy support
            "stage_1_segmentation": self.get_stage1_json_path,
            "stage_2a_entry_delimitation": self.get_stage2a_path,
            "stage_2b_extract_entries_from_delimiters": self.get_stage2b_path,
        }

        if stage_name not in stage_to_method:
            return False

        output_path = stage_to_method[stage_name]()
        return output_path.exists()

    def get_all_stage_paths(self) -> dict[str, Path]:
        """
        Get dictionary of all stage output paths.

        Returns:
            Dictionary mapping stage names to their primary output file paths
        """
        return {
            "stage_1a_json": self.get_stage1a_json_path(),
            "stage_1a_txt": self.get_stage1a_txt_path(),
            "stage_1b": self.get_stage1b_path(),
            "stage_2": self.get_stage2_path(),
            "stage_3": self.get_stage3_path()
        }

    def archive_existing_outputs(self):
        """
        Move any existing outputs for this file handle to archive directory.

        Useful for preserving old outputs before reprocessing.
        """
        import shutil
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_subdir = self.stage_dirs["archive"] / f"{self.file_handle}_{timestamp}"
        archive_subdir.mkdir(parents=True, exist_ok=True)

        archived_files = []

        for stage_name, output_path in self.get_all_stage_paths().items():
            if output_path.exists():
                archive_path = archive_subdir / output_path.name
                shutil.copy2(output_path, archive_path)
                archived_files.append(str(output_path))

        return archived_files

    @staticmethod
    def extract_file_handle_from_path(file_path: str) -> str:
        """
        Static method to extract file handle from any path without creating OutputManager instance.

        Args:
            file_path: Path to any CV-related file

        Returns:
            Clean file handle
        """
        temp_om = OutputManager(file_path)
        return temp_om.file_handle


# Convenience functions for backward compatibility
def get_output_path(input_path: str, stage: str, base_dir: Path | None = None) -> Path:
    """
    Get output path for a specific stage given an input file.

    Args:
        input_path: Path to input CV file or file handle
        stage: Stage identifier (e.g., "stage_1a_segmentation", "stage_2", etc.)
        base_dir: Optional base output directory

    Returns:
        Path to output file for the specified stage
    """
    om = OutputManager(input_path, base_dir)

    stage_map = {
        "stage_1a": om.get_stage1a_json_path(),
        "stage_1a_json": om.get_stage1a_json_path(),
        "stage_1a_txt": om.get_stage1a_txt_path(),
        "stage_1b": om.get_stage1b_path(),
        "stage_2": om.get_stage2_path(),
        "stage_3": om.get_stage3_path(),
        # Legacy support
        "stage_1": om.get_stage1_json_path(),
        "stage_1_json": om.get_stage1_json_path(),
        "stage_1_txt": om.get_stage1_txt_path(),
        "stage_2a": om.get_stage2a_path(),
        "stage_2b": om.get_stage2b_path(),
    }

    # Also support full stage names
    if stage in om.stage_dirs:
        # Return the JSON path for this stage
        if "stage_1a" in stage:
            return om.get_stage1a_json_path()
        elif "stage_1b" in stage:
            return om.get_stage1b_path()
        elif "stage_1" in stage:
            return om.get_stage1_json_path()
        elif "stage_2_entry" in stage:
            return om.get_stage2_path()
        elif "stage_2a" in stage:
            return om.get_stage2a_path()
        elif "stage_2b" in stage:
            return om.get_stage2b_path()
        elif "stage_3" in stage:
            return om.get_stage3_path()

    if stage not in stage_map:
        raise ValueError(f"Unknown stage: {stage}")

    return stage_map[stage]
