"""Section A: personal data -- name, addresses, phones, emails (#398).

The shortest section in the template and one of the longest writers, because
almost nothing about a CV's contact block is structured. The work is in order:

1. Resolve the name. `cv_owner` first, then a LinkedIn slug in the A entries,
   then the document uid via `_extract_name_from_uid`. A bare surname does not
   count as complete and keeps the fallbacks running.
2. Classify each A entry into one of six slots by reading the LABEL in the
   source text, not the extracted field -- "Cell phone:" and "Office:" are what
   distinguish two otherwise identical phone numbers, and one entry can carry
   both. An extracted address that names its own halves is routed by
   `_labels_its_own_address_slots` instead, which is #442: a dict naming home
   and office was previously forced whole into whichever slot the raw text
   happened to label.
3. Re-open the ORIGINAL .docx when fields are still missing. Contact data
   frequently lives in a source table ("NAME: | Patricia Opresko") that entry
   extraction never turned into entries, and business-address cells embed
   Phone/Fax/E-mail lines that have to be pulled back out line by line.
4. Fill the template's PERSONAL DATA table, located by its "Work email:" cell
   rather than by index.

This is the only section writer that reads the source document directly, which
is why `Document` and `Path` are imported here and nowhere else in this package.
"""
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

try:
    from docx import Document
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import _set_cell_text, _set_font
from ..normalization import _address_cell_text, _from_pii_fragment, _labels_its_own_address_slots, _labels_its_own_phone_slots, _phone_cell_text, _pii_fragments
from ..parsing import _extract_name_from_uid


