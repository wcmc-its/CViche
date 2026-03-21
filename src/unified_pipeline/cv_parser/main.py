#!/usr/bin/env python3
"""
CV Parser - Main CLI interface

Extract CV data from PDF and map to institutional Word template
"""
import sys
import argparse
import json
import logging
from pathlib import Path
from typing import Optional

# Handle both module and script execution
try:
    from .pdf_extractor import PDFExtractor
    from .section_classifier import SectionClassifier
    from .data_structurer import DataStructurer
    from .template_mapper import TemplateMapper
except ImportError:
    from pdf_extractor import PDFExtractor
    from section_classifier import SectionClassifier
    from data_structurer import DataStructurer
    from template_mapper import TemplateMapper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_cv(
    pdf_path: str,
    template_path: str,
    output_path: str,
    use_llm: bool = True,
    save_intermediate: bool = False
) -> dict:
    """
    Parse CV from PDF and map to template.

    Args:
        pdf_path: Path to input PDF
        template_path: Path to Word template
        output_path: Path for output Word document
        use_llm: Whether to use LLM for classification/extraction
        save_intermediate: Whether to save intermediate JSON outputs

    Returns:
        Dictionary with processing results and statistics
    """
    results = {
        'success': False,
        'input_file': pdf_path,
        'output_file': output_path,
        'errors': [],
        'warnings': [],
        'stats': {}
    }

    try:
        # Step 1: Extract text from PDF
        logger.info("=" * 60)
        logger.info("STEP 1: Extracting text from PDF")
        logger.info("=" * 60)

        extractor = PDFExtractor(pdf_path)
        extracted_data = extractor.extract()

        results['stats']['pages'] = extracted_data['page_count']
        results['stats']['characters'] = extracted_data['char_count']
        results['stats']['preliminary_sections'] = len(extracted_data['preliminary_sections'])

        logger.info(f"Extracted {extracted_data['page_count']} pages, "
                   f"{extracted_data['char_count']} characters")

        # Step 2: Classify sections
        logger.info("=" * 60)
        logger.info("STEP 2: Classifying CV sections")
        logger.info("=" * 60)

        classifier = SectionClassifier(use_llm=use_llm)
        sections = classifier.parse_sections(
            extracted_data['raw_text'],
            extracted_data['preliminary_sections']
        )

        # Print classification report
        report = classifier.get_section_mapping_report(sections)
        logger.info(f"\n{report}")

        # Merge sections by type
        merged_sections = classifier.merge_sections(sections)
        results['stats']['classified_sections'] = len([s for s in sections if s['classified_as'] != 'UNKNOWN'])
        results['stats']['unknown_sections'] = len([s for s in sections if s['classified_as'] == 'UNKNOWN'])

        if save_intermediate:
            sections_file = Path(output_path).with_suffix('.sections.json')
            with open(sections_file, 'w') as f:
                json.dump(sections, f, indent=2)
            logger.info(f"Saved sections to {sections_file}")

        # Step 3: Extract structured data
        logger.info("=" * 60)
        logger.info("STEP 3: Extracting structured data")
        logger.info("=" * 60)

        structurer = DataStructurer(use_llm=use_llm)
        structured_data = structurer.structure_sections(merged_sections)

        logger.info(f"Structured {len(structured_data)} section types")

        if save_intermediate:
            data_file = Path(output_path).with_suffix('.data.json')
            with open(data_file, 'w') as f:
                json.dump(structured_data, f, indent=2, default=str)
            logger.info(f"Saved structured data to {data_file}")

        # Step 4: Map to template
        logger.info("=" * 60)
        logger.info("STEP 4: Mapping to Word template")
        logger.info("=" * 60)

        mapper = TemplateMapper(template_path)
        mapper.map_data(structured_data)

        # Check for unmapped sections
        unmapped = mapper.get_unmapped_sections(structured_data)
        if unmapped:
            results['warnings'].append(f"Unmapped sections: {', '.join(unmapped)}")
            logger.warning(f"Could not map sections: {', '.join(unmapped)}")

        # Save output
        mapper.save(output_path)

        logger.info("=" * 60)
        logger.info(f"SUCCESS: CV mapped to {output_path}")
        logger.info("=" * 60)

        results['success'] = True

    except Exception as e:
        logger.error(f"Error processing CV: {e}", exc_info=True)
        results['errors'].append(str(e))

    return results


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Parse CV from PDF and map to institutional template',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python -m cv_parser.main input.pdf output.docx

  # With specific template
  python -m cv_parser.main input.pdf output.docx --template my_template.docx

  # Without LLM (rule-based only)
  python -m cv_parser.main input.pdf output.docx --no-llm

  # Save intermediate outputs
  python -m cv_parser.main input.pdf output.docx --save-intermediate
        """
    )

    parser.add_argument(
        'input_pdf',
        help='Input PDF file (CV to parse)'
    )

    parser.add_argument(
        'output_docx',
        help='Output Word document (populated template)'
    )

    parser.add_argument(
        '--template',
        '-t',
        help='Path to Word template (default: wcm_cv_template_faculty_october_2022_final .docx)',
        default='wcm_cv_template_faculty_october_2022_final .docx'
    )

    parser.add_argument(
        '--no-llm',
        action='store_true',
        help='Disable LLM usage (rule-based classification only)'
    )

    parser.add_argument(
        '--save-intermediate',
        '-s',
        action='store_true',
        help='Save intermediate JSON outputs (sections, structured data)'
    )

    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Verbose output (DEBUG level logging)'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate input file
    input_path = Path(args.input_pdf)
    if not input_path.exists():
        logger.error(f"Input file not found: {args.input_pdf}")
        sys.exit(1)

    # Validate template
    template_path = Path(args.template)
    if not template_path.exists():
        logger.error(f"Template file not found: {args.template}")
        sys.exit(1)

    # Run parser
    results = parse_cv(
        pdf_path=str(input_path),
        template_path=str(template_path),
        output_path=args.output_docx,
        use_llm=not args.no_llm,
        save_intermediate=args.save_intermediate
    )

    # Print summary
    print("\n" + "=" * 60)
    print("PROCESSING SUMMARY")
    print("=" * 60)
    print(f"Input:  {results['input_file']}")
    print(f"Output: {results['output_file']}")
    print(f"Status: {'SUCCESS' if results['success'] else 'FAILED'}")
    print()
    print("Statistics:")
    for key, value in results['stats'].items():
        print(f"  {key}: {value}")

    if results['warnings']:
        print("\nWarnings:")
        for warning in results['warnings']:
            print(f"  - {warning}")

    if results['errors']:
        print("\nErrors:")
        for error in results['errors']:
            print(f"  - {error}")

    print("=" * 60)

    sys.exit(0 if results['success'] else 1)


if __name__ == '__main__':
    main()
