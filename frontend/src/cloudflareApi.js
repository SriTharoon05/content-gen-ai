import { backendBase, backendToken } from './backendChoice.js'

export async function cloudflareRequest(path, options = {}) {
  const multipart = options.body instanceof FormData
  const response = await fetch(backendBase('cloudflare') + path, {
    ...options,
    signal: options.signal || AbortSignal.timeout(60000),
    headers: {
      Authorization: `Bearer ${backendToken('cloudflare')}`,
      ...(!multipart ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })
  const text = await response.text()
  let data
  try { data = text ? JSON.parse(text) : {} } catch { throw new Error(`Backend returned an invalid response (HTTP ${response.status})`) }
  if (!response.ok) {
    const message = data.detail || data.error || `HTTP ${response.status}`
    const error = new Error(typeof message === 'string' ? message : JSON.stringify(message))
    error.status = response.status
    throw error
  }
  return data
}

export const postCloudflare = (path, body = {}, options = {}) => cloudflareRequest(path, { ...options, method: 'POST', body: JSON.stringify(body) })

export function publicationLocked(status) {
  // An uncertain upload must never become a one-click duplicate upload.
  return !!status && !['blocked', 'failed', 'remote_failed'].includes(status)
}

export function musicCropValid(start, end, duration) {
  return Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end - start >= 3 && (!duration || end <= duration + 0.1)
}

export function safeMediaUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || (url.protocol === 'http:' && ['localhost','127.0.0.1'].includes(url.hostname)) ? url.href : ''
  } catch { return '' }
}