class PersonalDataSection:
    """Section A writers, mixed into `WCMTemplateGenerator`."""

    def _fill_personal_data(self, entries: List[Dict], cv_owner: Dict, document_uid: str,
                            all_entries: List[Dict] = None, original_doc_path: str = None):
        """Fill personal data section.

        Args:
            entries: A-coded entries specifically
            cv_owner: CV owner data if available
            document_uid: Document identifier
            all_entries: All entries from the CV (to search for email if not in A entries)
            original_doc_path: Path to original Word document (for fallback email extraction)
        """
        if self.verbose:
            print("\nFilling Personal Data...")

        # Get name from cv_owner if available
        # Priority: full_name_with_credentials > full_name
        # Note: last_name alone is not considered a complete name (will try fallback)
        name = None
        name_is_complete = False  # Track if we have a full name or just last name
        if cv_owner and cv_owner.get('full_name_with_credentials'):
            # Take only first line (may contain newline + date prepared)
            name = cv_owner['full_name_with_credentials'].split('\n')[0].strip()
            if name:
                name_is_complete = True
        elif cv_owner and cv_owner.get('full_name'):
            name = cv_owner['full_name']
            if name:
                name_is_complete = True

        # Try to extract from A entries (LinkedIn URL, etc.)
        if not name:
            for entry in entries:
                text = entry.get('text', '')
                # Look for LinkedIn URL pattern: linkedin.com/in/firstname-lastname
                linkedin_match = re.search(r'linkedin\.com/in/([a-z]+-[a-z]+)', text.lower())
                if linkedin_match:
                    parts = linkedin_match.group(1).split('-')
                    name = ' '.join(p.title() for p in parts)
                    name_is_complete = True
                    break

        # Collect different types of contact info from A entries
        # The original text contains labels like "Office address:", "Cell phone:", etc.
        work_email = None
        personal_email = None
        office_phone = None
        cell_phone = None
        home_phone = None
        office_address = None
        home_address = None

        # A entries that reach none of the six slots below are consumed by
        # nothing. 'A' is in mapped_codes, so they were then excluded from the
        # appendix too, and vanished. Detected by ablation rather than by
        # re-listing the fields this loop reads, so it cannot drift out of step
        # when the loop learns to read a new one.
        unconsumed = []

        for entry in entries:
            fields = entry.get('extracted_fields', {}) or {}
            text = entry.get('text', '').lower()
            slots_before = (work_email, personal_email, office_phone,
                            cell_phone, office_address, home_address)

            # Values stage 4 lifted out of a protected-personal-data fragment
            # are not contact details and must not reach the template. web07's
            # Office address row renders "Cincinnati, Ohio" today, taken
            # straight from "PLACE OF BIRTH: Cincinnati, Ohio" by the address
            # catch-all below.
            pii_fragments = _pii_fragments(entry.get('text', ''))

            # Determine type based on original text labels
            extracted_phone = fields.get('phone')
            extracted_address = fields.get('address')
            extracted_email = (fields.get('email') or
                              fields.get('primary_email') or
                              fields.get('institutional_email') or
                              fields.get('work_email') or
                              fields.get('personal_email'))

            if pii_fragments:
                if _from_pii_fragment(extracted_phone, pii_fragments):
                    extracted_phone = None
                if _from_pii_fragment(extracted_address, pii_fragments):
                    extracted_address = None
                if _from_pii_fragment(extracted_email, pii_fragments):
                    extracted_email = None

            # Classify phone by type
            # Handle case where multiple phones are in one entry (e.g., "Mobile: X  Work: Y")
            if extracted_phone:
                # Check if text contains multiple phone type labels
                has_mobile = 'cell' in text or 'mobile' in text
                has_work = 'office' in text or 'work' in text
                has_home = 'home' in text

                if _labels_its_own_phone_slots(extracted_phone):
                    # A structured phone names its own halves, so trust those
                    # rather than the entry's raw text label (#450). web147's
                    # contact block is labelled "Home" but the dict carries
                    # cell/office/fax; the raw-text routing sent all three to
                    # home_phone, which the WCM template has no row for, so
                    # every number was dropped.
                    cell_phone = cell_phone or _phone_cell_text(extracted_phone, 'cell')
                    office_phone = office_phone or _phone_cell_text(extracted_phone, 'office')
                elif has_mobile and has_work and ';' in str(extracted_phone):
                    # Both types in same entry — try to split them
                    # Parse from original text to get correct assignment
                    phones = [p.strip() for p in str(extracted_phone).split(';')]
                    # Find phone numbers in order they appear in text
                    mobile_match = re.search(r'(?:cell|mobile)[^(]*(\(\d{3}\)\s*\d{3}[-.\s]?\d{4})', text, re.IGNORECASE)
                    work_match = re.search(r'(?:work|office)[^(]*(\(\d{3}\)\s*\d{3}[-.\s]?\d{4})', text, re.IGNORECASE)
                    if mobile_match and not cell_phone:
                        cell_phone = mobile_match.group(1)
                    if work_match and not office_phone:
                        office_phone = work_match.group(1)
                elif has_mobile:
                    if not cell_phone:
                        cell_phone = _phone_cell_text(extracted_phone, 'cell')
                elif has_home:
                    # The WCM template has exactly two phone rows, Office
                    # telephone and Cell phone -- there is no home row, so
                    # home_phone is written and never read, and a home-labelled
                    # number is deliberately not rendered.
                    #
                    # Routing it to the office row instead was tried and is
                    # WRONG: on web113 the HOME entry is processed before the
                    # BUSINESS entry, so the office row took the home number and
                    # the real business number was then skipped as already-set.
                    # Recovering a home phone needs a template row to put it in,
                    # not a slot to squat in.
                    if not home_phone:
                        home_phone = _phone_cell_text(extracted_phone, 'home')
                elif has_work or not office_phone:
                    if not office_phone:
                        office_phone = _phone_cell_text(extracted_phone, 'office')

            # Classify address by type
            if extracted_address:
                if _labels_its_own_address_slots(extracted_address):
                    # The dict names its own halves, so fill both slots from it
                    # instead of forcing the whole thing into whichever one the
                    # raw text happened to label (#442). A dict that names no
                    # slot is one address and falls through to the raw-text
                    # routing below, same as a string.
                    if not home_address:
                        home_address = _address_cell_text(extracted_address, 'home')
                    if not office_address:
                        office_address = _address_cell_text(extracted_address, 'office')
                elif 'home' in text:
                    if not home_address:
                        home_address = _address_cell_text(extracted_address, 'home')
                elif 'office' in text or 'work' in text or 'business' in text or not office_address:
                    if not office_address:
                        office_address = _address_cell_text(extracted_address, 'office')

            # Classify email by type
            if extracted_email:
                if 'personal' in text:
                    if not personal_email:
                        personal_email = extracted_email
                elif not work_email:
                    work_email = extracted_email

            # Also check entry text for email pattern (fallback)
            if not work_email and not personal_email:
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', entry.get('text', ''))
                if email_match and not _from_pii_fragment(email_match.group(0),
                                                          pii_fragments):
                    work_email = email_match.group(0)

            # home_phone is deliberately absent from this tuple: it is written
            # and never read (the template has no home-phone row), so an entry
            # that sets only home_phone reaches the document nowhere and is
            # genuinely unconsumed. Two corpus entries are in exactly that
            # state.
            if (work_email, personal_email, office_phone, cell_phone,
                    office_address, home_address) == slots_before:
                unconsumed.append(entry)

        # Handed to the post-render recovery pass, which is the only point at
        # which "did this content reach the document?" can actually be asked.
        self._unconsumed_personal_data = unconsumed

        # Legacy variable names for compatibility with rest of function
        email = work_email
        phone = office_phone
        address = office_address

        # If still no email, search all entries for email patterns
        if not email and all_entries:
            for entry in all_entries:
                text = entry.get('text', '')
                # Look for email pattern
                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
                if email_match:
                    email = email_match.group(0)
                    break

                # Also check extracted fields
                fields = entry.get('extracted_fields', {}) or {}
                email = fields.get('email') or fields.get('primary_email')
                if email:
                    break

        # Fallback: read personal data from original Word document
        # This handles cases where personal data is in tables (e.g., NAME: | Patricia Opresko)
        if original_doc_path and Path(original_doc_path).exists():
            try:
                original_doc = Document(original_doc_path)

                # First check tables (common format: label in col 0, value in col 1)
                for table in original_doc.tables[:3]:  # Only check first 3 tables
                    for row in table.rows:
                        if len(row.cells) >= 2:
                            label = row.cells[0].text.strip().lower()
                            value = row.cells[1].text.strip()

                            # Extract name if not yet found (or only have last name)
                            if not name_is_complete and 'name' in label and ':' in label:
                                if value and len(value) > 2:
                                    name = value
                                    name_is_complete = True
                                    if self.verbose:
                                        print(f"  Found name from table: {name}")

                            # Extract address if not yet found
                            # Note: Business address cells often contain embedded phone/fax/email
                            if not address and ('address' in label or 'business' in label) and ':' in label:
                                if value and len(value) > 5:
                                    # Parse the address block - it may contain Phone:, Fax:, E-mail: lines
                                    address_lines = []
                                    for line in value.split('\n'):
                                        line = line.strip()
                                        line_lower = line.lower()

                                        # Extract phone if embedded in address
                                        if not phone and ('phone:' in line_lower or 'phone\t' in line_lower):
                                            phone_match = re.search(r'(?:phone[:\s]+)(.+)', line, re.IGNORECASE)
                                            if phone_match:
                                                phone = phone_match.group(1).strip()
                                                if self.verbose:
                                                    print(f"  Found phone from address block: {phone}")
                                            continue

                                        # Extract email if embedded in address
                                        if not email and ('e-mail:' in line_lower or 'email:' in line_lower or 'e-mail\t' in line_lower):
                                            email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', line)
                                            if email_match:
                                                email = email_match.group(0)
                                                if self.verbose:
                                                    print(f"  Found email from address block: {email}")
                                            continue

                                        # Skip fax lines
                                        if 'fax:' in line_lower or 'fax\t' in line_lower:
                                            continue

                                        # Keep other lines as address
                                        if line:
                                            address_lines.append(line)

                                    address = '\n'.join(address_lines)
                                    if self.verbose:
                                        print(f"  Found address from table: {address[:50]}...")

                            # Extract phone if not yet found
                            if not phone and ('phone' in label or 'telephone' in label) and ':' in label:
                                if value and len(value) > 5:
                                    phone = value
                                    if self.verbose:
                                        print(f"  Found phone from table: {phone}")

                            # Extract email if not yet found
                            if not email and 'email' in label:
                                email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', value)
                                if email_match:
                                    email = email_match.group(0)
                                    if self.verbose:
                                        print(f"  Found email from table: {email}")

                # Also check paragraphs for email (if not found in tables)
                if not email:
                    for para in original_doc.paragraphs[:20]:
                        text = para.text.strip()
                        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
                        if email_match:
                            email = email_match.group(0)
                            if self.verbose:
                                print(f"  Found email from paragraph: {email}")
                            break
            except Exception as e:
                if self.verbose:
                    print(f"  Warning: Could not read original document for personal data: {e}")

        # Fallback to document_uid for name
        if not name:
            name = _extract_name_from_uid(document_uid)

        # Find and fill Name field
        name_idx = self._find_paragraph_with_text("Name:")
        if name_idx is not None:
            para = self.doc.paragraphs[name_idx]
            para.clear()
            run = para.add_run(f"Name: {name}")
            _set_font(run, bold=True)

        # Fill Date of preparation with today's date
        date_idx = self._find_paragraph_with_text("Date of preparation")
        if date_idx is not None:
            para = self.doc.paragraphs[date_idx]
            para.clear()
            today = datetime.now().strftime("%B %-d, %Y")  # e.g., "February 1, 2026"
            run = para.add_run(f"Date of preparation: {today}")
            _set_font(run)

        # Fill email, phone, and address in the PERSONAL DATA table (Table 1)
        # Table 1 structure: Office address, Office telephone, Work email, Home address, Cell phone, Personal email
        personal_data_table = self._find_table_with_cell_text("Work email:")
        if personal_data_table is not None:
            for row in personal_data_table.rows:
                cell_text = row.cells[0].text.strip().lower()

                # Office address
                if office_address and 'office address' in cell_text:
                    formatted_address = office_address.replace('\t', '\n').replace('; ', '\n').replace(';', '\n')
                    _set_cell_text(row.cells[1], formatted_address)
                    self.stats['entries_inserted'] += 1

                # Office telephone
                if office_phone and 'office telephone' in cell_text:
                    _set_cell_text(row.cells[1], office_phone)
                    self.stats['entries_inserted'] += 1

                # Work email
                if work_email and 'work email' in cell_text:
                    _set_cell_text(row.cells[1], work_email)
                    self.stats['entries_inserted'] += 1

                # Home address
                if home_address and 'home address' in cell_text:
                    formatted_address = home_address.replace('\t', '\n').replace('; ', '\n').replace(';', '\n')
                    _set_cell_text(row.cells[1], formatted_address)
                    self.stats['entries_inserted'] += 1

                # Cell phone
                if cell_phone and 'cell phone' in cell_text:
                    _set_cell_text(row.cells[1], cell_phone)
                    self.stats['entries_inserted'] += 1

                # Personal email
                if personal_email and 'personal email' in cell_text:
                    _set_cell_text(row.cells[1], personal_email)
                    self.stats['entries_inserted'] += 1
