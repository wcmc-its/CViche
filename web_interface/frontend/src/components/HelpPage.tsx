import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ChevronDown } from 'lucide-react'

const sections = [
  { id: 'what-is-cviche', label: 'What is CViche?' },
  { id: 'getting-started', label: 'Getting Started' },
  { id: 'understanding-results', label: 'Understanding Your Results' },
  { id: 'data-retention', label: 'Data Retention Policy' },
  { id: 'faq', label: 'Frequently Asked Questions' },
  { id: 'contact', label: 'Contact & Support' },
]

const CONTACT_EMAIL = 'paa2013@med.cornell.edu'

const handleScrollTo = (e: React.MouseEvent, id: string) => {
  e.preventDefault()
  const el = document.getElementById(id)
  if (el) {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: prefersReducedMotion ? 'auto' : 'smooth', block: 'start' })
  }
}

const mailLink = (
  <a href={`mailto:${CONTACT_EMAIL}`} className="text-primary-600 hover:underline">
    {CONTACT_EMAIL}
  </a>
)

const H2 = 'text-[19px] font-semibold text-gray-900 mb-3'
const DT = 'pt-3 sm:py-3 sm:border-b sm:border-sand-200 font-semibold text-gray-900'
const DD = 'pb-3 sm:py-3 border-b border-sand-200'

const gettingStartedTiles = [
  ['.docx or .pdf', "Text-based PDFs work best; scanned pages can't be read"],
  ['2–6 minutes', 'Longer CVs with many publications can take more'],
  ['No charge to you', 'AI processing is covered by the Library'],
]

const faqs: { q: string; a: React.ReactNode }[] = [
  {
    q: 'What file formats does CViche accept?',
    a: (
      <>
        CViche accepts .docx (Microsoft Word) and .pdf files. Text-based PDFs work best: a scanned page is
        only an image, so its text can't be read. If your CV is in .doc or another format, please convert it
        to .docx before uploading.
      </>
    ),
  },
  {
    q: 'How long does processing take?',
    a: (
      <>
        Most CVs are processed in 2 to 6 minutes. Longer CVs with many publications may take 10 minutes or
        more. You can watch the progress in real time on the run page, or close it and come back later:
        processing continues on the server and the finished run appears in your run history.
      </>
    ),
  },
  {
    q: 'Does it cost me anything?',
    a: <>No. The AI processing costs are covered by the Library.</>,
  },
  {
    q: 'Can I re-run a CV?',
    a: (
      <>
        Yes. Upload the same file again from the main page. Each upload creates a new run. Your previous runs
        are saved on the Runs page.
      </>
    ),
  },
  {
    q: 'Is my data secure?',
    a: (
      <>
        Your uploaded CV is stored on a secure WCM server and is only accessible to you and CViche
        administrators. However, the text of the CV is sent to a third-party AI service for processing:
        Anthropic&apos;s Claude, running on Amazon Bedrock. AWS states that Bedrock does not share CV text or
        AI output with Anthropic or any other model provider, and does not use it to train models.
        Before the text is sent, CViche removes the dates of birth and Social Security numbers it
        recognizes. It can miss some formats, so you should not include anything you would not want these
        systems to see. By using CViche, you acknowledge that your CV content will be processed by a
        third-party AI service. If this is not acceptable, please do not use the service.
      </>
    ),
  },
  {
    q: 'What if something looks wrong in my results?',
    a: (
      <>
        Use the feedback form on your completed run page to report the specific issue. For general questions or
        urgent concerns, email Paul Albert at {mailLink}.
      </>
    ),
  },
]

