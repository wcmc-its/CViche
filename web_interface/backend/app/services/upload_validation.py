"""The pure upload validators: magic bytes, the zip-bomb bound, text extraction
(python-docx, and the PDF sandbox for PDFs) and the minimum-text rule.

Shared by /upload (app/api/upload.py re-imports every name) and the emailed-CV
intake (#1298). Lives in services/ so the worker can use it without importing
app.api or app.auth (see run_service.UPLOAD_DIR).
"""
import io
import logging
import os
import re
import tempfile
import zipfile
import zlib
from enum import StrEnum
from urllib.parse import unquote

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from lxml import etree
from lxml.etree import XMLSyntaxError

from app.services.pdf_sandbox import PdfText, extract_pdf_text

logger = logging.getLogger(__name__)
ZIP_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF-"
PDF_EXTENSION = ".pdf"

# A PDF whose image-only (scanned) pages reach this share of its pages is
# refused at upload (#1282): most of its content would be missing from the
# output, at full cost. Below it, the run goes ahead and /estimate names the
# pages so the user decides before submitting.
SCANNED_PAGE_REJECT_SHARE = 0.5

# Minimum extracted text (characters) for a document to be considered readable.
# A real CV runs into the thousands of characters; anything below this is almost
# certainly a scanned image, a password-protected file, or effectively blank.
MIN_EXTRACTED_CHARS = 500

# Expansion bound checked before python-docx parses (#793). zipfile stops
# inflating each entry at its declared file_size, so capping the declared
# totals caps what a parse can expand to. The 392 local corpus CVs top out at
# 37 entries and 7.2 MB uncompressed; a zip bomb declares gigabytes.
_DOCX_MAX_ENTRIES = 1000
_DOCX_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


def _validate_pdf_magic(content: bytes) -> bool:
    """Check if content starts with the PDF header."""
    return content[:len(PDF_MAGIC)] == PDF_MAGIC


def _validate_docx_magic(content: bytes) -> bool:
    """Check if content is a ZIP archive containing Word document structure,
    within the entry-count and uncompressed-size bounds above."""
    if content[:4] != ZIP_MAGIC:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            entries = zf.infolist()
            if (len(entries) > _DOCX_MAX_ENTRIES
                    or sum(e.file_size for e in entries) > _DOCX_MAX_UNCOMPRESSED_BYTES):
                logger.warning("Rejected docx: %d entries, %d bytes uncompressed",
                               len(entries), sum(e.file_size for e in entries))
                return False
            return "word/document.xml" in zf.namelist()
    except (zipfile.BadZipFile, Exception):
        return False


# Active content refused at upload (#1334): parts of a .docx that act when
# someone opens the retained original (#1333). On the 1,169 distinct local
# corpus CVs none of the three rules below refuses a file. Left alone on
# purpose: ActiveX parts and embedded OLE objects (real CVs carry them, and the
# S3 malware scan in #1333 covers binaries), hyperlinks, and external targets
# that are local paths (template leftovers: file:///, file://localhost,
# "Macintosh HD:" paths).
ACTIVE_CONTENT_MESSAGE = (
    "This Word file contains macros, links that load content from another "
    "server, or DDE fields, which CViche doesn't accept for security reasons. "
    "Save the CV as a PDF, or re-save it in Word without them, and upload again."
)


class ActiveContent(StrEnum):
    MACRO = "macro"
    EXTERNAL_LINK = "external-link"
    DDE = "dde"


