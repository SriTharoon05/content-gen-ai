export function formatGenerationTime(timing, now = Date.now()) {
  if (!timing) return 'Not recorded'
  const seconds = timing.status === 'running'
    ? (now - Date.parse(timing.started_at)) / 1000 : timing.elapsed_seconds
  if (!Number.isFinite(seconds)) return 'Not recorded'
  const total = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor(total % 3600 / 60)
  const duration = `${hours ? `${hours}h ` : ''}${minutes}m ${total % 60}s`
  return `${duration}${timing.status === 'running' ? ' · elapsed' : timing.status === 'failed' ? ' · failed' : ''}`
}
