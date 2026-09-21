import { useRef } from 'react'
export default function CropAudio({ src, start=0, end=0 }) {
  const player=useRef(null)
  const play=()=>{ if(player.current){player.current.currentTime=Math.max(0,Number(start)||0);player.current.play().catch(()=>{})} }
  return <div style={{margin:'12px 0'}}>
    <audio key={src} ref={player} controls preload="metadata" src={src}
      onPlay={e=>{const a=e.currentTarget;if(a.currentTime<start || (end && a.currentTime>=end)) a.currentTime=start}}
      onTimeUpdate={e=>{const a=e.currentTarget;if(end && a.currentTime>=end){a.pause();a.currentTime=start}}}
      onSeeked={e=>{const a=e.currentTarget;if(a.currentTime<start)a.currentTime=start;if(end && a.currentTime>end){a.pause();a.currentTime=start}}}/>
    <button className="btn small" type="button" onClick={play}>▶ Preview selected crop</button>
    <span className="dim tiny"> {Number(start).toFixed(1)}s – {Number(end).toFixed(1)}s</span>
  </div>
}
