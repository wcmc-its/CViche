#!/usr/bin/env python3
"""Convert PDF CVs to docx and print one TSV line per file (#806).

    PYTHONPATH=src python3 scripts/pdf_to_docx.py <in_dir_or_file> <out_dir>

Converts each PDF (a single file, or every *.pdf directly inside a directory)
to <out_dir>/<stem>.docx via unified_pipeline.core.pdf_to_docx.

Interface contract:

  stdout  one machine-readable TSV line per input file -- the program's
          output, not a log:
            stem<TAB>pages<TAB>paragraphs<TAB>blank_paragraphs<TAB>image_only_pages<TAB>chars<TAB>status
          image_only_pages is a comma-separated list of 1-based page numbers
          (empty if none). status is `ok` or `error`; an error row has empty
          count columns.
  stderr  diagnostics only (one line per failed file, with the reason)
  exit 1  at least one file failed; a failing file never stops the rest
"""
import argparse
import logging
import sys
from pathlib import Path

from unified_pipeline.core.pdf_to_docx import convert_pdf_to_docx

logger = logging.getLogger(__name__)


def _inputs(source: Path) -> list[Path]:
    if source.is_file():
        return [source]
    return sorted(p for p in source.iterdir() if p.suffix.lower() == ".pdf")


def _ok_row(stem: str, report) -> str:
    pages = ",".join(str(n) for n in report.image_only_pages)
    return "\t".join(map(str, (stem, report.pages, report.paragraphs,
                               report.blank_paragraphs, pages, report.chars, "ok")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="a PDF file, or a directory of PDFs")
    ap.add_argument("out_dir", help="directory for the converted .docx files")
    args = ap.parse_args(argv)

    source, out_dir = Path(args.source), Path(args.out_dir)
    if not source.exists():
        ap.error(f"source does not exist: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)

    failed = 0
    for pdf in _inputs(source):
        try:
            report = convert_pdf_to_docx(pdf, out_dir / f"{pdf.stem}.docx")
        except Exception as exc:
            # The batch contract: one bad PDF (encrypted, corrupt) is an
            # 'error' row and a non-zero exit, never a stop for the rest.
            logger.exception("conversion failed: %s (%s: %s)", pdf.name,
                             type(exc).__name__, exc)
            failed += 1
            # stdout is the TSV contract (see the docstring): print(), not the logger.
            print(f"{pdf.stem}\t\t\t\t\t\terror")
            continue
        print(_ok_row(pdf.stem, report))
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(message)s")
    sys.exit(main())
