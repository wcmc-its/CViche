#!/usr/bin/env python3
"""
Word Document Verification Tool

Analyzes a WCM CV template to verify what data was actually inserted.
Provides detailed reporting on:
- Which sections have content
- How many table rows were inserted
- Publication formatting quality
- Education and position entries

Usage:
    python verify_word_document.py <path_to_docx>
"""

import sys
from pathlib import Path
from docx import Document
import json


def analyze_table(table, table_num):
    """Analyze a table and return statistics."""
    total_rows = len(table.rows)
    non_empty_rows = 0
    data_rows = []

    for i, row in enumerate(table.rows):
        row_text = ' '.join([cell.text.strip() for cell in row.cells])
        if row_text.strip():
            non_empty_rows += 1
            # Skip header row (usually first row)
            if i > 0 and row_text.strip():
                data_rows.append(row_text[:100])

    return {
        'table_num': table_num,
        'total_rows': total_rows,
        'non_empty_rows': non_empty_rows,
        'data_rows': max(0, non_empty_rows - 1),  # Exclude header
        'sample_data': data_rows[:3]
    }


def find_section_tables(doc):
    """Map sections to their tables based on position in document."""
    section_tables = {}
    current_section = None

    # Build map of paragraph indices
    para_indices = {id(para): i for i, para in enumerate(doc.paragraphs)}
    table_indices = {id(table): i for i, table in enumerate(doc.tables)}

    # Find section headers
    for i, para in enumerate(doc.paragraphs):
        text = para.text.strip()

        # Detect section headers (all caps, length > 3)
        if text.isupper() and len(text) > 3:
            # Check for specific sections
            if 'EDUCATION' in text and 'ACADEMIC DEGREE' not in text:
                current_section = 'EDUCATION'
            elif 'ACADEMIC DEGREE' in text:
                current_section = 'ACADEMIC_DEGREE_B1'
            elif 'ACADEMIC APPOINTMENTS' in text:
                current_section = 'ACADEMIC_APPOINTMENTS_D1'
            elif 'BIBLIOGRAPHY' in text or 'PUBLICATION' in text:
                current_section = 'BIBLIOGRAPHY'

    # Match tables to sections
    # Table 3 is typically Academic Degree (B1)
    # Table 6 is typically Academic Appointments (D1)
    if len(doc.tables) >= 3:
        section_tables['ACADEMIC_DEGREE_B1'] = doc.tables[2]
    if len(doc.tables) >= 6:
        section_tables['ACADEMIC_APPOINTMENTS_D1'] = doc.tables[5]

    return section_tables


def check_publications(doc):
    """Check publication formatting in the document."""
    pub_section_found = False
    pub_entries = []
    issues = []

    for para in doc.paragraphs:
        text = para.text.strip()

        # Look for publication entries (numbered lists in bibliography)
        if text and text[0].isdigit() and '.' in text[:5]:
            pub_entries.append(text)

            # Check for formatting issues
            if '[' in text and ']' in text:
                issues.append(f"Author list formatting issue: {text[:80]}")
            if "'Yaseen" in text or "'et al" in text:
                issues.append(f"Quote marks in authors: {text[:80]}")

    return {
        'total_publications': len(pub_entries),
        'formatting_issues': len(issues),
        'sample_publications': pub_entries[:3],
        'issues': issues[:5]
    }


def verify_document(docx_path):
    """Main verification function."""
    doc = Document(docx_path)

    print("=" * 80)
    print(f"WORD DOCUMENT VERIFICATION: {Path(docx_path).name}")
    print("=" * 80)

    # Check key tables
    section_tables = find_section_tables(doc)

    results = {
        'education': None,
        'positions': None,
        'publications': None,
        'total_tables': len(doc.tables),
        'success': True,
        'errors': []
    }

    # Check Education (B1) - Table 3
    if 'ACADEMIC_DEGREE_B1' in section_tables:
        table_stats = analyze_table(section_tables['ACADEMIC_DEGREE_B1'], 3)
        results['education'] = table_stats

        print(f"\n✓ EDUCATION (Academic Degree) - Table {table_stats['table_num']}")
        print(f"  Data rows inserted: {table_stats['data_rows']}")
        if table_stats['data_rows'] == 0:
            print(f"  ⚠️  WARNING: No education data found!")
            results['errors'].append("Education table is empty")
            results['success'] = False
        else:
            for i, sample in enumerate(table_stats['sample_data'], 1):
                print(f"    {i}. {sample}")
    else:
        print("\n✗ EDUCATION table not found")
        results['errors'].append("Education table not found")
        results['success'] = False

    # Check Positions (D1) - Table 6
    if 'ACADEMIC_APPOINTMENTS_D1' in section_tables:
        table_stats = analyze_table(section_tables['ACADEMIC_APPOINTMENTS_D1'], 6)
        results['positions'] = table_stats

        print(f"\n✓ POSITIONS (Academic Appointments) - Table {table_stats['table_num']}")
        print(f"  Data rows inserted: {table_stats['data_rows']}")
        if table_stats['data_rows'] == 0:
            print(f"  ⚠️  WARNING: No positions data found!")
            results['errors'].append("Positions table is empty")
            results['success'] = False
        else:
            for i, sample in enumerate(table_stats['sample_data'], 1):
                print(f"    {i}. {sample}")
    else:
        print("\n✗ POSITIONS table not found")
        results['errors'].append("Positions table not found")
        results['success'] = False

    # Check Publications
    pub_stats = check_publications(doc)
    results['publications'] = pub_stats

    print(f"\n✓ PUBLICATIONS (Bibliography)")
    print(f"  Total publications found: {pub_stats['total_publications']}")
    print(f"  Formatting issues: {pub_stats['formatting_issues']}")

    if pub_stats['total_publications'] == 0:
        print(f"  ⚠️  WARNING: No publications found!")
        results['errors'].append("No publications found")
        results['success'] = False
    elif pub_stats['formatting_issues'] > 0:
        print(f"  ⚠️  WARNING: Formatting issues detected:")
        for issue in pub_stats['issues']:
            print(f"    • {issue}")
        results['success'] = False
    else:
        print(f"  ✓ Publications properly formatted")
        for i, sample in enumerate(pub_stats['sample_publications'], 1):
            print(f"    {i}. {sample[:100]}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if results['success']:
        print("✅ VERIFICATION PASSED - All data inserted correctly")
    else:
        print("❌ VERIFICATION FAILED")
        print(f"\nErrors found ({len(results['errors'])}):")
        for error in results['errors']:
            print(f"  • {error}")

    print(f"\nStatistics:")
    if results['education']:
        print(f"  Education entries: {results['education']['data_rows']}")
    if results['positions']:
        print(f"  Position entries: {results['positions']['data_rows']}")
    if results['publications']:
        print(f"  Publications: {results['publications']['total_publications']}")

    return results


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python verify_word_document.py <path_to_docx>")
        print("\nExample:")
        print("  python verify_word_document.py web_interface/outputs/YRTHTG/stage_4_wcm_templates/YRTHTG_2079_Zahida_WCM.docx")
        sys.exit(1)

    docx_path = sys.argv[1]

    if not Path(docx_path).exists():
        print(f"Error: File not found: {docx_path}")
        sys.exit(1)

    results = verify_document(docx_path)

    # Exit with error code if verification failed
    sys.exit(0 if results['success'] else 1)
