import { Link } from 'react-router-dom'
import { HelpCircle, ArrowLeft } from 'lucide-react'

const sections = [
  { id: 'what-is-cviche', label: 'What is CViche?' },
  { id: 'getting-started', label: 'Getting Started' },
  { id: 'understanding-results', label: 'Understanding Your Results' },
  { id: 'faq', label: 'Frequently Asked Questions' },
  { id: 'contact', label: 'Contact & Support' },
]

const handleScrollTo = (e: React.MouseEvent, id: string) => {
  e.preventDefault()
  const el = document.getElementById(id)
  if (el) {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: prefersReducedMotion ? 'auto' : 'smooth', block: 'start' })
  }
}

export default function HelpPage() {
  return (
    <main
      className="flex items-start justify-center min-h-screen p-4 pt-12"
      style={{
        backgroundImage: 'url(/headerbg.png)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat',
      }}
    >
      <div className="w-full max-w-2xl">
        {/* Logo */}
        <div className="flex justify-center mb-6">
          <img
            src="/header-logo.png"
            alt="CViche"
            className="h-16 object-contain"
          />
        </div>

        <section aria-label="Help and support" className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
          {/* Back navigation */}
          <nav aria-label="Back navigation" className="mb-6">
            <Link
              to="/"
              className="inline-flex items-center gap-1.5 text-sm text-primary-600 hover:text-primary-700 hover:underline"
            >
              <ArrowLeft className="h-4 w-4" />
              Back to upload
            </Link>
          </nav>

          {/* Page title */}
          <div className="flex items-center gap-2 mb-6">
            <HelpCircle className="h-6 w-6 text-primary-600" />
            <h1 className="text-xl font-semibold text-gray-900">Help & Support</h1>
          </div>

          {/* Table of contents */}
          <nav aria-label="Table of contents" className="bg-gray-50 rounded-lg p-4 mb-8">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">On this page</p>
            <ol className="list-none space-y-1">
              {sections.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    onClick={(e) => handleScrollTo(e, section.id)}
                    className="text-sm text-primary-600 hover:underline"
                  >
                    {section.label}
                  </a>
                </li>
              ))}
            </ol>
          </nav>

          {/* Section 1: What is CViche? */}
          <section id="what-is-cviche">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">What is CViche?</h2>
            <div className="space-y-3 text-sm text-gray-700 leading-relaxed">
              <p>
                CViche is a tool that converts your CV into the standard Weill Cornell Medicine (WCM) institutional
                format. Upload your CV as a Word document, and CViche uses AI to extract, organize, and reformat your
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

          <hr className="border-t border-gray-200 my-6" />

          {/* Section 2: Getting Started */}
          <section id="getting-started">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Getting Started</h2>
            <div className="space-y-3 text-sm text-gray-700 leading-relaxed">
              <p>
                CViche accepts Word documents (.docx format). If your CV is in PDF or another format, convert it to
                .docx first using Microsoft Word or Google Docs.
              </p>
              <p>
                After uploading, you will see a real-time progress view showing each step of the pipeline as it
                processes your document. Most CVs complete in 3 to 8 minutes depending on length and number of
                publications.
              </p>
              <p>
                The estimated cost per run is displayed before you confirm the upload. This is the AI processing cost
                only and is covered by the CViche pilot program{'\u2014'}there is no charge to you.
              </p>
            </div>
          </section>

          <hr className="border-t border-gray-200 my-6" />

          {/* Section 3: Understanding Your Results */}
          <section id="understanding-results">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Understanding Your Results</h2>
            <div className="space-y-3 text-sm text-gray-700 leading-relaxed">
              <p>
                When processing completes, you will receive a Word document (.docx) formatted in the WCM institutional
                CV template. Download this file from the Output Files section of your run page.
              </p>
              <p>
                The CV Insights panel shows a summary of what CViche extracted: number of publications found, sections
                identified, and any enrichment data added from PubMed. Review this to confirm your content was captured
                correctly.
              </p>
              <p>
                Duration and cost are shown in the run header. Duration is the total wall-clock time from start to
                finish. Cost is the AI processing cost for that run.
              </p>
              <p>
                If something looks wrong in your results{'\u2014'}missing publications, entries in the wrong section, or
                formatting issues{'\u2014'}use the feedback form on your run page to report the specific problem. This
                helps us improve CViche for everyone.
              </p>
            </div>
          </section>

          <hr className="border-t border-gray-200 my-6" />

          {/* Section 4: FAQ */}
          <section id="faq">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Frequently Asked Questions</h2>
            <div className="space-y-4 text-sm text-gray-700 leading-relaxed">
              <div>
                <p className="text-sm font-semibold text-gray-900 mb-1">What file formats does CViche accept?</p>
                <p>
                  CViche accepts .docx (Microsoft Word) files only. If your CV is in PDF, .doc, or another format,
                  please convert it to .docx before uploading.
                </p>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-900 mb-1">How long does processing take?</p>
                <p>
                  Most CVs are processed in 3 to 8 minutes. Longer CVs with many publications may take up to 15
                  minutes. You can watch the progress in real time on the run page.
                </p>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-900 mb-1">Does it cost me anything?</p>
                <p>
                  No. The AI processing costs are covered by the CViche pilot program. The cost shown on your run page
                  is for informational purposes only.
                </p>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-900 mb-1">Can I re-run a CV?</p>
                <p>
                  Yes. Upload the same file again from the main page. Each upload creates a new run. Your previous runs
                  are saved in the run history table below the upload area.
                </p>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-900 mb-1">Is my data secure?</p>
                <p>
                  Your CV is processed on secure servers and is only accessible to you and CViche administrators. Files
                  are not shared with third parties. The AI processing uses OpenAI's API, which does not use your data
                  for training.
                </p>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-900 mb-1">What if something looks wrong in my results?</p>
                <p>
                  Use the feedback form on your completed run page to report the specific issue. For general questions or
                  urgent concerns, email Paul Albert at{' '}
                  <a href="mailto:paa2013@med.cornell.edu" className="text-primary-600 hover:underline">
                    paa2013@med.cornell.edu
                  </a>
                  .
                </p>
              </div>
            </div>
          </section>

          <hr className="border-t border-gray-200 my-6" />

          {/* Section 5: Contact & Support */}
          <section id="contact">
            <h2 className="text-lg font-semibold text-gray-900 mb-4">Contact & Support</h2>
            <div className="space-y-3 text-sm text-gray-700 leading-relaxed">
              <p>
                For issues with a specific run, use the feedback form on that run's page. The feedback form lets you
                report exactly what went wrong so we can investigate and improve the results.
              </p>
              <p>
                For general questions, suggestions, or anything else, contact Paul Albert at{' '}
                <a href="mailto:paa2013@med.cornell.edu" className="text-primary-600 hover:underline">
                  paa2013@med.cornell.edu
                </a>
                .
              </p>
            </div>
          </section>
        </section>

        {/* Attribution */}
        <p className="text-center text-xs text-gray-500 mt-4">
          CViche is provided by the Library and Software Development Services
        </p>
      </div>
    </main>
  )
}
