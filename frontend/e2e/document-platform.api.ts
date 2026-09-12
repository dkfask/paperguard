import { randomBytes } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { basename, resolve } from 'node:path'
import type { APIRequestContext } from '@playwright/test'

const backendPort = process.env.E2E_BACKEND_PORT ?? '8000'
export const API_BASE_URL = process.env.E2E_API_BASE_URL ?? `http://127.0.0.1:${backendPort}`
export const TEMPLATE_PATH = process.env.E2E_TEMPLATE_PATH
  ?? resolve(process.cwd(), '../backend/tests/fixtures/templates/minimal.docx')
export const E2E_ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? ''

function markdownSpec(specName: string) {
  return `---
name: ${specName}
version: '1'
description: 浏览器测试使用的确定性规范
rules:
  - id: body-minimum
    type: auto
    target: body
    checker: word_count
    params:
      min: 1
    severity: warning
    enforce: soft
    description: 正文至少包含 1 个字符
    source: E2E fixture
---

# E2E 通用报告规范

正文必须包含内容。
`
}

export type PublishedPackage = {
  packageId: string
  templateName: string
  specName: string
}

export type StudentAccount = {
  username: string
  password: string
  projectTitle: string
}

async function jsonOrThrow(response: Awaited<ReturnType<APIRequestContext['get']>>, label: string) {
  if (!response.ok()) {
    throw new Error(`${label}: ${response.status()} ${await response.text()}`)
  }
  return response.json()
}

export async function publishFixturePackage(api: APIRequestContext): Promise<PublishedPackage> {
  if (!E2E_ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')
  const specName = `E2E 通用报告规范 ${Date.now()}-${randomBytes(4).toString('hex')}`

  const loginResponse = await api.post('/api/auth/login', {
    data: { username: 'admin', password: E2E_ADMIN_PASSWORD },
  })
  const login = await jsonOrThrow(loginResponse, 'admin login')
  const headers = { Authorization: `Bearer ${login.token}` }
  const templateFile = readFileSync(TEMPLATE_PATH)
  const templateResponse = await api.post('/api/templates/parse', {
    headers,
    multipart: {
      file: {
        name: basename(TEMPLATE_PATH),
        mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        buffer: templateFile,
      },
    },
  })
  const templateCandidate = await jsonOrThrow(templateResponse, 'parse DOCX template')

  const specResponse = await api.post('/api/specs/parse', {
    headers,
    multipart: {
      file: {
        name: 'e2e-spec.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from(markdownSpec(specName), 'utf8'),
      },
    },
  })
  const specCandidate = await jsonOrThrow(specResponse, 'parse deterministic specification')

  const confirmedTemplateResponse = await api.post('/api/templates/parse/confirm', {
    headers,
    data: templateCandidate,
  })
  const confirmedTemplate = await jsonOrThrow(confirmedTemplateResponse, 'confirm DOCX template')
  const confirmedSpecResponse = await api.post('/api/specs/parse/confirm', {
    headers,
    data: specCandidate,
  })
  const confirmedSpec = await jsonOrThrow(confirmedSpecResponse, 'confirm specification')

  const packageResponse = await api.post('/api/document-packages', {
    headers,
    data: {
      template_candidate: confirmedTemplate,
      spec_candidate: confirmedSpec,
      document_type_id: 'e2e-report',
      renderer_version: 'generic-docx-v1',
      rule_engine_version: 'generic-check-v1',
    },
  })
  const bundle = await jsonOrThrow(packageResponse, 'publish document package')
  return {
    packageId: bundle.package.id,
    templateName: bundle.template.name,
    specName,
  }
}

export async function createStudent(api: APIRequestContext, testName: string): Promise<StudentAccount> {
  const suffix = `${Date.now()}-${randomBytes(5).toString('hex')}`
  const username = `e2e-${testName.replace(/[^a-z0-9]+/gi, '-').slice(0, 24)}-${suffix}`
  const password = `E2e7${randomBytes(18).toString('hex')}`
  const projectTitle = `E2E ${testName} ${suffix}`
  const response = await api.post('/api/auth/register', {
    data: { username, password, display_name: username, role: 'student' },
  })
  await jsonOrThrow(response, 'register isolated student')
  return { username, password, projectTitle }
}
