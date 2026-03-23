# CV Entry Extraction Specification

**Version**: 1.0
**Date**: 2025-01-22
**Purpose**: Defines what constitutes an "entry" when extracting structured data from CVs

---

## Overview

This document defines what constitutes an **entry** when extracting structured data from a CV.

An **entry** is the smallest self-contained unit that describes one distinct professional item (e.g., a publication, a grant, a position, a degree, an award, a committee role, a mentee, a lecture, a patent, etc.).

Entries may appear as bullets, table rows, paragraphs, or mixed-format structures. The LLM should use both semantic content and visual cues (bullets, indentation, paragraph spacing, tables) to determine boundaries.

---

## What Counts as an Entry?

**Treat each unique item as a separate entry.**

### Publications

- Each article, book chapter, book, abstract, or conference paper is **one entry**
- If multiple publications appear in one paragraph or table cell, split them if their structure (authors + year + title + venue) allows it

### Grants and Funding

- Each grant or funded project (identified by a unique grant number or title) is **one entry**
- Renewals or supplements remain part of the same entry unless clearly separate funded projects

### Positions and Appointments

- Each job title at an institution is **one entry**
- Title progressions such as Assistant → Associate → Professor count as **separate entries** if their date ranges differ

### Education and Training

- Each degree or major training program is **one entry**
- Fellowships, residencies, and postdoctoral programs each count as **one entry**

### Awards and Honors

- Each award, prize, fellowship, or honor is **one entry**

### Teaching

- Each distinct course is **one entry**, even if taught repeatedly
- Multiple semesters of the same course remain part of **one course entry**

### Mentoring and Advising