export default function HelpPage() {
  const { hash } = useLocation()
  const [activeId, setActiveId] = useState(sections[0].id)
  const [openFaq, setOpenFaq] = useState<number | null>(0)

  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView()
  }, [hash])

  // Highlight the section currently in view.
  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return
    const visible = new Set<string>()
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) visible.add(entry.target.id)
          else visible.delete(entry.target.id)
        })
        const first = sections.find((s) => visible.has(s.id))
        if (first) setActiveId(first.id)
      },
      { rootMargin: '-10% 0px -60% 0px' },
    )
    sections.forEach((s) => {
      const el = document.getElementById(s.id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [])

  return (
    <main className="px-4 sm:px-7 pt-8 pb-12">
      <div className="w-full max-w-[1080px] mx-auto grid grid-cols-1 lg:grid-cols-[200px_minmax(0,1fr)] gap-6 lg:gap-10 items-start">
        {/* Left column: table of contents + contact */}
        <nav aria-label="Table of contents" className="lg:sticky lg:top-6 flex flex-col gap-0.5 text-[13px]">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-[0.03em] px-2.5 pb-2">On this page</p>
          <ol className="list-none flex flex-col gap-0.5">
            {sections.map((section) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  onClick={(e) => handleScrollTo(e, section.id)}
                  aria-current={activeId === section.id ? 'location' : undefined}
                  className={`block px-2.5 py-1.5 rounded-md hover:bg-sand-300/60 hover:text-gray-900 ${
                    activeId === section.id ? 'bg-sand-300/60 text-gray-900 font-medium' : 'text-gray-700'
                  }`}
                >
                  {section.label}
                </a>
              </li>
            ))}
          </ol>
          <div className="mt-4 mx-2.5 pt-3.5 border-t border-sand-400 text-gray-500 flex flex-col gap-1">
            <span>Questions?</span>
            <a href={`mailto:${CONTACT_EMAIL}`} className="text-primary-600 hover:underline break-all">
              {CONTACT_EMAIL}
            </a>
          </div>
        </nav>

        {/* Right column: content card */}
        <div className="min-w-0 lg:max-w-[720px]">
          <section
            aria-label="Help and support"
            className="bg-white border border-sand-300 rounded-xl shadow-[0_1px_2px_rgba(60,40,10,0.05)] px-5 py-7 sm:px-11 sm:py-9 flex flex-col gap-9 text-[15px] leading-[1.65] text-gray-700"
          >
            <h1 className="text-[26px] font-semibold text-gray-900 tracking-tight">Help & Support</h1>

            <section id="what-is-cviche" className="scroll-mt-6">
              <h2 className={H2}>What is CViche?</h2>
              <div className="space-y-3">
                <p>
                  CViche is a tool that converts your CV into the standard Weill Cornell Medicine (WCM) institutional
                  format. Upload your CV as a Word document or PDF, and CViche uses AI to extract, organize, and reformat your
                  academic record into the correct structure.
                </p>
                <p>
                  CViche is designed for faculty and staff at WCM. Whether you are submitting your own CV or preparing one
                  on behalf of a faculty member, CViche handles the conversion so you do not have to reformat manually.
                </p>
                <p>
                  Behind the scenes, CViche reads your CV, identifies sections like publications, education, and
                  appointments, then organizes them into WCM's standard categories. It also enriches publication entries
                  with data from PubMed, including citation counts and correct journal formatting.
                </p>
              </div>
            </section>

            <section id="getting-started" className="scroll-mt-6">
              <h2 className={H2}>Getting Started</h2>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 mb-3">
                {gettingStartedTiles.map(([title, desc]) => (
                  <div key={title} className="bg-sand-50 rounded-[10px] px-3.5 py-3 text-[13px] leading-[1.45]">
                    <div className="font-semibold text-gray-900">{title}</div>
                    {desc}
                  </div>
                ))}
              </div>
              <div className="space-y-3">
                <p>
                  CViche accepts Word documents (.docx) and PDFs (.pdf). Text-based PDFs work best; scanned pages
                  can't be read. If your CV is in .doc or another format, convert it to .docx first using Microsoft
                  Word or Google Docs.
                </p>
                <p>
                  After uploading, you will see a real-time progress view showing each step of the pipeline as it
                  processes your document. Most CVs complete in 2 to 6 minutes depending on length and number of
                  publications. You don't need to keep the page open; processing continues on the server.
                </p>
                <p>
                  AI processing is covered by the Library{'—'}there is no charge to you.
                </p>
              </div>
            </section>

            <section id="understanding-results" className="scroll-mt-6">
              <h2 className={H2}>Understanding Your Results</h2>
              <div className="space-y-3">
                <p>
                  When processing completes, you will receive a Word document (.docx) formatted in the WCM institutional
                  CV template. Download this file from the Download card on the run page.
                </p>
                <p>
                  The CV Insights panel shows a summary of what CViche extracted: number of publications found, sections
                  identified, and any enrichment data added from PubMed. Review this to confirm your content was captured
                  correctly.
                </p>
                <p>
                  Duration is shown in the run header. It is the total wall-clock time from start to finish.
                </p>
                <p>
                  If something looks wrong in your results{'—'}missing publications, entries in the wrong section, or
                  formatting issues{'—'}use the feedback form on your run page to report the specific problem. This
                  helps us improve CViche for everyone.
                </p>
              </div>
            </section>

            {/* Data retention policy (Faculty Affairs, 2026-09). Linked from the upload attestation. */}
            <section id="data-retention" className="scroll-mt-6">
              <h2 className={H2}>Data Retention Policy</h2>
              <dl className="grid grid-cols-1 sm:grid-cols-[140px_minmax(0,1fr)] border-t border-sand-200">
                <dt className={DT}>What is retained</dt>
                <dd className={DD}>
                  For every run, CViche keeps the original CV as uploaded, the
                  intermediate outputs produced at each stage of processing (including the text exchanged with the AI
                  service), the final WCM-formatted document, the run record (who submitted it, when, and processing
                  cost), and any feedback submitted about the run.
                </dd>
                <dt className={DT}>How long</dt>
                <dd className={DD}>
                  Runs are retained indefinitely during the pilot. There is no automatic
                  expiry; removal is by request (see below).
                </dd>
                <dt className={DT}>Where</dt>
                <dd className={DD}>
                  All of this is stored on WCM-managed cloud infrastructure. The only material
                  that leaves WCM systems is the CV text sent to the AI service during processing, as described under
                  &ldquo;Is my data secure?&rdquo; below, and publication identifiers (such as DOIs and PubMed IDs) sent
                  to the National Library of Medicine&apos;s PubMed service to look up citation details.
                </dd>
                <dt className={DT}>Who can access it</dt>
                <dd className={DD}>
                  You can see your own runs. CViche administrators (the Library
                  development team) can see all runs. Access requires WCM single sign-on; there is no public or
                  anonymous access.
                </dd>
                <dt className={DT}>How it is used</dt>
                <dd className={DD}>
                  Retained runs are used to measure and improve CViche&apos;s accuracy
                  and to test proposed changes before they are released. CViche does not send your CV or its output to
                  any office or use it in any appointment, promotion, or review process; the output is a draft returned
                  to the person who uploaded it.
                </dd>
                <dt className={DT}>Removal</dt>
                <dd className={DD}>
                  To have a run removed, contact Paul Albert at {mailLink}. The original CV, intermediate outputs, and
                  final document are deleted from storage and the run record from the database. Deleted runs may remain
                  in routine system backups for a limited period.
                </dd>
                <dt className={DT}>Terms you agreed to</dt>
                <dd className={DD}>
                  The consent terms you accepted are always available on the{' '}
                  <Link to="/terms" className="text-primary-600 hover:underline">terms page</Link>.
                </dd>
              </dl>
            </section>

            <section id="faq" className="scroll-mt-6">
              <h2 className="text-[19px] font-semibold text-gray-900 mb-2">Frequently Asked Questions</h2>
              <div>
                {faqs.map((item, i) => {
                  const open = openFaq === i
                  return (
                    <div key={item.q} className="border-t border-sand-200">
                      <h3>
                        <button
                          type="button"
                          id={`faq-button-${i}`}
                          aria-expanded={open}
                          aria-controls={`faq-panel-${i}`}
                          onClick={() => setOpenFaq(open ? null : i)}
                          className="flex w-full items-center justify-between gap-3 py-3 text-left font-semibold text-gray-900 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-600"
                        >
                          {item.q}
                          <ChevronDown
                            aria-hidden="true"
                            className={`h-4 w-4 shrink-0 text-gray-500 transition-transform ${open ? 'rotate-180' : ''}`}
                          />
                        </button>
                      </h3>
                      <div
                        id={`faq-panel-${i}`}
                        role="region"
                        aria-labelledby={`faq-button-${i}`}
                        hidden={!open}
                      >
                        <p className="mb-3.5">{item.a}</p>
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>

            <section id="contact" className="scroll-mt-6">
              <h2 className={H2}>Contact & Support</h2>
              <div className="space-y-3">
                <p>
                  For issues with a specific run, use the feedback form on that run's page. The feedback form lets you
                  report exactly what went wrong so we can investigate and improve the results.
                </p>
                <p>
                  For general questions, suggestions, or anything else, contact Paul Albert at {mailLink}.
                </p>
              </div>
            </section>
          </section>
        </div>

        {/* Attribution */}
        <p className="lg:col-span-2 text-center text-xs text-gray-500">
          CViche is provided by the Library and Software Development Services
        </p>
      </div>
    </main>
  )
}
