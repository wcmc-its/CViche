"""
V6: Comprehensive Locked CV Section Headers

~850+ headers covering ALL CV/resume types globally:
- Academic CVs (biomedical + all disciplines)
- Industry/tech/engineering resumes
- Business/management CVs
- Humanities/social science/creative/arts CVs
- Government CVs
- International formats (NIH, NSF, Europass, UK, Canada, Australia, India, Asia)
- LinkedIn-style sections
- Grant biosketches (NIH/NSF/DoD/CIHR/UKRI)

These headers are LOCKED and will NEVER be removed by validation.
"""

LOCKED_CV_HEADERS = {
    # ========== 1. PERSONAL / PROFILE / SUMMARY ==========
    'personal data', 'personal details', 'personal information', 'contact information', 'contact details',
    'profile', 'professional profile', 'career profile', 'academic profile',
    'summary', 'professional summary', 'executive summary', 'career summary',
    'about me', 'personal statement', 'professional statement', 'science identity statement',
    'objective', 'career objective', 'career goals', 'headline',
    'professional overview', 'bio', 'short bio', 'biographical sketch',
    'summary of qualifications', 'key qualifications', 'core qualifications',
    'summary of achievements', 'career highlights', 'highlights',
    'strengths', 'key strengths',
    'professional branding statement', 'value proposition',
    'personal mission statement', 'professional mission statement',
    'overview of academic activities', 'overview of professional activities',
    'summary of contributions', 'statement of contributions',
    'statement of impact', 'impact narrative',

    # ========== 1A. DIVERSITY, EQUITY & INCLUSION ==========
    'diversity', 'equity', 'inclusion', 'dei', 'diversity statement',
    'diversity, equity and inclusion', 'diversity equity and inclusion',
    'diversity and inclusion', 'equity and inclusion',
    'diversity activities', 'dei activities', 'dei statement',
    'commitment to diversity', 'inclusion statement',

    # ========== 2. EDUCATION & TRAINING ==========
    'education', 'education and training', 'academic background', 'educational background',
    'educational qualifications', 'qualifications', 'academic qualifications',
    'professional qualifications', 'academic degrees',
    'undergraduate education', 'graduate education', 'postgraduate education',
    'professional education', 'medical education', 'dental education',
    'nursing education', 'pharmacy education',
    'training', 'professional training', 'technical training',
    'clinical training', 'specialty training', 'subspecialty training',
    'postdoctoral training', 'residency training', 'residency education',
    'fellowship training', 'fellowship education',
    'continuing education', 'continuing professional development',
    'continuing medical education', 'cme', 'executive education',
    'coursework', 'relevant coursework',
    'licenses and certifications', 'certifications',
    'professional credentials', 'academic credentials',
    'bootcamps', 'workshops', 'seminars', 'online courses', 'moocs',
    'massive open online courses',
    'apprenticeships', 'internships', 'externships',
    'graduate training', 'vocational training',
    'faculty development', 'leadership development', 'career development',
    'skill development', 'professional development',
    'simulation training', 'skills training', 'assessment activities',

    # ========== 3. EXPERIENCE & EMPLOYMENT ==========
    'experience', 'work experience', 'professional experience',
    'employment history', 'career history', 'job history',
    'positions', 'positions held', 'positions and employment',
    'professional positions and employment', 'other professional positions',
    'industry positions', 'industry experience',
    'private practice positions', 'field experience', 'relevant experience',
    'research experience', 'clinical experience', 'teaching experience',
    'leadership experience', 'management experience', 'executive experience',
    'technical experience', 'engineering experience', 'consulting experience',
    'project experience', 'international experience', 'global experience',
    'military experience', 'volunteer experience', 'community experience',
    'nonprofit experience', 'freelance experience', 'contract experience',
    'gig experience', 'remote work experience', 'hybrid work experience',

    # ========== 4. APPOINTMENTS & ROLES ==========
    'appointments', 'appointments and positions', 'appointments and roles',
    'academic appointments', 'teaching appointments', 'research appointments',
    'clinical appointments', 'hospital appointments',
    'visiting appointments', 'visiting positions', 'visiting scholar positions',
    'joint appointments', 'secondary appointments',
    'adjunct appointments', 'affiliate appointments',
    'employment status', 'appointment and promotion history',
    'title history', 'rank progression',
    'board appointments', 'advisory roles', 'executive roles',
    'founder roles', 'cofounder roles', 'leadership roles', 'management roles',
    'director roles', 'committee roles', 'consulting roles',
    'advisor roles', 'mentor roles', 'coaching roles',

    # ========== 5. RESPONSIBILITIES ==========
    'responsibilities', 'faculty responsibilities', 'professional responsibilities',
    'clinical responsibilities', 'administrative responsibilities',
    'academic responsibilities', 'service responsibilities',
    'percent effort and institutional responsibilities', 'percent effort',
    'teaching effort', 'clinical effort', 'research effort', 'administrative effort',
    'current site activity', 'weill cornell activity',
    'effort distribution', 'effort allocation',
    'annual accomplishments', 'annual performance summary',
    'annual faculty activities', 'annual report of activities',
    'faculty activity report', 'future directions', 'goals for upcoming year',

    # ========== 6. SKILLS ==========
    'skills', 'core skills', 'technical skills', 'hard skills', 'soft skills',
    'transferable skills', 'leadership skills', 'management skills',
    'communication skills', 'analytical skills', 'research skills',
    'data skills', 'statistical skills', 'design skills', 'creative skills',
    'coding skills', 'programming skills', 'engineering skills',
    'laboratory skills', 'clinical skills', 'procedural skills',
    'computer skills', 'language skills', 'languages',
    'tools and technologies', 'software proficiency', 'platforms',
    'frameworks', 'methodologies', 'competencies',
    'areas of expertise', 'domains of expertise', 'specializations',

    # ========== 7. PROJECTS ==========
    'projects', 'key projects', 'major projects', 'selected projects',
    'representative projects', 'academic projects', 'industry projects',
    'engineering projects', 'software projects', 'design projects',
    'creative projects', 'client projects', 'research projects',
    'capstone projects', 'thesis project', 'dissertation project',
    'independent projects', 'open-source projects', 'portfolio projects',
    'product development', 'product leadership', 'program development',
    'innovation projects', 'entrepreneurship projects', 'startup projects',

    # ========== 8. TEACHING & EDUCATION ==========
    'educational contributions', 'teaching', 'teaching activities',
    'teaching experience', 'teaching roles', 'teaching interests',
    'teaching statement', 'teaching philosophy',
    'didactic teaching', 'clinical teaching', 'administrative teaching',
    'continuing education and professional education',
    'community education', 'patient outreach', 'public education',
    'education activities', 'education leadership', 'education administration',
    'education portfolio', 'teaching portfolio',
    'teaching evaluations', 'teaching innovations', 'teaching achievements',
    'instructional activities', 'instructional experience',
    'training activities', 'mentorship teaching',
    'course leadership', 'course administration', 'courses taught',
    'formal scheduled classes', 'formal scheduled classes for students',
    'curriculum development', 'curriculum design', 'curriculum leadership',
    'learner assessment', 'examination activities',
    'simulation teaching', 'workshops taught', 'seminars taught',
    'program leadership', 'training program leadership', 'clerkship leadership',
    'graduate medical education', 'gme',
    'undergraduate medical education', 'ume',
    'learning facilitation', 'learning design', 'instructional design',
    'educational outreach',

    # ========== 9. MENTORING & ADVISING ==========
    'mentoring', 'mentoring activities', 'mentorship', 'mentorship activities',
    'mentorship outcomes', 'mentorship effectiveness', 'mentorship roles',
    'mentoring statement', 'current mentees', 'past mentees',
    'advising', 'academic advising', 'career advising', 'peer mentoring',
    'faculty mentoring', 'research mentorship', 'teaching supervision',
    'clinical supervision', 'leadership supervision',
    'institutional training grants', 'mentored trainee grants',
    'coaching', 'supervision', 'supervision experience',
    'student supervision', 'doctoral supervision', 'postdoctoral supervision',
    'predoctoral students', 'predoctoral students supervised',
    'predoctoral students supervised or mentored',
    'phd supervision', 'msc supervision', 'intern supervision',
    'staff supervision', 'thesis supervision', 'dissertation supervision',
    'team leadership',

    # ========== 10. CLINICAL PRACTICE ==========
    'clinical practice', 'clinical practice, innovation, and leadership',
    'clinical innovations', 'clinical leadership', 'clinical activities',
    'clinical expertise', 'clinical specialties', 'procedures performed',
    'clinical competencies', 'scope of practice', 'clinical workload',
    'clinical program leadership', 'clinical program development',
    'clinical productivity', 'clinical outcomes activities',
    'clinical quality activities', 'practice improvement activities',
    'quality improvement activities', 'patient safety activities',
    'inpatient responsibilities', 'outpatient responsibilities',
    'service line leadership', 'call responsibilities',
    'clinic administration', 'practice administration',

    # ========== 11. RESEARCH ==========
    'research', 'research activities', 'research interests', 'research statement',
    'research overview', 'research goals', 'research objectives',
    'research focus areas', 'research contributions', 'research achievements',
    'research accomplishments', 'research highlights', 'research portfolio',
    'scholarly activity', 'scholarly contributions', 'scholarship',
    'scholarship of discovery', 'scholarship of integration',
    'scholarship of application', 'scholarship of teaching',
    'laboratory activities', 'laboratory experience', 'laboratory management',
    'laboratory leadership', 'fieldwork',
    'research infrastructure development', 'research mobility',
    'research collaborations', 'collaborative research activities',
    'research supervision', 'research leadership',
    'team science activities', 'multidisciplinary activities',
    'interdisciplinary activities', 'translational science activities',
    'implementation science activities',
    'clinical trial activities', 'clinical trial leadership',
    'study leadership', 'study management',
    'registries and databases', 'data resources developed',
    'public health research', 'quantitative research', 'qualitative research',
    'mixed-methods research', 'grant-funded research',
    'data analysis', 'data science activities',
    'informatics activities', 'machine learning research',
    'ai research', 'computational research',

    # ========== 12. FUNDING & GRANTS ==========
    'research support', 'research funding', 'funding',
    'current research funding', 'current funding', 'past funding',
    'completed funding', 'pending funding', 'funding track record',
    'grants', 'awarded grants', 'grant support', 'grant funding',
    'grant history', 'grant portfolio',
    'external funding', 'internal funding', 'seed funding', 'pilot funding',
    'federal funding', 'foundation funding', 'industry funding',
    'government funding', 'sponsored research', 'sponsored projects',
    'center grants', 'program project grants',
    'multi-investigator grants', 'core facility leadership',
    'proposal submissions', 'grant applications',

    # ========== 13. INNOVATION & IP ==========
    'patents and inventions', 'intellectual property', 'ip portfolio',
    'patent portfolio', 'technology transfer', 'technology development',
    'commercialization activities', 'commercialization',
    'innovation activities', 'innovation', 'innovation leadership',
    'innovation portfolio', 'entrepreneurial activities', 'entrepreneurship',
    'startup activities', 'startup leadership', 'venture creation',
    'software tools', 'datasets', 'protocols',
    'white papers', 'technical reports', 'working papers',
    'digital media', 'creative works', 'disclosures',
    'product innovation',

    # ========== 14. ADMINISTRATION & LEADERSHIP ==========
    'leadership', 'leadership activities', 'executive leadership',
    'management', 'administration', 'administrative activities',
    'program administration', 'operations management', 'business management',
    'organizational leadership', 'vision and strategy', 'strategic planning',
    'change management', 'process improvement', 'performance improvement',
    'people management', 'human resources leadership',

    # ========== 15. SERVICE ==========
    'service', 'institutional service', 'professional service',
    'community service', 'public service', 'volunteer service',
    'civic engagement', 'outreach', 'public outreach',
    'science communication', 'industry service',
    'university service', 'school service', 'hospital service',
    'departmental service', 'divisional service',
    'committee service', 'board service', 'committee leadership',
    'task force participation', 'working groups', 'working group participation',
    'governance', 'governance activities', 'professional citizenship',
    'extramural professional responsibilities', 'extramural service',
    'leadership in extramural organizations', 'service on boards and committees',
    'regional service', 'national service', 'international service',
    'organizational service', 'society service',
    'government service', 'government and other professional services',
    'other professional services', 'professional services',
    'consulting activities', 'industry collaborations',
    'strategic partnerships', 'professional partnerships',
    'collaborative networks',

    # ========== 16. EDITORIAL & REVIEWING ==========
    'editorial activities', 'editorial board membership', 'editorial boards',
    'journal reviewing', 'ad hoc reviewing', 'reviewer activities',
    'editor', 'co-editor', 'grant reviewing', 'peer review',
    'study sections', 'scientific review panels',
    'external review activities', 'external examining', 'external appointments',

    # ========== 17. PRESENTATIONS & TALKS ==========
    'presentations', 'talks', 'public talks', 'speaking engagements',
    'invited presentations', 'scientific presentations',
    'professional presentations', 'research presentations',
    'lectures', 'seminars', 'invited talks', 'invited lectures', 'invited seminars',
    'international lectures',
    'grand rounds', 'keynote presentations', 'plenary presentations',
    'conference presentations', 'poster presentations', 'oral presentations',
    'workshop presentations', 'panel discussions',
    'regional invited presentations', 'national invited presentations',
    'international invited presentations',
    'international meetings and symposia', 'international symposia',
    'webinars', 'podcasts', 'media appearances', 'press', 'interviews',
    'broadcast appearances', 'public events',

    # ========== 18. PUBLICATIONS & SCHOLARLY OUTPUT ==========
    'bibliography', 'publications', 'scholarly publications',
    'academic publications', 'creative publications', 'professional publications',
    'scientific publications', 'research publications', 'technical publications',
    'clinical publications', 'educational publications', 'policy publications',
    'conference papers', 'peer-reviewed research articles',
    'non-peer-reviewed research publications', 'peer-reviewed articles',
    'peer reviewed publications', 'articles', 'journal articles',
    'review articles', 'reviews', 'commentaries',
    'magazine articles', 'newspaper articles', 'blog posts',
    'reviews and editorials', 'books', 'book chapters', 'chapters',
    'edited volumes', 'monographs', 'poetry', 'fiction', 'nonfiction',
    'case reports', 'guidelines', 'consensus statements',
    'position statements', 'position papers',
    'conference proceedings', 'in review', 'submitted',
    'abstracts', 'posters', 'manuscripts in preparation',
    'other media', 'other publications', 'digital publications',
    'multimedia works', 'reports', 'policy papers',

    # ========== 19. HONORS & AWARDS ==========
    'honors', 'honors and awards', 'awards', 'recognition',
    'academic honors', 'professional honors', 'distinctions', 'prizes',
    'fellowships and awards', 'scholarships and awards', 'scholarships',
    'fellowships', 'named lectureships',
    'excellence in teaching awards', 'research awards', 'teaching awards',
    'clinical awards', 'achievement awards', 'notable achievements',
    'career achievements', 'achievements', 'accolades', 'commendations',
    'certificates of achievement', 'professional recognition',
    'industry recognition', 'employee awards',
    'recognition of exemplary service during the covid-19 pandemic',
    'positions and honors',

    # ========== 20. AFFILIATIONS & MEMBERSHIPS ==========
    'affiliations', 'institutional and hospital affiliation',
    'hospital affiliations', 'institutional affiliations',
    'professional affiliations', 'organizational affiliations',
    'society memberships', 'memberships', 'committees',
    'professional organizations', 'professional organizations and society memberships',
    'scientific societies', 'professional society memberships',

    # ========== 21. LICENSURE & CERTIFICATION ==========
    'licensure', 'medical licensure', 'state licensure', 'professional licensure',
    'board certification', 'board eligibility', 'credentialing',
    'privileging', 'clinical privileges',

    # ========== 22. PORTFOLIOS ==========
    'portfolio', 'professional portfolio', 'design portfolio',
    'art portfolio', 'project portfolio', 'creative portfolio',
    'engineering portfolio', 'writing portfolio', 'media portfolio',

    # ========== 23. COMPLIANCE, ETHICS & SAFETY ==========
    'compliance', 'ethics activities', 'bioethics activities',
    'regulatory compliance', 'safety training', 'lab safety', 'clinical safety',
    'data governance', 'research ethics',

    # ========== 24. MISCELLANEOUS COMMON HEADERS ==========
    'interests', 'hobbies', 'personal interests',
    'extracurricular activities', 'initiatives',
    'professional development activities',
    'conferences attended', 'events organized', 'workshops organized',

    # ========== 25. COMMON VARIATIONS ==========
    'curriculum vitae', 'cv', 'vita', 'resume'
}
