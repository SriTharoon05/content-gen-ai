import { useEffect, useState } from 'react'
import { api, mediaUrl, money, moneyShort } from '../api'
import { useStore } from '../store.jsx'
import MusicEditor from '../components/MusicEditor'
import { Field, Num, Select, Slider, Text, Toggle, StatePill, Progress, Modal, Empty } from '../components/ui.jsx'

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div className="section-label">{title}</div>
      {children}
    </div>
  )
}

function ShotCard({ shot, onRegenerate }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <div className="shot clickable" onClick={() => setOpen(true)}>
        <div className="thumb">
          {shot.has_image ? (
            <img src={mediaUrl.shot(shot.videoId, shot.shot_id)} alt={shot.shot_id} loading="lazy" />
          ) : (
            <div style={{ aspectRatio: '9/16', background: 'var(--panel-3)', display: 'grid', placeItems: 'center' }}>
              <span className="dim tiny">no image</span>
            </div>
          )}
          {shot.reused ? <span className="pill good tag">reused</span> : null}
        </div>
        <div className="meta">
          <b>{shot.shot_id}</b>
          <span className="dim"> · {shot.duration.toFixed(2)}s</span>
          <div className="cap">{shot.narration}</div>
          <span className="pill">{shot.transition?.kind?.replace(/_/g, ' ') || 'hard cut'}</span>
        </div>
      </div>
      {open && (
        <Modal title={shot.shot_id} subtitle={shot.narration} onClose={() => setOpen(false)}>
          {shot.has_image && (
            <img src={mediaUrl.shot(shot.videoId, shot.shot_id)} alt="" style={{ maxWidth: 240, borderRadius: 10, marginBottom: 14 }} />
          )}
          <div className="kv" style={{ marginBottom: 14 }}>
            <span className="k">Subject</span><span>{shot.subject_type}</span>
            <span className="k">Action tag</span><span className="mono">{shot.action_tag}</span>
            <span className="k">Setting tag</span><span className="mono">{shot.setting_tag}</span>
            <span className="k">Characters</span><span>{shot.character_refs?.join(', ') || '—'}</span>
            <span className="k">Transition in</span><span>{shot.transition?.kind} {shot.transition?.duration_ms ? `(${shot.transition.duration_ms}ms)` : ''}</span>
          </div>
          <details style={{ marginBottom: 14 }}>
            <summary>Full image prompt</summary>
            <p className="tiny muted" style={{ marginTop: 8 }}>{shot.prompt}</p>
          </details>
          <button className="btn primary" onClick={() => { onRegenerate(shot.shot_id); setOpen(false) }}>
            Regenerate this image — uses selected model credits
          </button>
        </Modal>
      )}
    </>
  )
}

