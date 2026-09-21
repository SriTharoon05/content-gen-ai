import { useEffect, useRef, useState } from 'react'
import CropAudio from '../components/CropAudio'
import { api, mediaUrl } from '../api'
import { useStore } from '../store.jsx'
import { Field, Num, Select, Slider, Text, Toggle, Empty } from '../components/ui.jsx'

function UploadForm({ onUploaded }) {
  const { music, channels, notify } = useStore()
  const fileRef = useRef(null)
  const [form, setForm] = useState({
    name: '', category: music.categories[0] || 'neutral', mood: '', tempo: 0,
    trim_start: 0, trim_end: 0, default_volume_pct: 30, channels: [], rights_cleared: false, notes: '',
  })
  const [duration, setDuration] = useState(0)
  const [previewUrl, setPreviewUrl] = useState('')
  const [uploading, setUploading] = useState(false)
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])

  const set = (patch) => setForm((f) => ({ ...f, ...patch }))

  const pickFile = (file) => {
    if (!file) return
    const url = URL.createObjectURL(file)
    setPreviewUrl(url)
    const audio = new Audio(url)
    audio.onloadedmetadata = () => {
      const d = audio.duration || 0
      setDuration(d)
      set({ trim_start: 0, trim_end: Math.min(d, 90), name: form.name || file.name.replace(/\.[^.]+$/, '') })
    }
  }

  const toggleChannel = (slug) => set({
    channels: form.channels.includes(slug) ? form.channels.filter((c) => c !== slug) : [...form.channels, slug],
  })

  const upload = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) return notify('Choose an audio file first', 'error')
    if (!form.rights_cleared) return notify('Confirm you have the rights to use this track', 'error')
    if (form.trim_end - form.trim_start < 3) return notify('Cropped region must be at least 3 seconds', 'error')
    setUploading(true)
    try {
      const data = new FormData()
      data.append('file', file)
      Object.entries(form).forEach(([key, value]) => {
        data.append(key, key === 'channels' ? value.join(',') : String(value))
      })
      await api.uploadMusic(data)
      fileRef.current.value = ''
      setPreviewUrl('')
      setForm({ name: '', category: music.categories[0] || 'neutral', mood: '', tempo: 0,
        trim_start: 0, trim_end: 0, default_volume_pct: 30, channels: [], rights_cleared: false, notes: '' })
      notify('Track added to the library')
      onUploaded()
    } catch (error) {
      notify(error.message, 'error')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h3>Add a track</h3>
      <div className="grid cols-2">
        <Field label="Audio file" hint="mp3, wav, m4a, aac, ogg or flac · under 40 MB. Upload a 1:00–1:30 clip; crop below to the part you want used.">
          <input ref={fileRef} type="file" accept="audio/*" onChange={(e) => pickFile(e.target.files?.[0])} />
        </Field>
        <Field label="Display name"><Text value={form.name} onChange={(v) => set({ name: v })} /></Field>

        <Field label="Category folder" hint="Organise by mood so you can drop more files in later — e.g. horror, emotional, uplifting">
          <div className="row">
            <Select value={form.category} onChange={(v) => set({ category: v })}
              options={music.categories.map((c) => ({ value: c, label: c }))} />
          </div>
        </Field>
        <Field label="Mood tags" hint="What the editing agent matches against">
          <Text value={form.mood} onChange={(v) => set({ mood: v })} placeholder="tense, slow build, unsettling" />
        </Field>
      </div>

      {previewUrl && duration > 0 && (
        <div className="card flat" style={{ marginTop: 4, marginBottom: 14 }}>
          <b className="tiny">Crop to the section you want ({duration.toFixed(1)}s total)</b>
          <div className="grid cols-2" style={{ marginTop: 8 }}>
            <Field label="Start (s)"><Num value={form.trim_start} onChange={(v) => set({ trim_start: Math.min(v, form.trim_end - 3) })} min={0} max={duration} step={0.5} /></Field>
            <Field label="End (s)"><Num value={form.trim_end} onChange={(v) => set({ trim_end: Math.max(v, form.trim_start + 3) })} min={0} max={duration} step={0.5} /></Field>
          </div>
          <p className="dim tiny">Using {(form.trim_end - form.trim_start).toFixed(1)}s of this track. It will be looped or trimmed to fit each video automatically.</p>
          <CropAudio src={previewUrl} start={form.trim_start} end={form.trim_end} />
        </div>
      )}

      <Slider label="Default volume for this track" value={form.default_volume_pct}
        onChange={(v) => set({ default_volume_pct: v })} suffix="%" />

      <Field label="Preferred channels for automatic selection" hint="All tracks are available to every channel when selected manually. Leave all unticked for automatic selection on any channel.">
        <div className="row">
          {channels.map((c) => (
            <label key={c.slug} className="row tight" style={{ cursor: 'pointer' }}>
              <input type="checkbox" checked={form.channels.includes(c.slug)} onChange={() => toggleChannel(c.slug)} />
              <span className="tiny">{c.name}</span>
            </label>
          ))}
        </div>
      </Field>

      <Field label="Notes"><Text value={form.notes} onChange={(v) => set({ notes: v })} placeholder="Source, licence, attribution" /></Field>
      <Toggle label="I have the rights to use this track in published videos"
        value={form.rights_cleared} onChange={(v) => set({ rights_cleared: v })} />

      <button className="btn primary" onClick={upload} disabled={uploading}>
        {uploading ? 'Uploading…' : 'Upload track'}
      </button>
    </div>
  )
}

