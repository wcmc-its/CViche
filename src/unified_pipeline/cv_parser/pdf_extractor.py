"""
PDF extraction module - Extract text and structure from PDF CVs
"""
import pdfplumber
from typing import Dict, List, Tuple
from pathlib import Path
import logging

from .utils import clean_text, is_section_header

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PDFExtractor:
    """Extract text and structural information from PDF CVs."""

    def __init__(self, pdf_path: str):
        """
        Initialize PDF extractor.

        Args:
            pdf_path: Path to PDF file
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def extract(self) -> Dict:
        """
        Extract all content from PDF.

        Returns:
            Dictionary with:
                - raw_text: Full text content
                - pages: List of page texts
                - metadata: PDF metadata
                - sections: Preliminary section detection
        """
        logger.info(f"Extracting content from {self.pdf_path.name}")

        with pdfplumber.open(self.pdf_path) as pdf:
            pages = []
            all_text = []

            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    pages.append({
                        'page_number': i + 1,
                        'text': page_text,
                        'width': page.width,
                        'height': page.height
                    })
                    all_text.append(page_text)

            raw_text = "\n\n".join(all_text)
            metadata = pdf.metadata or {}

            logger.info(f"Extracted {len(pages)} pages, {len(raw_text)} characters")

            # Preliminary section detection
            preliminary_sections = self._detect_sections(raw_text)

            return {
                'raw_text': raw_text,
                'pages': pages,
                'metadata': metadata,
                'preliminary_sections': preliminary_sections,
                'page_count': len(pages),
                'char_count': len(raw_text)
            }

    def _detect_sections(self, text: str) -> List[Dict]:
        """
        Detect potential section boundaries in text.

        Args:
            text: Full text content

        Returns:
            List of detected sections with start positions and headers
        """
        sections = []
        lines = text.split('\n')

        for i, line in enumerate(lines):
            cleaned_line = clean_text(line)
            if cleaned_line and is_section_header(cleaned_line):
                sections.append({
                    'line_number': i,
                    'header': cleaned_line,
                    'header_upper': cleaned_line.upper()
                })

        logger.info(f"Detected {len(sections)} potential section headers")
        return sections

    def extract_with_layout(self) -> Dict:
        """
        Extract text with layout information (coordinates, fonts, etc.).

        Returns:
            Dictionary with detailed layout information
        """
        logger.info(f"Extracting with layout from {self.pdf_path.name}")

        with pdfplumber.open(self.pdf_path) as pdf:
            pages_with_layout = []

            for i, page in enumerate(pdf.pages):
                # Extract words with coordinates
                words = page.extract_words()

                # Extract tables if present
                tables = page.extract_tables()

                pages_with_layout.append({
                    'page_number': i + 1,
                    'words': words,
                    'tables': tables,
                    'width': page.width,
                    'height': page.height
                })

            logger.info(f"Extracted layout information from {len(pages_with_layout)} pages")

            return {
                'pages': pages_with_layout,
                'page_count': len(pages_with_layout)
            }

    def extract_tables(self) -> List[List]:
        """
        Extract all tables from PDF.

        Returns:
            List of tables (each table is a list of rows)
        """
        all_tables = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    all_tables.extend(tables)

        logger.info(f"Extracted {len(all_tables)} tables")
        return all_tables

    def get_text_chunks(self, max_chunk_size: int = 4000) -> List[Tuple[int, str]]:
        """
        Get text in chunks suitable for LLM processing.

        Args:
            max_chunk_size: Maximum characters per chunk

        Returns:
            List of (page_number, text_chunk) tuples
        """
        chunks = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    # If page is larger than max chunk, split it
                    if len(page_text) > max_chunk_size:
                        # Split by paragraphs
                        paragraphs = page_text.split('\n\n')
                        current_chunk = ""

                        for para in paragraphs:
                            if len(current_chunk) + len(para) + 2 <= max_chunk_size:
                                current_chunk += para + "\n\n"
                            else:
                                if current_chunk:
                                    chunks.append((i + 1, current_chunk.strip()))
                                current_chunk = para + "\n\n"

                        if current_chunk:
                            chunks.append((i + 1, current_chunk.strip()))
                    else:
                        chunks.append((i + 1, page_text))

        logger.info(f"Created {len(chunks)} text chunks")
        return chunks


def extract_pdf_text(pdf_path: str) -> str:
    """
    Simple function to extract all text from a PDF.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Extracted text
    """
    extractor = PDFExtractor(pdf_path)
    result = extractor.extract()
    return result['raw_text']


def extract_pdf_data(pdf_path: str) -> Dict:
    """
    Extract comprehensive data from PDF.

    Args:
        pdf_path: Path to PDF file

    Returns:
        Dictionary with all extracted data
    """
    extractor = PDFExtractor(pdf_path)
    return extractor.extract()
