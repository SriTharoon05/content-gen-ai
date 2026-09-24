import { useEffect, useState } from 'react'
import { formatGenerationTime } from '../generationTime.js'

export default function GenerationTime({ timing }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (timing?.status !== 'running') return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [timing?.status, timing?.started_at])
  return <span title="From submission to finished generation, including queueing, provider waits, retries, rendering and storage. Excludes review time and publishing.">{formatGenerationTime(timing, now)}</span>
}
