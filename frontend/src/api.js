const BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/$/, '')

export function getToken() {
  return localStorage.getItem('storyshorts.token') || ''
}
export function setToken(value) {
  if (value) localStorage.setItem('storyshorts.token', value)
  else localStorage.removeItem('storyshorts.token')
}

async function request(path, { method = 'GET', body, raw } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body && !raw) headers['Content-Type'] = 'application/json'

  let response
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: raw ? body : body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error('Cannot reach the backend. Is uvicorn running on port 8000?')
  }

  if (response.status === 204) return null
  const text = await response.text()
  let payload = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = { detail: text }
  }
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(
      typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `Request failed (${response.status})`,
    )
  }
  return payload
}

export const api = {
  integrations: () => request('/integrations'),
  connectYoutube: (slug) => request(`/channels/${slug}/youtube/connect`, { method: 'POST' }),
  uploadFlow: (id) => request(`/videos/${id}/upload-flow`, { method: 'POST' }),
  verifyYoutube: (id) => request(`/videos/${id}/youtube/verify`, { method: 'POST' }),
  analytics: (slug) => request(`/channels/${slug}/analytics`),
  publish: (id, platform) => request(`/videos/${id}/publish/${platform}`, { method: 'POST' }),
  health: () => request('/health'),
  costs: () => request('/costs'),
  options: () => request('/options'),
  registry: () => request('/registry'),

  settings: () => request('/settings'),
  saveSettings: (settings) => request('/settings', { method: 'PUT', body: { settings } }),

  channels: () => request('/channels'),
  createChannel: (body) => request('/channels', {method:'POST',body}),
  patchChannel: (slug, patch) => request(`/channels/${slug}`, { method: 'PUT', body: patch }),
  memory: (slug) => request(`/channels/${slug}/memory`),
  addMemory: (slug, entry) => request(`/channels/${slug}/memory`, { method: 'POST', body: entry }),
  deleteMemory: (id) => request(`/memory/${id}`, { method: 'DELETE' }),

  runChannel: (slug, options) => request(`/channels/${slug}/run`, { method: 'POST', body: options }),
  runAll: (options) => request('/run-all', { method: 'POST', body: options }),
  scheduleNow: () => request('/schedule/run-now', { method: 'POST' }),

  videos: (query = '') => request(`/videos${query}`),
  video: (id) => request(`/videos/${id}`),
  approve: (id) => request(`/videos/${id}/approve`, { method: 'POST' }),
  previewReviewed: (id, revision) => request(`/videos/${id}/preview-reviewed`, {method:'POST',body:{output_revision:revision}}),
  deleteVideo: (id) => request(`/videos/${id}`, { method: 'DELETE' }),
  regenerate: (id, what, payload) =>
    request(`/videos/${id}/regenerate/${what}`, { method: 'POST', body: payload || {} }),

  music: () => request('/music'),
  uploadMusic: (formData) => request('/music', { method: 'POST', body: formData, raw: true }),
  patchMusic: (id, patch) => request(`/music/${id}`, { method: 'PUT', body: patch }),
  deleteMusic: (id) => request(`/music/${id}`, { method: 'DELETE' }),

  jobs: (query = '') => request(`/jobs${query}`),
  retryJob: (id) => request(`/jobs/${id}/retry`, { method: 'POST' }),
  cancelJob: (id) => request(`/jobs/${id}/cancel`, { method: 'POST' }),
}

export const mediaUrl = {
  narration: (id) => `${BASE}/videos/${id}/narration`,
  video: (id) => `${BASE}/videos/${id}/stream`,
  download: (id) => `${BASE}/videos/${id}/download`,
  shot: (id, shotId) => `${BASE}/videos/${id}/shots/${shotId}/image`,
  music: (id) => `${BASE}/music/${id}/preview`,
}

export function money(value, places = 4) {
  if (!value) return `$0.${'0'.repeat(places)} / ₹0`
  return `$${Number(value.usd || 0).toFixed(places)} / ₹${Number(value.inr || 0).toFixed(2)}`
}

export function moneyShort(value) {
  if (!value) return '$0 / ₹0'
  const usd = Number(value.usd || 0)
  return `$${usd < 1 ? usd.toFixed(3) : usd.toFixed(2)} / ₹${Number(value.inr || 0).toFixed(0)}`
}

/** A stored key is sent back as an opaque fingerprint; the UI shows its shape, never its value. */
export function describeKey(fingerprint) {
  if (!fingerprint) return null
  const [, length, tail] = String(fingerprint).split(':')
  return { length: Number(length) || 0, tail: tail || '****' }
}

export const isStoredKey = (value) => typeof value === 'string' && value.startsWith('__keep__')