function TrackRow({ track, channels }) {
  const { trackValue, setMusicDraft, music } = useStore()
  const volume = trackValue(track.id, 'default_volume_pct')
  const trackChannels = trackValue(track.id, 'channels') || []
  const trimStart = trackValue(track.id, 'trim_start')
  const trimEnd = trackValue(track.id, 'trim_end') || track.duration_seconds

  return (
    <div className="card flat">
      <div className="row between">
        <div>
          <b>{trackValue(track.id, 'name')}</b>
          <div className="dim tiny">
            {track.category} · {track.duration_seconds}s{track.tempo ? ` · ${track.tempo} BPM` : ''}
            {track.exists ? '' : ' · file missing'}
          </div>
        </div>
      </div>
      <CropAudio src={mediaUrl.music(track.id)} start={trimStart} end={trimEnd} />
      <Field label="Track name"><Text value={trackValue(track.id,'name')} onChange={v=>setMusicDraft(track.id,{name:v})}/></Field>
      <Field label="Category folder"><Select value={trackValue(track.id,'category')} onChange={v=>setMusicDraft(track.id,{category:v})} options={music.categories.map(c=>({value:c,label:c}))}/></Field>
      <div className="grid cols-2">
        <Field label="Crop start (s)"><Num value={trimStart} onChange={(v) => setMusicDraft(track.id, { trim_start: v })} min={0} max={track.duration_seconds} step={0.5} /></Field>
        <Field label="Crop end (s)"><Num value={trimEnd} onChange={(v) => setMusicDraft(track.id, { trim_end: v })} min={0} max={track.duration_seconds} step={0.5} /></Field>
      </div>
      <Slider label="Default volume" value={volume} onChange={(v) => setMusicDraft(track.id, { default_volume_pct: v })} suffix="%" />
      <Field label="Mood tags"><Text value={trackValue(track.id, 'mood')} onChange={(v) => setMusicDraft(track.id, { mood: v })} /></Field>
      <Field label="Preferred channels for automatic selection" hint="These preferences only guide automatic selection. You can manually choose this track on any channel. Click Save All after changing.">
        <div className="row tight">
          {channels.map((c) => (
            <span key={c.slug} className={`chip ${trackChannels.includes(c.slug) ? 'on' : ''}`}
              onClick={() => setMusicDraft(track.id, {
                channels: trackChannels.includes(c.slug) ? trackChannels.filter((x) => x !== c.slug) : [...trackChannels, c.slug],
              })}>
              {c.slug}
            </span>
          ))}
        </div>
      </Field>
    </div>
  )
}

export default function Music() {
  const { music, setMusic, channels, notify, settingsDraft, setSetting } = useStore()
  const [category, setCategory] = useState('')

  const reload = () => api.music().then(setMusic)

  const remove = async (id, name) => {
    if (!window.confirm(`Remove "${name}" from the library? Its stored audio is retained for previous edits.`)) return
    try { await api.deleteMusic(id); reload(); notify('Removed from library; audio retained for edit history') } catch (error) { notify(error.message, 'error') }
  }

  const filtered = category ? music.tracks.filter((t) => t.category === category) : music.tracks
  const byCategory = {}
  for (const t of filtered) (byCategory[t.category] ||= []).push(t)

  return (
    <>
      <h1 className="page-title">Music / BGM</h1>
      <p className="page-sub">
        Upload beds and crop them to the section you want; each video loops or trims the crop to fit
        automatically. Tracks are organised into category folders — add a new category in Settings →
        Music, then drop files straight into it as you build the library out.
      </p>

      <UploadForm onUploaded={reload} />
      <div className="card" style={{marginBottom:20}}><h3>Default background music</h3>
        <p className="muted">Used for new videos, including autonomous uploads. Save All after changing the default. No selection means narration only.</p>
        <Field label="Default track"><Select value={settingsDraft?.music?.default_track || ''} onChange={v=>setSetting('music','default_track',v)}
          options={[{value:'',label:'No background music'},...music.tracks.map(t=>({value:t.id,label:`${t.category} / ${t.name}`}))]}/></Field>
        <Slider label="Default intensity" value={settingsDraft?.music?.default_volume_pct ?? 30} suffix="%" onChange={v=>setSetting('music','default_volume_pct',v)}/>
        <Toggle label="Lower music while narration speaks" value={settingsDraft?.music?.ducking ?? true} onChange={v=>setSetting('music','ducking',v)}/>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        <Select value={category} onChange={setCategory}
          options={[{ value: '', label: 'All categories' }, ...music.categories.map((c) => ({ value: c, label: c }))]} />
      </div>

      {Object.keys(byCategory).length === 0 ? (
        <div className="card"><Empty>No tracks yet. Videos will render with narration only.</Empty></div>
      ) : (
        Object.entries(byCategory).map(([cat, tracks]) => (
          <div key={cat} style={{ marginBottom: 18 }}>
            <div className="section-label">{cat} ({tracks.length})</div>
            <div className="grid cols-2">
              {tracks.map((t) => (
                <div key={t.id}>
                  <TrackRow track={t} channels={channels} />
                  <button className="btn small danger" style={{ marginTop: 8 }} onClick={() => remove(t.id, t.name)}>Delete</button>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </>
  )
}
