# CViche User Guide

## What Is CViche?

CViche is a tool built by the Samuel J. Wood Library at Weill Cornell Medicine that converts your existing CV into the official WCM CV format. It uses AI to read your CV, identify each section (education, publications, grants, etc.), and reorganize everything into the correct WCM template -- saving you hours of manual reformatting.

**What CViche does:**

- Reads your CV in Word (.docx) or PDF format
- Identifies and classifies every entry (degrees, positions, publications, grants, honors, and more)
- Maps each entry to the correct section of the WCM CV template (71 possible sections)
- Enriches publication data using PubMed (adds author lists, journal names, MeSH terms)
- Generates a formatted Word document in the official WCM CV layout
- Optionally generates a research narrative summary

**What CViche does NOT do:**

- CViche does not replace human review. The output is a starting point that should be reviewed and corrected before official use.
- CViche does not access any external systems on your behalf. It only processes the document you upload.
- CViche does not store or share your CV outside the project team.

---

## Getting Started

### Logging In

1. Navigate to the CViche web application in your browser. (Your administrator will provide the URL.)
2. On the login screen, enter your **full name** and your **WCM email address** (e.g., `abc1234@med.cornell.edu`).
3. Click **Sign In**.

Your email must be on the approved access list. If you see "Access denied," contact your administrator to request access.

Once signed in, your session lasts 7 days. You do not need to log in again during that period unless you explicitly log out or your browser clears cookies.

> **Note:** In a future update, CViche will support single sign-on through WCM's Enterprise Directory (SAML SSO). When that is available, you will log in with your standard WCM credentials.

### The Consent Process

The first time you use CViche, you will see a consent page before you can upload a CV. This is a one-time step (unless the consent text is updated, in which case you will be asked to re-consent).

**What you are agreeing to:**

- The Samuel J. Wood Library is conducting a pilot of CViche, a prototype tool that uses AI to convert CVs into WCM format.
- Your CV will be processed using AI. The output will be reviewed by project staff, and your feedback will be analyzed to improve the system.
- You may omit personal contact details from your CV before uploading if you prefer.
- Your name, email, and authorization role are collected as part of the pilot.

**Authorization role:** On the consent page, you will be asked to select one of two roles:

- **Submitting my own CV** -- You are uploading your own CV for conversion.
- **Authorized on behalf of faculty** -- You are an authorized staff member uploading a CV on behalf of a faculty member.

This selection becomes your default for future uploads but can be changed on a per-upload basis.

After reading the consent text, check the consent checkbox and click **Agree and Continue**.

---

## Uploading a CV

### Supported File Formats

CViche accepts the following file types:

- **Word documents** (.docx) -- recommended for best results
- **PDF files** (.pdf) -- text-based PDFs work best

Older Word files (.doc) are not accepted; save them as .docx first.

A PDF is converted to Word before processing. Text-based PDFs (exported from Word, Google Docs, or similar) convert well, though they sometimes lose structural information (headers, tables, lists) that can affect the quality of the output. A scanned PDF is only images of pages, so its text can't be read: a fully scanned PDF is rejected at upload, and any scanned pages in an otherwise readable PDF are named in the run log as missing from the output.

### Upload Steps

1. On the main page, click **Select a file** or drag and drop your CV file.
2. Configure the three options below the file input:

   - **Submission type:** Defaults to the role you selected during consent. Toggle between "Submitting my own CV" and "Authorized on behalf of faculty" if needed for this specific upload.
   - **Show track changes:** On by default. When enabled, the output Word document includes track changes marks showing what the pipeline modified from the original text. This makes it easy to see exactly what was changed.
   - **Show pipeline comments:** Off by default. When enabled, the output document includes comments in the margins with classification details, confidence scores, and processing notes. This is useful if you want to understand why the pipeline made specific decisions.

3. Click **Start Pipeline**.

### Usage Limits

To manage system resources during the pilot, each user is limited to:

- **10 runs per day** (resets at midnight Eastern Time)
- **50 runs per month** (resets on the 1st of each month)

Your remaining quota is displayed on the upload page (e.g., "7 of 10 runs remaining today"). When the limit is reached, the upload button is disabled with an explanation of when the limit resets.

Administrators are not subject to usage limits.

---

## What Happens During Processing

