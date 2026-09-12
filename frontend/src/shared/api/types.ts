export interface ProjectSummary {
  id: string | number
  title?: string
  template_name?: string
  done_count?: number
  required_count?: number
  total_chars?: number
  progress?: number
  progress_pct?: number
  updated_at?: string
}

export interface TemplateSummary {
  id: string | number
  name: string
  description?: string
  version?: string | number
}

export interface WorkspaceOverview {
  packs: string[]
  templates: TemplateSummary[]
  projects: ProjectSummary[]
  deletedProjects: ProjectSummary[]
}
