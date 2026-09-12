import { useQuery } from '@tanstack/vue-query'
import { fetchWorkspaceOverview } from './api'

export function useWorkspaceOverview() {
  return useQuery({
    queryKey: ['workspace', 'overview'],
    queryFn: fetchWorkspaceOverview,
  })
}
