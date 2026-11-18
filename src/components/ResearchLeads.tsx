import type { ResearchLead } from '@/types/legal'

export function ResearchLeads({ leads }: { leads: ResearchLead[] }) {
  const sources = new Map<string, { lead: ResearchLead; schools: Set<string> }>()
  for (const lead of leads) {
    if (!/^https?:\/\//i.test(lead.source_url)) continue
    const source = sources.get(lead.source_url)
    if (source) {
      source.schools.add(lead.search_institution)
    } else {
      sources.set(lead.source_url, { lead, schools: new Set([lead.search_institution]) })
    }
  }
  if (!sources.size) return null

  return (
    <details className="verification-details research-leads">
      <summary>待核线索 · {sources.size} 份相关网页</summary>
      <p className="research-leads-warning">已读取网页正文，但尚无法核实作者与检索院校的任职关系。以下摘录仅供查阅原网页，不计入四校命中，也不作为本回答的引用依据。</p>
      <ul>
        {Array.from(sources.values(), ({ lead, schools }) => (
          <li key={lead.source_url}>
            <a href={lead.source_url} target="_blank" rel="noreferrer noopener">{lead.title}</a>
            <p>检索范围 · {Array.from(schools).join('、')}</p>
            <blockquote>{lead.excerpt}</blockquote>
          </li>
        ))}
      </ul>
    </details>
  )
}
