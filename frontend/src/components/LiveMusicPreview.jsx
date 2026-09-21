import { useEffect, useRef, useState } from 'react'
import { mediaUrl } from '../api'

// Decoded audio and the live mix stay in the browser; the backend serves saved assets only.
export default function LiveMusicPreview({video,track,form,ceiling}) {
  const player=useRef(null), mixer=useRef(null), current=useRef({form,ceiling})
  current.current={form,ceiling}
  const [ready,setReady]=useState(false), [loading,setLoading]=useState(false), [error,setError]=useState('')
  const [playing,setPlaying]=useState(false)
  const stop=()=>{
    const m=mixer.current;if(!m)return
    for(const source of m.sources){try{source.stop()}catch{} source.disconnect()}
    m.sources=[];cancelAnimationFrame(m.frame)
  }
  const dispose=()=>{stop();const m=mixer.current;mixer.current=null;if(m)m.ctx.close().catch(()=>{});setReady(false)}
  useEffect(()=>{player.current?.pause();dispose();setError('');return dispose},[video.id,video.output_revision,track?.id])
  const start=()=>{
    stop();const m=mixer.current,v=player.current;if(!m||!v||v.paused)return
    m.ctx.resume().catch(()=>{})
    const {form:f}=current.current
    const startAt=Number(f.music_start_seconds)||0,endAt=Number(f.music_end_seconds)||m.bed?.duration||0
    if(m.bed && (endAt-startAt<3 || endAt>m.bed.duration+.1 || startAt<0)){
      v.pause();setError('Choose a valid crop of at least 3 seconds.');return
    }
    const when=m.ctx.currentTime+.03
    const voice=m.ctx.createBufferSource();voice.buffer=m.voice;voice.playbackRate.value=v.playbackRate
    voice.connect(m.analyser);m.sources.push(voice)
    if(v.currentTime<m.voice.duration)voice.start(when,v.currentTime)
    if(m.bed){
      const bed=m.ctx.createBufferSource();bed.buffer=m.bed;bed.loop=true
      bed.loopStart=startAt;bed.loopEnd=endAt;bed.playbackRate.value=v.playbackRate
      bed.connect(m.gain);m.sources.push(bed)
      bed.start(when,startAt+(v.currentTime%(endAt-startAt)))
    }
    const samples=new Float32Array(m.analyser.fftSize)
    const tick=()=>{
      if(mixer.current!==m || v.paused)return
      const {form:latest,ceiling:max}=current.current
      m.analyser.getFloatTimeDomainData(samples)
      let sum=0;for(const n of samples)sum+=n*n
      const speaking=Math.sqrt(sum/samples.length)>.025
      const amount=(Number(latest.music_volume_pct)||0)/100*max*(latest.ducking && speaking ? .3 : 1)
      m.gain.gain.setTargetAtTime(amount,m.ctx.currentTime,latest.ducking && speaking ? .01 : .25)
      m.frame=requestAnimationFrame(tick)
    };tick()
  }
  useEffect(()=>{if(ready && !player.current?.paused)start()},[form.music_start_seconds,form.music_end_seconds])
  useEffect(()=>{
    const avoidOverlap=e=>{
      if(e.target===player.current){document.querySelectorAll('audio,video').forEach(el=>{if(el!==e.target)el.pause()})}
      else player.current?.pause()
    }
    document.addEventListener('play',avoidOverlap,true)
    return ()=>document.removeEventListener('play',avoidOverlap,true)
  },[])
  const prepare=async()=>{
    player.current?.pause();dispose();setLoading(true);setError('')
    const ctx=new (window.AudioContext||window.webkitAudioContext)()
    const m={ctx,sources:[],frame:0};mixer.current=m
    try{
      await ctx.resume()
      const decode=async url=>{const r=await fetch(url);if(!r.ok)throw Error('Saved audio is unavailable for live preview');return ctx.decodeAudioData(await r.arrayBuffer())}
      const [voice,bed]=await Promise.all([decode(mediaUrl.narration(video.id)),track?decode(mediaUrl.music(track.id)):Promise.resolve(null)])
      if(mixer.current!==m)return
      m.voice=voice;m.bed=bed;m.gain=ctx.createGain();m.gain.gain.value=0
      m.analyser=ctx.createAnalyser();m.analyser.fftSize=1024
      const limiter=ctx.createDynamicsCompressor();limiter.threshold.value=-1;limiter.knee.value=0;limiter.ratio.value=20
      m.analyser.connect(limiter);m.gain.connect(limiter);limiter.connect(ctx.destination)
      setReady(true)
    }catch(e){if(mixer.current===m){dispose();setError(e.message)}}
    finally{setLoading(false)}
  }
  return <section style={{margin:'18px 0'}}>
    <h3>Live soundtrack preview</h3>
    <p className="muted tiny">Adjust intensity above while playing—no render, image generation or provider credits. Uses clean narration so old music is never doubled. Browser ducking is approximate; confirm the final saved mix before publishing.</p>
    <button className="btn" disabled={loading} onClick={prepare}>{loading?'Loading saved audio…':ready?'Reload live audio':'Load live preview'}</button>
    {error && <p role="alert" className="note warn">{error}</p>}
    {ready && <div><p className="dim tiny">Live draft only. Apply once when satisfied to save this soundtrack into the upload file.</p>
      <button className="btn" onClick={()=>{if(player.current.paused)player.current.play().catch(e=>setError(e.message));else player.current.pause()}}>{playing?'Pause live mix':'Play live mix'}</button><br/>
      <video ref={player} muted playsInline controls preload="metadata" style={{maxWidth:300,width:'100%',marginTop:12,borderRadius:10}}
        src={`${mediaUrl.video(video.id)}?revision=${encodeURIComponent(video.output_revision||'')}`}
        onPlaying={()=>{setPlaying(true);start()}} onPause={()=>{setPlaying(false);stop()}} onWaiting={stop} onSeeking={stop} onSeeked={start} onRateChange={start} onEnded={()=>{setPlaying(false);stop()}}
        onVolumeChange={e=>{if(!e.currentTarget.muted)e.currentTarget.muted=true}} />
    </div>}
  </section>
}
