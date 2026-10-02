/**
 * Simple markdown-to-HTML renderer for the consent text.
 * Handles headings (## / ###), bold (**text**), numbered lists, bullet lists,
 * and paragraphs. No external dependency needed for this limited subset.
 */
export default function ConsentTextRenderer({ text }: { text: string }) {
  const lines = text.split('\n')
  const elements: React.ReactNode[] = []
  let key = 0

  const renderInline = (line: string): React.ReactNode => {
    // Bold: **text**
    const parts = line.split(/(\*\*[^*]+\*\*)/)
    return parts.map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      return part
    })
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // Skip empty lines
    if (line.trim() === '') {
      i++
      continue
    }

    // H1: # Heading
    if (line.startsWith('# ') && !line.startsWith('## ')) {
      elements.push(<h2 key={key++} className="text-lg font-bold text-gray-900 mb-2 mt-4 first:mt-0">{renderInline(line.slice(2))}</h2>)
      i++
      continue
    }

    // H2: ## Heading
    if (line.startsWith('## ')) {
      elements.push(<h3 key={key++} className="text-base font-bold text-gray-900 mb-2 mt-4">{renderInline(line.slice(3))}</h3>)
      i++
      continue
    }

    // H3: ### Heading
    if (line.startsWith('### ')) {
      elements.push(<h4 key={key++} className="text-sm font-bold text-gray-900 mb-1 mt-3">{renderInline(line.slice(4))}</h4>)
      i++
      continue
    }

    // Numbered list: 1. item
    if (/^\d+\.\s/.test(line.trim())) {
      const items: React.ReactNode[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i].trim())) {
        items.push(<li key={key++}>{renderInline(lines[i].trim().replace(/^\d+\.\s/, ''))}</li>)
        i++
      }
      elements.push(<ol key={key++} className="list-decimal list-inside space-y-1 mb-3 text-gray-700">{items}</ol>)
      continue
    }

    // Bullet list: - item
    if (line.trim().startsWith('- ')) {
      const items: React.ReactNode[] = []
      while (i < lines.length && lines[i].trim().startsWith('- ')) {
        items.push(<li key={key++}>{renderInline(lines[i].trim().slice(2))}</li>)
        i++
      }
      elements.push(<ul key={key++} className="list-disc list-inside space-y-1 mb-3 text-gray-700">{items}</ul>)
      continue
    }

    // Paragraph
    elements.push(<p key={key++} className="mb-3 text-gray-700">{renderInline(line)}</p>)
    i++
  }

  return <>{elements}</>
}
