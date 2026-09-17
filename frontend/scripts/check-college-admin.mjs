import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(new URL('..', import.meta.url).pathname)
const required = [
  'src/views/Workflow.vue',
  'src/views/Organizations.vue',
  'src/views/Login.vue',
  'src/views/FirstLogin.vue',
  'src/views/NotFound.vue',
  'src/components/AppErrorBoundary.vue',
  'src/shared/auth/permissions.ts',
  'src/router.js',
  'tests/Workflow.reports.test.js',
  'tests/Organizations.test.js',
  'tests/permissions.test.js',
  'tests/AppErrorBoundary.test.js',
]

const missing = required.filter((file) => !existsSync(resolve(root, file)))
if (missing.length) {
  console.error(`Missing college-admin files: ${missing.join(', ')}`)
  process.exit(1)
}

const workflow = readFileSync(resolve(root, 'src/views/Workflow.vue'), 'utf8')
for (const token of ['function rate(value)', 'report.metrics?.on_time_first_submission_rate', 'archive_completion_rate']) {
  if (!workflow.includes(token)) {
    console.error(`Missing workflow report token: ${token}`)
    process.exit(1)
  }
}

const organizations = readFileSync(resolve(root, 'src/views/Organizations.vue'), 'utf8')
if (!organizations.includes('!unit.parent_id || !ids.has(unit.parent_id)')) {
  console.error('Organizations tree must start from visible roots when school is out of scope')
  process.exit(1)
}

const permissions = readFileSync(resolve(root, 'src/shared/auth/permissions.ts'), 'utf8')
if (!permissions.includes('export function homePath') || !permissions.includes("return '/workspace'")) {
  console.error('homePath must send college_admin / admin / teacher to /workspace')
  process.exit(1)
}

const login = readFileSync(resolve(root, 'src/views/Login.vue'), 'utf8')
const firstLogin = readFileSync(resolve(root, 'src/views/FirstLogin.vue'), 'utf8')
const router = readFileSync(resolve(root, 'src/router.js'), 'utf8')
const errorBoundary = readFileSync(resolve(root, 'src/components/AppErrorBoundary.vue'), 'utf8')
const notFound = readFileSync(resolve(root, 'src/views/NotFound.vue'), 'utf8')
if (!login.includes('homePath(data.user)')) {
  console.error('Login must land college admins via homePath')
  process.exit(1)
}
if (!firstLogin.includes('homePath(result.user)')) {
  console.error('FirstLogin must land via homePath')
  process.exit(1)
}
if (!router.includes('homePath(auth.user)')) {
  console.error('Router must use homePath for authenticated redirects')
  process.exit(1)
}
if (!errorBoundary.includes('homePath(auth.user)')) {
  console.error('Error boundary must return college admins to homePath')
  process.exit(1)
}
if (!notFound.includes('homePath(auth.user)')) {
  console.error('NotFound must use homePath')
  process.exit(1)
}

const repoRoot = resolve(root, '..')
const policies = readFileSync(resolve(repoRoot, 'backend/app/api/policies.py'), 'utf8')
if (!policies.includes('from app.services.access_service import visible_organization_ids')) {
  console.error('policies.py must reuse visible_organization_ids for college_admin')
  process.exit(1)
}
if (!policies.includes('return visible_organization_ids(store, user)')) {
  console.error('college_admin policy scope must call visible_organization_ids')
  process.exit(1)
}

const policyTest = readFileSync(resolve(repoRoot, 'backend/tests/test_policy_lifecycle.py'), 'utf8')
for (const token of ['sibling_college_id', 'assert outside.status_code == 403', 'school_binding']) {
  if (!policyTest.includes(token)) {
    console.error(`Missing college policy isolation token: ${token}`)
    process.exit(1)
  }
}

console.log('College-admin desktop productization structure OK')