_VBA_PROJECT_BASENAME = "vbaproject.bin"
_RELS_SUFFIX = ".rels"
_WORD_PART_PREFIX = "word/"
_XML_SUFFIX = ".xml"
_EXTERNAL_TARGET_MODE = "external"
_HYPERLINK_TYPE_SUFFIX = "/hyperlink"
_NETWORK_SCHEMES = frozenset({"http", "https", "ftp"})
_FILE_SCHEME = "file"
_LOCAL_FILE_HOSTS = frozenset({"", "localhost"})
_DDE_FIELD_CODES = frozenset({"DDE", "DDEAUTO"})
_URI_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
# \\host\share, //host/share, and the mixed-slash spellings Windows also reads as UNC.
_UNC_PREFIX = re.compile(r"[\\/]{2}")
_FIELD_CODE = re.compile(r"\s*([A-Za-z]+)")
# What reading one part can raise on a zip whose directory _validate_docx_magic
# accepted: a CRC mismatch, a corrupt deflate stream, malformed XML.
_PART_READ_ERRORS = (zipfile.BadZipFile, zlib.error, XMLSyntaxError)


def docx_active_content(content: bytes) -> ActiveContent | None:
    """The first kind of active content in a .docx that has passed
    ``_validate_docx_magic``, or None.

    Refuses on positive evidence only. An unreadable zip or part is skipped
    and logged, and the rest of the file is still scanned: malformed input is
    left to the python-docx read that follows (``_DOCX_READ_ERRORS``), which
    is how this module already treats it."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            found = _scan_docx(zf)
    except zipfile.BadZipFile:
        logger.warning("Active-content scan skipped: not a readable zip", exc_info=True)
        return None
    if found is not None:
        logger.warning("[SECURITY] Refused docx carrying active content: %s", found)
    return found


def _scan_docx(zf: zipfile.ZipFile) -> ActiveContent | None:
    entries = zf.infolist()
    if any(_basename(e.filename) == _VBA_PROJECT_BASENAME for e in entries):
        return ActiveContent.MACRO
    for entry in entries:
        name = entry.filename.lower()
        if name.endswith(_RELS_SUFFIX) and _has_network_relationship(_parse_part(zf, entry)):
            return ActiveContent.EXTERNAL_LINK
    for entry in entries:
        name = entry.filename.lower()
        if name.startswith(_WORD_PART_PREFIX) and name.endswith(_XML_SUFFIX) \
                and _has_dde_field(_parse_part(zf, entry)):
            return ActiveContent.DDE
    return None


def _basename(zip_name: str) -> str:
    return zip_name.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _parse_part(zf: zipfile.ZipFile, entry: zipfile.ZipInfo) -> etree._Element | None:
    """One part's XML, or None (logged) when it can't be read. No entity
    resolution, no network, no huge_tree. A parser per call: lxml parsers are
    not shared across the threadpool's threads."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        return etree.fromstring(zf.read(entry), parser)
    except _PART_READ_ERRORS:
        logger.warning("Active-content scan skipped an unreadable part", exc_info=True)
        return None


def _has_network_relationship(root: etree._Element | None) -> bool:
    """An external, non-hyperlink relationship whose target is on another host
    (a remote attachedTemplate, say). Local paths and hyperlinks pass."""
    if root is None:
        return False
    for rel in root.iter("{*}Relationship"):
        if ((rel.get("TargetMode") or "").lower() == _EXTERNAL_TARGET_MODE
                and not (rel.get("Type") or "").endswith(_HYPERLINK_TYPE_SUFFIX)
                and _is_network_target(rel.get("Target") or "")):
            return True
    return False


def _is_network_target(target: str) -> bool:
    """http(s)/ftp, a UNC path, or a file URL naming a host other than
    localhost (file://host/...)."""
    target = unquote(target).strip()
    if _UNC_PREFIX.match(target):
        return True
    scheme = _URI_SCHEME.match(target)
    if scheme is None:
        return False
    name = scheme.group(1).lower()
    if name in _NETWORK_SCHEMES:
        return True
    return name == _FILE_SCHEME and _is_remote_file_path(target[scheme.end():])


def _is_remote_file_path(rest: str) -> bool:
    """What follows "file:": remote when its authority names a host.

    ponytail: file:////x/... (empty authority, path starting "//") passes,
    though Windows reads it as UNC. Mac Word writes local templates exactly
    that way (file:////Users/...), and 3 corpus CVs carry one; refusing the
    shape would refuse them. Revisit if #1333's scan or a real upload shows
    one aimed at a server."""
    rest = rest.replace("\\", "/")
    if not rest.startswith("//"):
        return False
    host = rest[2:].partition("/")[0]
    return host.lower() not in _LOCAL_FILE_HOSTS