After you click **Start Pipeline**, CViche processes your CV through a series of stages. This typically takes **3 to 8 minutes** depending on the length of your CV and the number of publications.

### The Progress View

The pipeline viewer shows:

- **Left sidebar:** All 12 processing stages with status icons (pending, running, complete, or error).
- **Top bar:** Your run ID, total processing cost, token usage, and overall status.
- **Main panel:** Details for the currently selected stage, including logs and output files.

### Processing Stages

1. **Hierarchy Extraction** -- Uses AI to identify the major sections of your CV (education, publications, grants, etc.) and their structure.
2. **Hierarchy Mapping** -- Maps extracted section headers to specific locations in your document.
3. **Entry Extraction** -- Splits each section into individual entries (one per degree, one per publication, etc.).
4. **Header Taxonomy Mapping** -- Uses AI to assign each section header to the correct WCM taxonomy code (e.g., S1 for Peer-Reviewed Articles, B1 for Education).
5. **Entry Classification** -- Classifies individual entries into specific WCM categories, with a correction pipeline for ambiguous items.
6. **Field Extraction** -- Pulls out structured fields from each entry (dates, titles, institutions, author names, grant numbers, etc.).
7. **Research Summary** -- Generates a biosketch-style research narrative summarizing your research activities.
8. **PubMed Enrichment** -- Matches your publications against PubMed and adds metadata (full author lists, journal names, MeSH terms, PMIDs).
9. **Institution Enrichment** -- Adds location data (city, state, country) to your education and employment entries using an LLM lookup.
10. **Teaching Formatter** -- Standardizes teaching activity descriptions into a consistent format.
11. **Citation Formatter** -- Reformats publication citations that were not found in PubMed into standard Vancouver format.
12. **WCM Word Template** -- Produces the final formatted Word document in the official WCM CV layout.

You can watch each stage complete in real time. If a stage encounters an error, it will be flagged and you can view the error details in the logs.

---

## Downloading Your Output

When processing is complete, a success overlay appears with:

- **Download WCM CV** button -- downloads the formatted Word document
- **Give Feedback** button -- opens the feedback form (more on this below)
- **"I'll do this later"** link -- dismisses the overlay

You can also download the output at any time by:

1. Finding the run in your **Previous Runs** list on the upload page.
2. Clicking the run to open the pipeline viewer.
3. Navigating to the final stage and downloading the output file.

### Understanding the Output Document

The output is a Word (.docx) file organized into the WCM CV sections. Here is what you may see:

- **Track changes** (if enabled): Insertions and deletions are marked using Word's Track Changes feature. This shows you exactly what text was added, removed, or moved during conversion. You can accept or reject changes individually in Word.
- **Comments** (if pipeline comments are enabled): Margin comments from the pipeline explain classification decisions, show confidence scores, and note any entries that required special handling. These can be deleted in bulk in Word when you no longer need them.
- **Empty sections:** WCM sections that had no matching content in your CV are included as empty placeholders. You can fill these in manually if needed, or delete them.

---

## Giving Feedback

Your feedback is critical to improving CViche. It takes approximately **3 minutes** and directly informs the research behind this project.

### When You Will Be Asked

- **Right after processing completes:** The success overlay includes a prominent "Give Feedback" button.
- **On the upload page:** An amber banner shows how many of your runs are awaiting feedback (e.g., "You have 3 runs awaiting feedback"). This banner remains visible until all completed runs have feedback.
- **In run history:** Each completed run shows either an amber "Needs feedback" badge or a green "Feedback given" badge.
- **In the pipeline viewer:** Completed runs have a "Feedback" tab alongside the Logs and Prompt Logs tabs.

### What the Feedback Form Asks

The form is tailored to the specific run you are reviewing. Questions include:

1. **Your role** -- Are you the CV subject, departmental staff, or library/faculty affairs staff?
2. **Accuracy** (1-10) -- Were names, dates, and content faithfully preserved?
3. **Completeness** (1-10) -- Was all content from the original captured?
4. **Usefulness** (1-5) -- How usable is this as a starting point?
5. **Manual effort estimate** -- How long would it take to reformat this CV manually?
6. **Correction effort** -- How long to fix the CViche output?
7. **Publication enrichment quality** (1-5) -- Shown only if PubMed enrichment ran
8. **Research summary quality** (1-5) -- Shown only if a research summary was generated
9. **Issues noticed** -- Rate the severity of six common issue types (missing content, wrong section, etc.)
10. **Issue locations** -- Check which WCM sections had problems (only sections that were populated in your run are shown)
11. **Biggest concern** -- Free text describing the single most important issue
12. **Likelihood to recommend** (1-5) -- Would you recommend CViche to a colleague?

