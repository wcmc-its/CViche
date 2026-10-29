"""
Output Manager for CV Processing Pipeline

Provides the stage 1a / 1b / 2 output paths for one CV, named by its file
handle. Callers: `segmentation/signature_based_segmentation.py` (stage 1a),
`stage_1b_hierarchy_mapper.py` and `stage_2_entry_extraction.py`; both
drivers then look for those files by `document_uid` (`run_full_pipeline.py`
`get_output_paths`, `orchestrator.py` `_get_output_paths`), which each
derives as `Path(cv_path).stem`. So the file handle is that stem, unchanged.

Usage:
    from core.output_manager import OutputManager

    om = OutputManager("9999_Doe_Jane_CV.docx")

    stage1a_path = om.get_stage1a_json_path()  # stage_1a_segmentation/9999_Doe_Jane_CV_segmented.json
    stage1b_path = om.get_stage1b_path()        # stage_1b_hierarchy_mapping/9999_Doe_Jane_CV_hierarchy_mapped.json
    stage2_path = om.get_stage2_path()          # stage_2_entry_extraction/9999_Doe_Jane_CV_entries.json
"""

from pathlib import Path

#: Stage output directories, created on construction: every caller writes
#: into one of them right after building its OutputManager.
STAGE_1A_DIRNAME = "stage_1a_segmentation"
STAGE_1B_DIRNAME = "stage_1b_hierarchy_mapping"
STAGE_2_DIRNAME = "stage_2_entry_extraction"


class OutputManager:
    """
    Output file paths for one CV's stage 1a / 1b / 2 artifacts.
    """

    def __init__(self, input_path: str | Path, base_output_dir: Path | None = None) -> None:
        """
        Initialize output manager with input CV file.

        Args:
            input_path: Path to the input CV file (or a bare document uid)
            base_output_dir: Optional base directory for outputs (defaults to config OUTPUT_BASE)

        Raises:
            ValueError: If `input_path` names no file (an empty handle)
        """
        self.input_path = Path(input_path)
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

        self.stage_dirs = {
            name: self.base_dir / name
            for name in (STAGE_1A_DIRNAME, STAGE_1B_DIRNAME, STAGE_2_DIRNAME)
        }
        for stage_dir in self.stage_dirs.values():
            stage_dir.mkdir(parents=True, exist_ok=True)

    def _extract_file_handle(self) -> str:
        """
        The input's file stem, exactly as both drivers derive `document_uid`
        (`Path(cv_path).stem`): "9999_Doe_Jane_CV.docx" -> "9999_Doe_Jane_CV".

        No processing suffix is stripped. Stripping one made "X_entries.docx"
        and "X.docx" share the handle "X" -- one CV's stage outputs overwrote
        the other's -- and wrote X_entries.docx's stage 1b/2 files where the
        drivers, keyed by "X_entries", never look (#1910).

        Raises:
            ValueError: If the stem is empty
        """
        stem = self.input_path.stem
        if not stem:
            raise ValueError(f"No file handle in input path {str(self.input_path)!r}")
        return stem

    # Stage 1a: Segmentation
    def get_stage1a_json_path(self) -> Path:
        """Get output path for Stage 1a segmentation JSON file."""
        return self.stage_dirs[STAGE_1A_DIRNAME] / f"{self.file_handle}_segmented.json"

    def get_stage1a_txt_path(self) -> Path:
        """Get output path for Stage 1a segmentation TXT file (human-readable)."""
        return self.stage_dirs[STAGE_1A_DIRNAME] / f"{self.file_handle}_segmented.txt"

    # Legacy Stage 1 names (stage 1a and stage 1b's callers still use them)
    def get_stage1_json_path(self) -> Path:
        """Get output path for Stage 1 segmentation JSON file. DEPRECATED: Use get_stage1a_json_path()."""
        return self.get_stage1a_json_path()

    def get_stage1_txt_path(self) -> Path:
        """Get output path for Stage 1 segmentation TXT file. DEPRECATED: Use get_stage1a_txt_path()."""
        return self.get_stage1a_txt_path()

    # Stage 1b: Hierarchy Mapping
    def get_stage1b_path(self) -> Path:
        """Get output path for Stage 1b hierarchy mapping."""
        return self.stage_dirs[STAGE_1B_DIRNAME] / f"{self.file_handle}_hierarchy_mapped.json"

    # Stage 2: Entry Extraction (combined 2a + 2b)
    def get_stage2_path(self) -> Path:
        """Get output path for Stage 2 entry extraction."""
        return self.stage_dirs[STAGE_2_DIRNAME] / f"{self.file_handle}_entries.json"
