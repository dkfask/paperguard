import { fetchTemplate as legacyFetchTemplate, fetchTemplates as legacyFetchTemplates } from '../../api.js'
import type { TemplateSummary } from '../../shared/api/types'

export const templatesApi = {
  list: () => legacyFetchTemplates() as Promise<TemplateSummary[]>,
  get: (id: string | number) => legacyFetchTemplate(id) as Promise<Record<string, unknown>>,
}