You can only submit feedback once per run. If both the CV subject and an administrator review the same run, both can submit separate feedback.

### Why Feedback Matters

Feedback data is the primary research dataset for evaluating CViche's effectiveness. Your responses help us:

- Identify which pipeline stages need improvement
- Measure time savings compared to manual conversion
- Understand which WCM sections are most problematic
- Prioritize bug fixes and feature improvements

---

## Viewing Previous Runs

Your **Previous Runs** list appears on the upload page below the file upload area. It shows your 20 most recent runs, with the option to load more.

For each run, you can see:

- **Filename** and **date**
- **Status** (complete, running, failed, or cancelled)
- **Duration** and **cost**
- **Feedback badge** (amber "Needs feedback" or green "Feedback given")

Click any run to open it in the pipeline viewer, where you can:

- Review logs for each stage
- Download output files
- Give feedback (for completed runs)
- View prompt logs (detailed AI interaction data)

### Re-running a CV

If a run failed or you want to try again with different settings:

1. Click the failed or completed run in your history.
2. Click **Restart with this file** to create a new run using the same uploaded document.
3. Adjust settings (track changes, pipeline comments, submission type) if desired.
4. Click **Start Pipeline** to begin.

Each restart counts as a new run against your usage limits.

If the original file is no longer available, you will be prompted to upload the file again.

---

## Frequently Asked Questions

### Why was something classified in the wrong section?

CViche uses AI to determine which WCM section each CV entry belongs to. With 71 possible sections, the AI sometimes makes mistakes, especially with:

- Entries that could fit multiple categories (e.g., a teaching award could go under Honors or Teaching)
- Unusual formatting or non-standard CV structures
- Entries in languages other than English

If you notice misclassifications, please report them in the feedback form (question 9, "Wrong section placement"). This data helps us improve the classification model.

### Can I re-run a CV?

Yes. Click the run in your Previous Runs list and use the "Restart with this file" button. Each re-run counts toward your daily and monthly usage limits.

### Who sees my data?

Your CV and the converted output are accessible only to:

- **You** (the person who uploaded it)
- **CViche administrators** (project team members with admin access)

Your data is used solely for the CViche pilot project. Feedback responses are anonymized in aggregate reporting. Your CV is stored securely and is not shared outside the project team.

### What happens if processing fails partway through?

If the pipeline fails at any stage, you can:

1. View the partial results and logs up to the point of failure.
2. Use "Restart with this file" to try again.

Common causes of failure include very long CVs that exceed processing limits, or unusual document formatting that the parser cannot interpret.

### Can I use CViche for someone else's CV?

Yes, if you are authorized to do so. Select "Authorized on behalf of faculty" as your submission type. This is intended for departmental staff and faculty affairs personnel who manage CV submissions.

### How long are my files kept?

Uploaded CVs and output documents are retained for the duration of the pilot. They are not automatically deleted. If the retention policy changes, you will be notified.

### The page says my session expired. What happened?

Your login session lasts 7 days. After that, you need to sign in again. In development environments, sessions may also expire when the server restarts. This is normal.

### How accurate is CViche?

Accuracy varies depending on the structure and complexity of your CV. CViche works best with well-structured Word documents that use clear section headings. Common areas where the AI may need correction include:

- Entries near the boundary between two similar WCM categories
- Publications with non-standard citation formats
- Sections unique to your field that do not map neatly to the 71 WCM categories

The output should always be reviewed before official use. Your feedback on accuracy directly helps us improve the system.

---

## Getting Help

If you encounter issues or have questions about CViche:

- **Technical issues** (errors, failed runs, login problems): Contact Paul Albert at the Samuel J. Wood Library.
- **Questions about the WCM CV format**: Contact the Office of Faculty Affairs.
- **Feedback about CViche itself**: Use the in-app feedback form after each run.

Your administrator can adjust your usage limits, troubleshoot access issues, and escalate technical problems to the development team.
