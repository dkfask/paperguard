import type { RouteLocationNormalized } from 'vue-router'
import type { User } from '../../stores/auth'

export interface PermissionResolver {
  can(action: string, resource: string): boolean
}

const rolePermissions: Record<string, Set<string>> = {
  admin: new Set(['manage:users', 'manage:ai', 'manage:templates', 'manage:policies', 'manage:workflow', 'read:audit', 'read:operations']),
  teacher: new Set(['manage:templates', 'manage:policies', 'manage:workflow']),
  student: new Set(['write:projects', 'read:workflow']),
}

export function createPermissionResolver(user: User | null | undefined): PermissionResolver {
  const permissions = user?.status === 'pending' ? new Set<string>() : (rolePermissions[user?.role || ''] || new Set<string>())
  return {
    can(action: string, resource: string) {
      return permissions.has(`${action}:${resource}`)
    },
  }
}

export function canAccessRoute(route: Pick<RouteLocationNormalized, 'meta'>, user: User | null | undefined) {
  if (route.meta.public) return true
  if (user?.role === 'user') return Boolean(route.meta.certification)
  if (!isBusinessRole(user)) return false
  const roles = (route.meta as { roles?: string[] }).roles
  if (!roles || roles.length === 0) return true
  return Boolean(user && roles.includes(user.role))
}

export function hasAnyRole(user: User | null | undefined, roles: string[]) {
  return Boolean(isBusinessRole(user) && user && roles.includes(user.role))
}

export function isBusinessRole(user: User | null | undefined) {
  return Boolean(user && ['admin', 'teacher', 'student'].includes(user.role))
}

export function homePath(user: User | null | undefined) {
  if (user?.role === 'user') return '/role-certification'
  if (user && !isBusinessRole(user)) return '/login'
  if (user && ['admin', 'teacher'].includes(user.role)) {
    return '/workspace'
  }
  return '/'
}