function RegeneratePanel({ video, shots, perImagePrice, onDone }) {
  const { choices, notify } = useStore()
  const [busy, setBusy] = useState('')
  const [playbackRate, setPlaybackRate] = useState(video.options?.playback_rate || 0.94)
  const [tab, setTab] = useState('free')
  const [narrationOpts, setNarrationOpts] = useState({
    voice: video.voice?.voice || '', speech_tempo: video.spec?.speech_tempo || 1.08, keep_direction: false,
  })
  const [scriptNotes, setScriptNotes] = useState('')
  const [addLangCode, setAddLangCode] = useState('')
  const [selectedShots, setSelectedShots] = useState([])

  const run = async (what, payload, label) => {
    setBusy(what)
    try {
      const result = await api.regenerate(video.id, what, payload)
      notify(`${label} queued (job ${result.job_id.slice(0, 8)})`)
      onDone()
    } catch (error) {
      notify(error.message, 'error')
    } finally {
      setBusy('')
    }
  }

  const existingLanguages = new Set([video.language, ...(video.variants || []).map((v) => v.language)])
  const availableLanguages = choices.languages.filter((l) => !existingLanguages.has(l.code))
  const shotCost = money({ usd: selectedShots.length * perImagePrice * 1.185, inr: selectedShots.length * perImagePrice * 114 })

  const toggleShot = (id) => setSelectedShots((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))

  return (
    <div className="card">
      <h3>Regenerate — only what you ask for</h3>
      <p className="muted tiny" style={{ marginTop: -8, marginBottom: 14 }}>
        Reuse actions keep existing assets. Regeneration uses the selected provider and may consume
        quota or credits. Audio changes rebuild captions and timing before rendering.
      </p>

      <div className="seg" style={{ marginBottom: 16 }}>
        <button className={tab === 'free' ? 'on' : ''} onClick={() => setTab('free')}>Reuse assets</button>
        <button className={tab === 'paid' ? 'on' : ''} onClick={() => setTab('paid')}>Generate new assets</button>
      </div>

      <div className="card flat" style={{marginBottom:16}}>
        <b>Audio speed — keep every image</b>
        <p className="dim tiny">Reuses the saved performance and images. Rebuilds captions, scene timing and video. Speed is relative to the saved original audio, not cumulative. Slower audio can make the video longer. Uses transcription quota only.</p>
        <Slider label="Playback speed" value={playbackRate} onChange={setPlaybackRate} min={0.75} max={1.25} step={0.01} suffix="×" />
        <button className="btn primary small" disabled={busy} onClick={() => run('speed', {playback_rate:playbackRate}, 'Audio speed and synchronized edit')}>Apply speed & rebuild</button>
      </div>

      {tab === 'free' && (
        <div className="grid cols-3">
          <div className="card flat">
            <b className="tiny">Transitions & music</b>
            <p className="dim tiny">Ask the editing agent to re-pick transitions and the music track/volume.</p>
            <button className="btn small" style={{ marginTop: 8 }} disabled={busy}
              onClick={() => run('edit', { ask_agent: true }, 'Transitions & music')}>
              {busy === 'edit' ? 'Working…' : 'Regenerate'}
            </button>
          </div>
          <div className="card flat">
            <b className="tiny">Title & caption</b>
            <p className="dim tiny">New YouTube title, description and Instagram caption from the approved script.</p>
            <button className="btn small" style={{ marginTop: 8 }} disabled={busy}
              onClick={() => run('copy', {}, 'Title & caption')}>
              {busy === 'copy' ? 'Working…' : 'Rewrite publishing copy'}
            </button>
          </div>
          <div className="card flat">
            <b className="tiny">Re-render only</b>
            <p className="dim tiny">Re-run FFmpeg from the current narration, images and edit plan. No provider call.</p>
            <button className="btn small" style={{ marginTop: 8 }} disabled={busy}
              onClick={() => run('render', {}, 'Re-render')}>
              {busy === 'render' ? 'Working…' : 'Re-render'}
            </button>
          </div>
        </div>
      )}

      {tab === 'paid' && (
        <div className="grid cols-2">
          <div className="card flat">
            <b className="tiny">New narration — speech provider quota</b>
            <p className="dim tiny">
              New voice, tempo or performance. Captions, shot spans and the cut are fully re-derived
              from the new audio — this is the fix for tempo changes going out of sync.
            </p>
            <Field label="Voice">
              <Select value={narrationOpts.voice}
                onChange={(v) => setNarrationOpts((o) => ({ ...o, voice: v }))}
                options={[{ value: '', label: '— keep current —' },
                  ...choices.voices.map((v) => ({ value: v.name, label: `${v.name} (${v.descriptor})` }))]} />
            </Field>
            <Slider label="Speech tempo" value={narrationOpts.speech_tempo}
              onChange={(v) => setNarrationOpts((o) => ({ ...o, speech_tempo: v }))} min={0.85} max={1.4} step={0.01} suffix="×" />
            <Toggle label="Keep the existing performance direction" value={narrationOpts.keep_direction}
              onChange={(v) => setNarrationOpts((o) => ({ ...o, keep_direction: v }))}
              hint="Off asks the audio agent to write a fresh performance brief" />
            <button className="btn primary small" style={{ marginTop: 8 }} disabled={busy}
              onClick={() => run('narration', narrationOpts, 'Narration')}>
              {busy === 'narration' ? 'Working…' : 'Regenerate narration'}
            </button>
          </div>

          <div className="card flat">
            <b className="tiny">Rewrite script — narration + new images</b>
            <p className="dim tiny">Rewrites the narration, then re-narrates and re-shoots automatically.</p>
            <Field label="Notes for the rewrite (optional)">
              <textarea value={scriptNotes} onChange={(e) => setScriptNotes(e.target.value)}
                placeholder="e.g. make the hook a question, tighten the middle" />
            </Field>
            <button className="btn primary small" onClick={() => run('script', { notes: scriptNotes }, 'Script rewrite')} disabled={busy}>
              {busy === 'script' ? 'Working…' : 'Rewrite script'}
            </button>
          </div>

          <div className="card flat">
            <b className="tiny">Selected shots — {selectedShots.length} × {perImagePrice} credits</b>
            <p className="dim tiny">Pick shots below, then buy fresh images for just those.</p>
            <div className="row tight" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
              {shots.map((s) => (
                <span key={s.shot_id} className={`chip ${selectedShots.includes(s.shot_id) ? 'on' : ''}`}
                  onClick={() => toggleShot(s.shot_id)}>{s.shot_id}</span>
              ))}
            </div>
            <p className="dim tiny">Cost: {shotCost}</p>
            <button className="btn primary small" disabled={busy || !selectedShots.length}
              onClick={() => run('visuals', { shot_ids: selectedShots }, 'Selected shots')}>
              {busy === 'visuals' ? 'Working…' : `Regenerate ${selectedShots.length || ''} shot(s)`}
            </button>
          </div>

          <div className="card flat">
            <b className="tiny">Add language — 1 TTS call, 0 image credits</b>
            <p className="dim tiny">Same images, a localised script, new narration and captions.</p>
            <Field label="Language">
              <Select value={addLangCode} onChange={setAddLangCode}
                options={[{ value: '', label: 'Choose a language' }, ...availableLanguages.map((l) => ({ value: l.code, label: l.name }))]} />
            </Field>
            <button className="btn primary small" disabled={busy || !addLangCode}
              onClick={() => run('language', { language: addLangCode }, 'Language variant')}>
              {busy === 'language' ? 'Working…' : 'Add language'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function VideoDetail({ videoId, onClose, onOpenVideo }) {
  const { costs, notify } = useStore()
  const [video, setVideo] = useState(null)
  const [langTab, setLangTab] = useState('primary')
  const [approving, setApproving] = useState(false)
  const [musicDirty, setMusicDirty] = useState(false)

  const load = () => api.video(videoId).then(setVideo).catch((error) => notify(error.message, 'error'))
  useEffect(() => { load() }, [videoId])
  useEffect(() => {
    if (!video || ['READY', 'FAILED', 'AWAITING_APPROVAL'].includes(video.state)) return
    const interval = setInterval(load, 4000)
    return () => clearInterval(interval)
  }, [video?.state, videoId])

  if (!video) return <Empty>Loading…</Empty>

  const activeId = langTab === 'primary' ? video.id : langTab
  const activeVariant = langTab === 'primary' ? null : (video.variants || []).find((v) => v.id === langTab)
  const displayState = activeVariant ? activeVariant.state : video.state
  const displayDone = ['READY', 'AWAITING_APPROVAL'].includes(displayState)

  const approve = async () => {
    if (!window.confirm('Approve this video and upload it to the connected YouTube channel using your saved visibility setting?')) return
    setApproving(true)
    try { await api.approve(video.id); notify('Approved — YouTube upload queued'); load() }
    catch (error) { notify(error.message, 'error') }
    finally { setApproving(false) }
  }
  const remove = async () => {
    if (!window.confirm('Delete this video and all its variants, including image comparisons? This cannot be undone.')) return
    try { await api.deleteVideo(video.id); notify('Deleted'); onClose() } catch (error) { notify(error.message, 'error') }
  }

  const shots = (video.shots || []).map((s) => ({ ...s, videoId: video.id }))
  const regenerateShot = (shotId) => api.regenerate(video.id, 'image', { shot_id: shotId })
    .then(() => { notify(`Regenerating ${shotId}`); load() })
    .catch((error) => notify(error.message, 'error'))

  return (
    <>
      <div className="row between" style={{ marginBottom: 6 }}>
        <button className="btn small ghost" onClick={onClose}>← Back</button>
        <div className="row tight">
          {displayDone && <button className="btn primary small" disabled={approving || musicDirty} onClick={approve}>{approving ? 'Queuing…' : 'Publish to YouTube'}</button>}
          <button className="btn small danger" onClick={remove}>Delete</button>
        </div>
      </div>
      <h1 className="page-title">{video.title || video.topic || 'Untitled video'}</h1>
      <p className="page-sub">{video.channel} · <StatePill state={video.state} /></p>

      {(video.variants?.length > 0) && (
        <div className="seg" style={{ marginBottom: 16 }}>
          <button className={langTab === 'primary' ? 'on' : ''} onClick={() => setLangTab('primary')}>{video.language}</button>
          {video.variants.map((v) => (
            <button key={v.id} className={langTab === v.id ? 'on' : ''} onClick={() => onOpenVideo(v.id)}>
              {v.image_model?.split('/').pop() || v.language}{v.state !== 'READY' ? ' …' : ''}
            </button>
          ))}
        </div>
      )}

      {displayDone && langTab === 'primary' && <MusicEditor key={video.output_revision} video={video} onApplied={load} onDirty={setMusicDirty} />}
      {!displayDone && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row between"><b className="tiny">{displayState.replace(/_/g, ' ')}</b></div>
          <Progress value={activeVariant?.progress ?? video.progress} />
        </div>
      )}
      {video.error && <div className="note err" style={{ marginBottom: 16 }}>{video.error}</div>}

      <div className="grid cols-2" style={{ marginBottom: 18, alignItems: 'start' }}>
        <div className="card">
          <h3>Preview</h3>
          {displayDone ? (
            <video key={`${activeId}-${video.output_revision}`} controls src={`${mediaUrl.video(activeId)}?revision=${encodeURIComponent(video.output_revision || '')}`} />
          ) : (
            <Empty>Rendering — the player will appear once this version is ready.</Empty>
          )}
          {displayDone && <a className="btn small" style={{ marginTop: 10 }} href={mediaUrl.download(activeId)}>Download</a>}
          {displayDone && !!video.options?.music_revisions?.length && <p className="note info">Soundtrack applied. Replay is optional; select Publish to YouTube when ready.</p>}
        </div>

        <div className="card">
          <h3>Cost breakdown</h3>
          <table>
            <tbody>
              {Object.entries(video.costs?.by_provider || {}).map(([name, entry]) => (
                <tr key={name}>
                  <td className="nowrap">{name}</td>
                  <td className="dim tiny">{entry.calls} call(s)</td>
                  <td className="nowrap">{moneyShort(entry.cost)}</td>
                </tr>
              ))}
              <tr><td><b>Total</b></td><td /><td><b>{moneyShort(video.costs?.total)}</b></td></tr>
            </tbody>
          </table>
          <p className="dim tiny" style={{ marginTop: 8 }}>
            {video.costs?.text_tokens?.toLocaleString()} text tokens · {video.image_count} images
            ({video.reused_count} reused) · {video.narration_seconds}s narration
          </p>
        </div>
      </div>

      <div style={{ marginBottom: 18 }}>
        <RegeneratePanel video={video} shots={shots} perImagePrice={costs?.credits?.per_image ?? 0.004} onDone={load} />
      </div>

      {video.publication_copy && <Section title="Publishing copy — actual upload text">
        <div className="grid cols-2">
          <div className="card"><h3>YouTube</h3>
            <Field label="Public title"><input type="text" readOnly value={video.publication_copy.youtube_title}/></Field>
            <Field label="Description & hashtags"><textarea readOnly rows={7} value={video.publication_copy.youtube_description}/></Field>
          </div>
          <div className="card"><h3>Instagram</h3>
            <Field label="Caption & hashtags"><textarea readOnly rows={10} value={video.publication_copy.instagram_caption}/></Field>
          </div>
        </div>
        <p className="dim tiny">Use Rewrite publishing copy to generate fresh wording from the saved script. This does not regenerate images or audio. Better copy cannot guarantee reach.</p>
      </Section>}

      <Section title={`Shots (${shots.length})`}>
        {shots.length === 0 ? <Empty>No shots yet.</Empty> : (
          <div className="shots">
            {shots.map((s) => <ShotCard key={s.shot_id} shot={s} onRegenerate={regenerateShot} />)}
          </div>
        )}
      </Section>

      <Section title="Decision log">
        {(video.decisions || []).length === 0 ? <Empty>No decisions recorded yet.</Empty> : (
          <div className="card">
            {video.decisions.map((d, i) => (
              <div key={i} style={{ padding: '9px 0', borderBottom: '1px solid var(--line-soft)' }}>
                <div className="row tight">
                  <span className="pill">{d.agent}</span>
                  <span className="dim tiny">{d.kind}</span>
                  <span className="dim tiny" style={{ marginLeft: 'auto' }}>{d.at ? new Date(d.at).toLocaleTimeString() : ''}</span>
                </div>
                {d.reasoning && <p className="tiny" style={{ margin: '5px 0 0' }}>{d.reasoning}</p>}
              </div>
            ))}
          </div>
        )}
      </Section>
    </>
  )
}