- Each mentee (e.g., each PhD student, Master's student, or postdoc) is **one entry**

### Talks and Presentations

- Each invited talk, keynote, or conference presentation is **one entry**

### Patents and Intellectual Property

- Each patent or patent application is **one entry**

### Professional Memberships and Service

- Each membership or committee role is **one entry**

---

## Visual and Layout Cues

The LLM should use visual structure to help determine entry boundaries.

### Bullets and Numbered Lists

- Each bullet or numbered item at the same indentation level is **one entry**
- Sub-bullets are usually qualifiers for the parent entry, **not separate entries**

### Paragraphs

- A paragraph break or blank line often indicates a **new entry**
- Multiple sentences describing one item remain **one entry**

### Indentation

- Increased indentation typically indicates qualifiers
- Decreased indentation may signal a **new entry**

### Tables

- Each table row that describes an item is usually **one entry**
- Rows that contain multiple items should be split when possible

### Section Headings

- Headings (e.g., "Education", "Grants", "Publications") define context and are **not entries**

---

## Qualifiers and Multi-line Entries

Entries often include one or more **qualifiers** or detail lines. Qualifiers may include:

- "Co-authored with 3 mentees"
- Funding amounts
- Roles such as "Co-PI" or "Site PI"
- DOIs, PMIDs
- Additional descriptions or responsibilities
- Lists of collaborators

### Rules for Qualifiers

- Qualifiers should be **attached to the preceding entry**
- Do **not** create new entries for lines that merely add detail unless they clearly describe a distinct item
- Lines that wrap due to formatting (long titles, long author lists) must stay within **one entry**
- Global qualifiers (e.g., "All publications are peer-reviewed unless noted") are metadata, **not entries**

---

## Splitting vs. Merging Entries

### When to Split

Split content into multiple entries when:

- A paragraph contains multiple publications or grants with clearly distinguishable structures
- Distinct job titles or roles appear with different date ranges
- Items are separated by numbering, semicolons, or repeated citation patterns

### When to Merge

Merge lines into a single entry when:

- Lines are clearly part of the same item (e.g., description or collaborators)
- A line is simply a continuation of a long title, author list, or grant description
- Indented lines expand on the preceding item

---

## Examples

### Example 1: Grant with Qualifier

```
R01 DC017291: Development of a novel tool for ALS assessment, PI: Jordan Green, 2018–2024.
Co-authored with 3 mentees.
```

**Result**: One entry

**Rationale**: The second line is a qualifier describing the entry, not a separate grant.

---

### Example 2: Publication with Metadata

```
Smith J, Green JR, et al. (2023). Title. Journal.
PMID: 12345678.
```

**Result**: One entry

**Rationale**: The PMID is metadata for the publication, not a separate item.

---

### Example 3: Two Distinct Positions

```
Professor of Environmental Health, Rutgers University, 2006–2010.
Chair, Department of Environmental and Occupational Health, UMDNJ, 2006–2010.
```

**Result**: Two entries

**Rationale**: These are two distinct job titles at different institutions, even though the dates overlap.

---

### Example 4: Multi-line Publication

```
Smith J, Green JR, Johnson A, Williams B, Davis C, Martinez D, et al. (2023).
A novel approach to understanding complex environmental factors in urban health disparities.
Environmental Health Perspectives, 131(4):123-145.
```

**Result**: One entry

**Rationale**: This is a single publication split across multiple lines due to length.

---

### Example 5: Multiple Publications in One Paragraph

```
Smith J (2023). First paper. Journal A. Smith J (2022). Second paper. Journal B.
```

**Result**: Two entries

**Rationale**: Clear citation pattern allows splitting into distinct publications.

---

### Example 6: Course with Qualifiers

```
ENV 501: Environmental Toxicology (Fall 2020, Fall 2021, Fall 2022)
Graduate-level course, 25 students per year
Developed new module on microplastics
```

**Result**: One entry

**Rationale**: The course is taught multiple times, and the additional lines are descriptive qualifiers.

---

### Example 7: Mentoring with Details

```
John Doe, PhD Student, Environmental Health
Dissertation: "Air Quality and Respiratory Health"
Graduated 2023, now Assistant Professor at State University
```

**Result**: One entry

**Rationale**: All lines describe one mentee and their outcomes.

---

### Example 8: Bullet List of Awards

```
• Best Paper Award, Environmental Health Conference, 2023
• Outstanding Mentor Award, Rutgers University, 2021
• Early Career Investigator Award, NIH, 2019
```

**Result**: Three entries

**Rationale**: Each bullet describes a distinct award.

---

### Example 9: Table Row with Multiple Items (Should Split)

```
2023: Paper A, Journal X; Paper B, Journal Y; Paper C, Journal Z
```

**Result**: Three entries

**Rationale**: Semicolons and repeated pattern indicate multiple publications.

---

### Example 10: Global Qualifier (Not an Entry)

```
Publications

All publications are peer-reviewed unless otherwise noted.

1. Smith J (2023). First paper...
2. Smith J (2022). Second paper...
```

**Result**: Two entries (items 1 and 2)

**Rationale**: The qualifier applies to the entire section and is metadata, not an entry.

---

## Summary

- **One real-world item = one entry**
- Use **visual cues** (bullets, spacing, indentation, tables) plus **semantic cues** (titles, dates, grant numbers) to determine boundaries
- **Qualifiers belong with the entry they describe**
- **Split entries only when the content clearly describes multiple distinct items**

---

## Implementation Notes

When implementing this specification:

1. **Extract Word structure** using `extract_docx_structure()` to capture:
   - Text content
   - List levels and indentation
   - Bold/italic formatting
   - Table structure
   - Paragraph spacing

2. **Send structured layout** to LLM with this specification in the system prompt

3. **Return element indices** (not full content) for cost efficiency:
   ```json
   {
     "entry_id": "G1-E1",
     "element_idx_start": 42,
     "element_idx_end": 44,
     "entry_type": "publication",
     "confidence": 0.95
   }
   ```

4. **Extract full content** later using the element indices from the original DOCX

---

## Machine-Readable Schema

For structured output, use this JSON schema:

```json
{
  "type": "object",
  "properties": {
    "entries": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "entry_id": {"type": "string"},
          "element_idx_start": {"type": "integer"},
          "element_idx_end": {"type": "integer"},
          "entry_type": {
            "type": "string",
            "enum": [
              "publication",
              "grant",
              "position",
              "education",
              "award",
              "teaching",
              "mentoring",
              "presentation",
              "patent",
              "service",
              "other"
            ]
          },
          "text_snippet": {"type": "string", "maxLength": 200},
          "confidence": {"type": "number", "minimum": 0, "maximum": 1}
        },
        "required": ["entry_id", "element_idx_start", "entry_type", "confidence"]
      }
    }
  }
}
```