def _has_dde_field(root: etree._Element | None) -> bool:
    if root is None:
        return False
    for simple in root.iter("{*}fldSimple"):
        if _is_dde_instruction(_attr(simple, "instr")):
            return True
    return _has_dde_complex_field(root)


def _has_dde_complex_field(root: etree._Element) -> bool:
    """A complex field's instruction is its w:instrText runs between the
    "begin" fldChar and its "separate" (or "end"); fields nest, so each open
    field keeps its own runs on a stack (None once past "separate")."""
    open_fields: list[list[str] | None] = []
    for el in root.iter("{*}fldChar", "{*}instrText"):
        if etree.QName(el).localname == "instrText":
            if open_fields and open_fields[-1] is not None:
                open_fields[-1].append(el.text or "")
            continue
        kind = _attr(el, "fldCharType")
        if kind == "begin":
            open_fields.append([])
        elif kind in ("separate", "end") and open_fields:
            runs = open_fields.pop()
            if runs is not None and _is_dde_instruction("".join(runs)):
                return True
            if kind == "separate":
                open_fields.append(None)
    # An unterminated field still counts.
    return any(runs is not None and _is_dde_instruction("".join(runs)) for runs in open_fields)


def _attr(el: etree._Element, localname: str) -> str:
    """An attribute by local name, whatever its namespace prefix (transitional
    or strict OOXML)."""
    for key, value in el.attrib.items():
        if etree.QName(key).localname == localname:
            return value
    return ""


def _is_dde_instruction(instr: str) -> bool:
    code = _FIELD_CODE.match(instr)
    return code is not None and code.group(1).upper() in _DDE_FIELD_CODES


# What python-docx raises on a zip-shaped upload it cannot read (measured on
# 1.2.0): a zip missing its parts -> KeyError; malformed part XML ->
# XMLSyntaxError; a truncated zip -> BadZipFile; not an OPC package at all ->
# PackageNotFoundError; the tempfile round-trip -> OSError. Anything else is a
# bug and must surface, not be swallowed (§5.4).
_DOCX_READ_ERRORS = (PackageNotFoundError, zipfile.BadZipFile, KeyError, XMLSyntaxError, OSError,
                     zlib.error)


def _extract_text(content: bytes, file_ext: str) -> str | None:
    """Best-effort text extraction for the empty-document guard.

    Returns the extracted text, an empty string when the file is readable but
    contains no text (scan/blank), or ``None`` when the document could not be
    read at all (a known python-docx read failure, see ``_DOCX_READ_ERRORS``).
    Callers treat ``None`` as "cannot determine" and skip the guard rather
    than block a possibly-valid upload.

    A PDF never fails open (#806): the run's conversion uses the same parser
    under the same limits, so a PDF this cannot read, the run cannot either.

    Raises:
        EncryptedPdfError: a password-protected PDF.
        PdfTooComplexError: a PDF over pdf_sandbox's page, memory or time limit.
        UnreadablePdfError: any other PDF parse failure.
        PdfBusyError: every PDF child slot is taken.
    """
    if file_ext == PDF_EXTENSION:
        return extract_pdf_text(content)
    if file_ext != ".docx":
        return None
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        doc = Document(tmp_path)
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)
        return "\n".join(parts)
    except _DOCX_READ_ERRORS as e:
        logger.warning("Text extraction for empty-doc guard failed (%s): %s", file_ext, e, exc_info=True)
        return None
    finally:
        os.unlink(tmp_path)


def is_mostly_scanned(pdf: PdfText) -> bool:
    """True when the PDF's image-only pages are SCANNED_PAGE_REJECT_SHARE or
    more of its pages (#1282); a fully scanned PDF included."""
    return bool(pdf.pages) and len(pdf.image_only_pages) >= SCANNED_PAGE_REJECT_SHARE * pdf.pages
