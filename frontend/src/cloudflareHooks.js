import {useEffect,useRef} from 'react'

// Keep dashboard reads serial and pause them in hidden tabs. Opening a tab refreshes
// immediately, instead of issuing overlapping requests on a slow DB connection.
export function usePolling(callback, interval = 10000, enabled = true) {
  const latest = useRef(callback)
  latest.current = callback
  useEffect(() => {
    if (!enabled) return
    let timer, stopped = false, running = false
    const poll = async () => {
      clearTimeout(timer)
      if (stopped || running || document.hidden) return
      running = true
      try { await latest.current() }
      finally {
        running = false
        if (!stopped && !document.hidden) timer = setTimeout(poll, interval)
      }
    }
    const visibility = () => { clearTimeout(timer); if (!document.hidden) poll() }
    poll()
    document.addEventListener('visibilitychange', visibility)
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visibility) }
  }, [interval, enabled])
}

export function useDraftGuard(dirty, onDirty) {
  useEffect(() => {
    onDirty?.(dirty)
    const guard = event => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', guard)
    return () => { onDirty?.(false); window.removeEventListener('beforeunload', guard) }
  }, [dirty, onDirty])
}
