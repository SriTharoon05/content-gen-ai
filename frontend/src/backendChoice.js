const env = import.meta.env || {}
const defaults = {
  render: (env.VITE_RENDER_API_BASE || env.VITE_API_BASE || '/api').replace(/\/$/, ''),
  cloudflare: (env.VITE_CLOUDFLARE_API_BASE || 'https://story-shorts-cloudflare-pilot.storyshort.workers.dev/api').replace(/\/$/, ''),
}
export const selectedBackend = () => localStorage.getItem('storyshorts.backend') === 'cloudflare' ? 'cloudflare' : 'render'
export const backendBase = (backend = selectedBackend()) => defaults[backend]
export const backendToken = (backend = selectedBackend()) => localStorage.getItem(backend === 'render' ? 'storyshorts.token' : 'storyshorts.cloudflare.token') || ''
export function saveBackend(backend, token) {
  if (!['render', 'cloudflare'].includes(backend)) throw new Error('Invalid backend')
  localStorage.setItem(backend === 'render' ? 'storyshorts.token' : 'storyshorts.cloudflare.token', token)
  localStorage.setItem('storyshorts.backend', backend)
}
