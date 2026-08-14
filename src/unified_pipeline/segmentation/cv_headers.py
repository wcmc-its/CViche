"""CV section-header vocabulary for chunked Word segmentation.

Split out of word_chunked.py so the header data lives apart from the
detection/segmentation logic (#315 follow-up). Pure data — no imports.
"""

# V6: WCM Taxonomy-based known CV section headers
# Used as signal #11 in header detection to boost confidence for recognized section titles
KNOWN_CV_HEADERS = {
    # Section A: Contact/Personal Data
    'contact information', 'personal data', 'contact info', 'personal information',
    'name', 'address', 'email', 'phone', 'office',

    # Section B: Education
    'education', 'education and training', 'academic background',
    'degrees', 'graduate education', 'undergraduate education',
    'doctoral degree', 'medical degree', 'other education', 'professional development',

    # Section C: Postdoctoral Training
    'postdoctoral training', 'postdoctoral fellowship', 'post-doctoral',
    'residency training', 'fellowship training', 'internships',
    'post graduate training and fellowship appointments',

    # Section D: Professional Positions
    'professional positions', 'employment', 'academic appointments',
    'professional experience', 'work experience', 'positions held',
    'academic positions', 'clinical positions', 'administrative positions',
    'positions', 'positions: academic', 'positions: professional', 'positions: other',
    'previous positions', 'current position', 'current positions',

    # Section E: Employment Status
    'employment status', 'current appointment', 'appointment status',

    # Section F: Licensure & Certification
    'licensure', 'board certification', 'certifications', 'licenses',
    'licensure and certification', 'professional licenses',
    'certification and licensure', 'license to practice',

    # Section G: Institutional Affiliations
    'institutional affiliations', 'hospital affiliations', 'affiliations',

    # Section H: Honors & Awards
    'honors and awards', 'honors', 'awards', 'recognitions', 'distinctions',
    'prizes', 'fellowships', 'named lectureships',
    'honors: scholarships/grants', 'honors: other', 'scholarships', 'scholarships/grants',

    # Section I: Professional Organizations
    'professional organizations', 'memberships', 'societies',
    'professional societies', 'professional memberships',
    'scientific appointments: memberships', 'organizations and professional societies',

    # Section J: Percent Effort
    'percent effort', 'institutional responsibilities', 'effort distribution',

    # Section K: Educational Contributions
    'educational contributions', 'teaching', 'teaching experience', 'teaching experiences',
    'course teaching', 'medical student teaching', 'resident teaching',
    'curriculum development', 'educational leadership',
    'institutional teaching activities',

    # Section L: Clinical Practice
    'clinical practice', 'clinical leadership', 'clinical activities',
    'patient care', 'clinical work',

    # Section M: Research
    'research', 'research overview', 'research interests', 'research experience',
    'grant support', 'funding', 'research support', 'grants', 'research grants',
    'current funding', 'past funding', 'pending funding',
    'active grants', 'completed grants', 'current research studies',
    'clinical trials', 'research projects',

    # Section N: Mentoring
    'mentoring', 'mentorship', 'mentoring and supervision', 'student supervision',
    'trainees', 'mentees', 'current mentees', 'past mentees',
    'training grants', 'mentoring philosophy',

    # Section O: Institutional Leadership
    'institutional leadership', 'leadership activities', 'leadership roles',
    'committee service', 'institutional service',
    'committees and work groups',

    # Section P: Administrative Activities
    'administrative activities', 'administration', 'administrative roles',
    'institutional administration', 'departmental service',

    # Section Q: Extramural Professional Activities
    'extramural professional activities', 'professional responsibilities',
    'editorial activities', 'editorial boards', 'reviewer activities',
    'grant reviewing', 'peer review', 'national committees',
    'consulting', 'advisory boards',
    'scientific appointments: assistant reviewer', 'editorial review',

    # Section R: Invitations to Speak
    'invitations to speak', 'presentations', 'invited talks',
    'speaking engagements', 'seminars', 'lectures',
    'orals', 'oral presentations', 'invited lectures and oral presentations',

    # Section S: Bibliography/Publications
    'bibliography', 'publications', 'scholarly works',
    'peer-reviewed articles', 'peer reviewed publications', 'journal articles',
    'peer-reviewed journal publications',
    'books', 'book chapters', 'reviews and editorials',
    'non-peer-reviewed publications', 'conference proceedings',
    'abstracts', 'posters', 'poster presentations', 'in review', 'submitted',
    'manuscripts in preparation', 'other publications',

    # Section T: Supplemental
    'supplemental information', 'additional information', 'other',
    'references', 'languages', 'skills', 'public outreach',
    'media appearances', 'bibliometric summary', 'courses attended',
    'professional development courses',

    # Compound section headers (common in structured CVs)
    'positions, scientific appointments, honors',
    'relevant work experience',

    # Common variations and organizational headers
    'curriculum vitae', 'cv', 'resume', 'vita',
    'current', 'past', 'active', 'completed', 'pending',
    'selected', 'representative', 'major', 'significant',

    # Year-based organizational headers (common in CVs)
    '2024', '2023', '2022', '2021', '2020', '2019', '2018', '2017', '2016', '2015',
    '2014', '2013', '2012', '2011', '2010'
}

# Convert to lowercase set for fast O(1) lookup
KNOWN_CV_HEADERS_SET = {h.lower() for h in KNOWN_CV_HEADERS}


# Known subsection terms that should NEVER be H1 (top-level)
# These are organizational subdivisions, not major CV sections
KNOWN_SUBSECTION_TERMS = {
    # Geographic scope (common under Presentations, Service, etc.)
    'international', 'national', 'regional', 'local', 'state', 'institutional',
    # Time-based
    'current', 'past', 'completed', 'active', 'pending', 'ongoing',
    # Qualifier-based (e.g. "Selected Publications", "Representative Grants")
    'selected', 'representative', 'major', 'significant',
    # Role-based
    'principal investigator', 'co-investigator', 'co-pi', 'consultant',
    'advisor', 'mentor', 'co-mentor', 'coordinator', 'student',
    # Publication types (when under Publications)
    'peer-reviewed', 'non-peer-reviewed', 'in preparation', 'submitted', 'in review',
}
