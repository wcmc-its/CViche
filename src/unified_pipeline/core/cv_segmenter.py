"""
Unified CV Segmentation Interface

Auto-detects file type and routes to appropriate segmenter:
- .pdf → three_pass_vision_segmentation.py (vision-based)
- .docx → word_cv_segmentation.py (text-based with Structured Outputs)

Both approaches output identical schema with metadata:
- entry_type: Classification for section parser routing
- order_index: Deterministic ordering
- confidence: Quality assurance score
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

# Import segmentation functions
from ..segmentation.pdf_vision import segment_cv_three_pass
from ..segmentation.word_chunked import segment_word_cv_chunked


class CVSegmenter:
    """
    Unified interface for CV segmentation.

    Handles both PDF and Word documents with consistent output format.
    """

    SUPPORTED_FORMATS = {'.pdf', '.docx'}

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize segmenter.

        Args:
            api_key: OpenAI API key (defaults to env var)
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY_WORK")
        if not self.api_key:
            raise ValueError("OpenAI API key not found. Set OPENAI_API_KEY environment variable")

    def detect_format(self, file_path: str) -> str:
        """
        Detect file format from extension.

        Args:
            file_path: Path to CV file

        Returns:
            File extension (e.g., '.pdf', '.docx')

        Raises:
            ValueError: If format not supported
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format: {ext}. "
                f"Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
            )

        return ext

    def segment(self, file_path: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Segment CV into hierarchical sections and entries.

        Auto-detects format and routes to appropriate segmenter:
        - PDF: Three-pass vision approach (gestalt → triage → extraction)
        - Word: Text-based with Structured Outputs (exploits native structure)

        Args:
            file_path: Path to CV file (.pdf or .docx)
            output_dir: Output directory (defaults to script directory)

        Returns:
            Dictionary with:
            - num_sections: Number of top-level sections
            - total_entries: Total entries extracted
            - output_file: Path to JSON output
            - format: Input file format
            - approach: Segmentation approach used

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If format not supported
        """
        # Validate file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"CV file not found: {file_path}")

        # Detect format
        file_format = self.detect_format(file_path)

        print("="*80)
        print("CV SEGMENTER - UNIFIED INTERFACE")
        print("="*80)
        print(f"Input: {file_path}")
        print(f"Format: {file_format}")

        # Route to appropriate segmenter
        if file_format == '.pdf':
            print("Approach: Three-pass vision segmentation")
            print("  Pass 1: GESTALT (map all sections)")
            print("  Pass 2: TRIAGE (count items, decide chunking)")
            print("  Pass 3: EXTRACTION (get entries, chunked if needed)")
            print()

            result = segment_cv_three_pass(file_path, output_dir)
            result['format'] = 'pdf'
            result['approach'] = 'three-pass-vision'

        elif file_format == '.docx':
            print("Approach: Word native structure + text model")
            print("  - Exploits Word styles, lists, tables")
            print("  - Uses GPT-4o with Structured Outputs")
            print("  - 6-12x faster and cheaper than PDF")
            print()

            result = segment_word_cv_chunked(file_path, output_dir)
            result['format'] = 'docx'
            result['approach'] = 'word-chunked-hierarchical'

        else:
            # Should never reach here due to detect_format validation
            raise ValueError(f"Unsupported format: {file_format}")

        print()
        print("="*80)
        print("SEGMENTATION COMPLETE")
        print("="*80)
        print(f"Format: {result['format']}")
        print(f"Approach: {result['approach']}")
        print(f"Sections: {result['num_sections']}")
        print(f"Entries: {result['total_entries']}")
        print(f"Output: {result['output_file']}")
        print()

        return result


def main():
    """
    Command-line interface for CV segmentation.

    Usage:
        python cv_segmenter.py <file_path> [output_dir]

    Examples:
        python cv_segmenter.py cv.pdf
        python cv_segmenter.py cv.docx ./output
    """
    if len(sys.argv) < 2:
        print("CV Segmenter - Unified Interface")
        print()
        print("Usage: python cv_segmenter.py <file_path> [output_dir]")
        print()
        print("Supported formats:")
        print("  - .pdf  → Three-pass vision segmentation")
        print("  - .docx → Word native structure + text model")
        print()
        print("Examples:")
        print("  python cv_segmenter.py cv.pdf")
        print("  python cv_segmenter.py cv.docx ./output")
        print()
        sys.exit(1)

    file_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        segmenter = CVSegmenter()
        result = segmenter.segment(file_path, output_dir)

        # Success
        sys.exit(0)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
