import { api as legacyFetch, responseError } from '../../api/client.js'
import { translate } from '../../i18n.js'

export interface RequestConfig extends Omit<RequestInit, 'body'> {
  url: string
  body?: BodyInit | object | null
  responseType?: 'json' | 'blob' | 'text'
  authRequired?: boolean
  includeRequestId?: boolean
}

export class ApiError extends Error {
  status?: number
  requestId?: string

  constructor(message: string, status?: number, requestId?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.requestId = requestId
  }
}

function normalizeHeaders(inputHeaders: HeadersInit | undefined): Record<string, string> {
  if (!inputHeaders) return {}
  if (inputHeaders instanceof Headers) {
    const headers: Record<string, string> = {}
    inputHeaders.forEach((value, key) => { headers[key] = value })
    return headers
  }
  if (Array.isArray(inputHeaders)) return Object.fromEntries(inputHeaders)
  return { ...inputHeaders } as Record<string, string>
}

function normalizeBody(body: RequestConfig['body'], headers: Record<string, string>) {
  if (!body || typeof body !== 'object' || body instanceof Blob || body instanceof FormData || body instanceof URLSearchParams) {
    return body as BodyInit | null | undefined
  }
  const hasContentType = Object.keys(headers).some((key) => key.toLowerCase() === 'content-type')
  if (!hasContentType) headers['Content-Type'] = 'application/json'
  return JSON.stringify(body)
}

export async function request<T>({ url, body, responseType = 'json', authRequired = true, includeRequestId = true, headers: inputHeaders, ...init }: RequestConfig): Promise<T> {
  const headers = normalizeHeaders(inputHeaders)
  const options = {
    ...init,
    // The legacy client clones headers with object spread, so pass a plain map.
    headers,
    body: normalizeBody(body, headers),
  }
  const response = authRequired
    ? await legacyFetch(url, options, { includeRequestId })
    : await fetch(url, options)

  if (!response.ok) {
    const error = await responseError(response, translate('common.operationFailed'), {
      includeRequestId,
    }) as Error & { status?: number; requestId?: string }
    throw new ApiError(error.message, error.status, error.requestId)
  }

  if (response.status === 204) return undefined as T
  if (responseType === 'blob') return await response.blob() as T
  if (responseType === 'text') return await response.text() as T
  return await response.json() as T
}

export const apiClient = { request }
