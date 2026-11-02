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
  admission?: { can_use_for_draft: boolean; status: string }
}

export interface WorkspaceOverview {
  packs: string[]
  templates: TemplateSummary[]
  projects: ProjectSummary[]
  deletedProjects: ProjectSummary[]
}

export interface WritingSpecificationEvidence {
  node_id: string
  part: string
  path: string
  role: string
  region: string
  text: string
  properties: Record<string, unknown>
  property_sources: Record<string, string[]>
  details: Record<string, unknown>
}

export interface WritingSpecificationRule {
  id: string
  scope: Record<string, string>
  property: string
  expected: unknown
  evidence_kind: 'observed_pattern' | 'single_observation' | 'explicit_instruction'
  confidence: 'consistent' | 'single' | 'conflict' | 'needs_review'
  evidence_node_ids: string[]
  supporting_count: number
  observed_count: number
  alternatives: Record<string, unknown>[]
  decision: 'pending'
  enforcement: 'suggestion'
}

export interface WritingSpecification {
  schema_version: string
  extractor_version: string
  source_sha256: string
  specification_sha256: string
  status: 'needs_confirmation'
  nodes: WritingSpecificationEvidence[]
  rules: WritingSpecificationRule[]
  review_items: { code: string; message: string; node_ids: string[]; rule_ids: string[] }[]
  coverage: Record<string, unknown>
}

export interface WritingSpecificationResponse {
  template_id: string
  version: string
  specification: WritingSpecification
  markdown: string
}
