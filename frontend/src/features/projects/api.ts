import {
  createProject as legacyCreateProject,
  deleteProject as legacyDeleteProject,
  exportProject as legacyExportProject,
  fetchDeletedProjects as legacyFetchDeletedProjects,
  fetchProject as legacyFetchProject,
  fetchProjects as legacyFetchProjects,
  restoreDeletedProject as legacyRestoreDeletedProject,
} from '../../api.js'
import type { ProjectSummary } from '../../shared/api/types'

export const projectsApi = {
  list: () => legacyFetchProjects() as Promise<ProjectSummary[]>,
  listDeleted: () => legacyFetchDeletedProjects() as Promise<ProjectSummary[]>,
  get: (id: string | number) => legacyFetchProject(id) as Promise<Record<string, unknown>>,
  create: (templateId: string | number, title = '') => legacyCreateProject(templateId, title) as Promise<ProjectSummary>,
  remove: (id: string | number) => legacyDeleteProject(id),
  restore: (id: string | number) => legacyRestoreDeletedProject(id),
  export: (id: string | number, force = false) => legacyExportProject(id, force),
}
