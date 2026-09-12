import {
  addPolicyBinding,
  archivePolicy,
  createPolicyVersion,
  deletePolicyBinding,
  fetchPolicies,
  fetchPolicy,
  publishPolicy,
  savePolicy,
} from '../../api.js'

export const policiesApi = {
  list: () => fetchPolicies(),
  get: (id: string | number) => fetchPolicy(id),
  save: (policy: Record<string, unknown>, isNew = false) => savePolicy(policy, isNew),
  publish: (id: string | number) => publishPolicy(id),
  archive: (id: string | number) => archivePolicy(id),
  createVersion: (id: string | number, payload: Record<string, unknown>) => createPolicyVersion(id, payload),
  addBinding: (id: string | number, binding: Record<string, unknown>) => addPolicyBinding(id, binding),
  deleteBinding: (id: string | number, bindingId: string | number) => deletePolicyBinding(id, bindingId),
}
