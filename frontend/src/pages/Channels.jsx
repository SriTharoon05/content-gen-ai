import { useEffect, useState } from 'react'
import { api, moneyShort } from '../api'
import { useStore } from '../store.jsx'
import ChannelProfile from '../components/ChannelProfile'
import { musicOption } from '../musicOptions'
import { Field, Num, Select, Slider, Text, Toggle, Chips, Modal, Empty } from '../components/ui.jsx'

const DEFAULT_RUN = {
  publishing_mode: 'review',
  topic: '',
  count: 1,
  target_seconds: 65,
  min_shots: 20,
  max_shots: 30,
  voice: '',
  speech_tempo: 1.0,
  agent_directs_voice: true,
  primary_language: 'en',
  languages: [],
  music_enabled: true,
  music_track: '',
  music_volume_pct: 30,
  ducking: true,
  tone: '',
  style_note: '',
  must_include: '',
  must_avoid: '',
}

function MemoryModal({ slug, name, onClose }) {
  const { notify } = useStore()
  const [entries, setEntries] = useState(null)
  const [text, setText] = useState('')
  const [kind, setKind] = useState('style')
  const [pinned, setPinned] = useState(true)

  const load = () => api.memory(slug).then((r) => setEntries(r.memory))
  useEffect(() => { load() }, [slug])

  const add = async () => {
    if (!text.trim()) return
    try {
      await api.addMemory(slug, { content: text.trim(), kind, pinned })
      setText('')
      load()
    } catch (error) {
      notify(error.message, 'error')
    }
  }
  const remove = async (id) => {
    try { await api.deleteMemory(id); load() } catch (error) { notify(error.message, 'error') }
  }

  return (
    <Modal title={`Memory — ${name}`}
      subtitle="Durable notes injected into every prompt for this channel, plus what the pipeline learned from finished videos."
      onClose={onClose}>
      <div className="row" style={{ marginBottom: 14, alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <textarea value={text} onChange={(e) => setText(e.target.value)}
            placeholder="e.g. Always open on a concrete object, never a question." />
        </div>
      </div>
      <div className="row" style={{ marginBottom: 14 }}>
        <Select value={kind} onChange={setKind} options={[
          { value: 'style', label: 'Style rule' }, { value: 'note', label: 'General note' },
          { value: 'avoid', label: 'Avoid' }, { value: 'fact', label: 'Fact / continuity' },
        ]} />
        <label className="row tight" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
          <span className="tiny">Pin (never auto-pruned)</span>
        </label>
        <button className="btn primary small" onClick={add}>Add</button>
      </div>

      {entries === null ? <Empty>Loading…</Empty> : entries.length === 0 ? (
        <Empty>No memory yet. Entries the pipeline writes after each finished video will show up here too.</Empty>
      ) : (
        entries.map((entry) => (
          <div className="row between" key={entry.id} style={{ padding: '9px 0', borderBottom: '1px solid var(--line-soft)' }}>
            <div>
              <span className="pill" style={{ marginRight: 8 }}>{entry.kind}</span>
              {entry.pinned ? <span className="pill good" style={{ marginRight: 8 }}>pinned</span> : null}
              <span className="tiny">{entry.content}</span>
            </div>
            <button className="btn small ghost" onClick={() => remove(entry.id)}>Remove</button>
          </div>
        ))
      )}
    </Modal>
  )
}

function ChannelCard({ channel, onOpenVideo }) {
  const { channelValue, setChannelDraft, choices, music, costs, notify } = useStore()
  const [run, setRun] = useState({ ...DEFAULT_RUN, voice: channel.strategy?.voice_name || '' })
  const [confirming, setConfirming] = useState(false)
  const [starting, setStarting] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [editingProfile,setEditingProfile]=useState(false)

  const enabled = channelValue(channel.slug, 'enabled', channel.enabled)
  const voiceName = channelValue(channel.slug, 'voice_name', channel.strategy?.voice_name || '')
  const perImage = costs?.credits?.per_image ?? 0.004
  const worstCase = run.count * run.max_shots * perImage
  const affordable = worstCase <= (costs?.credits?.remaining ?? 0)
  const channelTracks = music.tracks

  const set = (patch) => setRun((r) => ({ ...r, ...patch }))

  const start = async () => {
    setStarting(true)
    try {
      const payload = { ...run }
      if (!payload.voice) delete payload.voice
      if (!payload.music_track) delete payload.music_track
      const result = await api.runChannel(channel.slug, payload)
      notify(`Queued ${result.queued} video(s) for ${channel.name}`)
      if (result.videos?.[0]) onOpenVideo(result.videos[0].video_id)
    } catch (error) {
      notify(error.message, 'error')
    } finally {
      setStarting(false)
      setConfirming(false)
    }
  }


  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="row between" style={{ marginBottom: 14 }}>
        <div>
          <b style={{ fontSize: 15.5 }}>{channel.name}</b>
          <span className="dim tiny" style={{ marginLeft: 8 }}>{channel.tagline}</span>
        </div>
        <div className="row tight">
          <Toggle label="Enabled" value={enabled} onChange={(v) => setChannelDraft(channel.slug, { enabled: v })} />
          <button className="btn small" onClick={() => setMemoryOpen(true)}>Memory</button>
        </div>
      </div>

      <ChannelProfile channel={channel} onEditing={setEditingProfile}/>
      <div className="grid cols-2">
        <Field label="Topic for this run" hint="Leave blank to choose from the channel's saved topic ideas"><Text value={run.topic} onChange={v=>set({topic:v})}/></Field>
        <Field label="After generation" hint="Direct publishing uploads to this channel's connected YouTube account using Settings visibility."><Select value={run.publishing_mode} onChange={v=>set({publishing_mode:v})} options={[{value:'review',label:'Review and approve before upload'},{value:'direct',label:'Publish directly to YouTube'},{value:'settings',label:'Use global publishing settings'}]}/></Field>
      </div>
      <div className="grid cols-3">
        <div>
          <div className="section-label">Length & count</div>
          <Field label="Videos to produce"><Num value={run.count} onChange={(v) => set({ count: v })} min={1} max={20} /></Field>
          <div className="row">
            <Field label="Min shots"><Num value={run.min_shots} onChange={(v) => set({ min_shots: v })} min={3} max={80} /></Field>
            <Field label="Max shots" hint="Up to 30: five extra scenes beyond the usual 25"><Num value={run.max_shots} onChange={(v) => set({ max_shots: v })} min={run.min_shots} max={30} /></Field>
          </div>
          <Field label="Target seconds"><Num value={run.target_seconds} onChange={(v) => set({ target_seconds: v })} min={15} max={180} /></Field>
        </div>

        <div>
          <div className="section-label">Voice & narration</div>
          <Field label="Voice" hint="Leave blank to let the audio agent cast it per story">
            <Select value={run.voice} onChange={(v) => set({ voice: v })}
              options={[{ value: '', label: '— agent decides —' },
                ...choices.voices.map((v) => ({ value: v.name, label: `${v.name} (${v.descriptor})` }))]} />
          </Field>
          <Slider label="Speech tempo" value={run.speech_tempo} onChange={(v) => set({ speech_tempo: v })}
            min={0.85} max={1.4} step={0.01} suffix="×" />
          <Toggle label="Agent directs full performance (tags, scene, cast)"
            value={run.agent_directs_voice} onChange={(v) => set({ agent_directs_voice: v })}
            hint="Off uses a plain read with no audio tags" />
        </div>

        <div>
          <div className="section-label">Languages</div>
          <Field label="Primary language">
            <Select value={run.primary_language} onChange={(v) => set({ primary_language: v })}
              options={choices.languages.map((l) => ({ value: l.code, label: l.name }))} />
          </Field>
          <Field label="Also produce in" hint="Same images, localised audio. English captions for every language; translated captions follow spoken phrases.">
            <Chips value={run.languages} onChange={(v) => set({ languages: v })}
              options={choices.languages.filter((l) => l.code !== run.primary_language)
                .map((l) => ({ value: l.code, label: l.name }))} max={8} />
          </Field>
        </div>
      </div>

      <div className="grid cols-3" style={{ marginTop: 4 }}>
        <div>
          <div className="section-label">Background music</div>
          <Toggle label="Use music" value={run.music_enabled} onChange={(v) => set({ music_enabled: v })} />
          {run.music_enabled && (
            <>
              <Field label="Track" hint="Leave blank to use the saved default from Music / BGM">
                <Select value={run.music_track} onChange={(v) => set({ music_track: v })}
                  options={[{ value: '', label: '— saved default —' },
                    ...channelTracks.map((t) => musicOption(t,channel.slug))]} />
              </Field>
              <Slider label="Volume" value={run.music_volume_pct} onChange={(v) => set({ music_volume_pct: v })} suffix="%" />
              <Toggle label="Duck under speech" value={run.ducking} onChange={(v) => set({ ducking: v })} />
            </>
          )}
        </div>

        <div>
          <div className="section-label">Creative direction (optional)</div>
          <Field label="Tone override"><Text value={run.tone} onChange={(v) => set({ tone: v })} placeholder="e.g. more playful" /></Field>
          <Field label="Must include"><Text value={run.must_include} onChange={(v) => set({ must_include: v })} /></Field>
          <Field label="Must avoid"><Text value={run.must_avoid} onChange={(v) => set({ must_avoid: v })} /></Field>
        </div>

        <Field label="Extra instructions for this run"><textarea value={run.style_note} onChange={e=>set({style_note:e.target.value})}/></Field>
      </div>

      <div className="row" style={{ marginTop: 16, justifyContent: 'flex-end' }}>
        <span className="dim tiny">
          Worst case: {worstCase.toFixed(3)} credits ({moneyShort({ usd: worstCase * (costs?.credits?.credit_price?.usd || 1.185), inr: worstCase * (costs?.credits?.credit_price?.inr || 114) })})
        </span>
        {confirming ? (
          <>
            <span className={`pill ${affordable ? 'good' : 'bad'}`}>
              {affordable ? 'affordable' : 'exceeds remaining credits'}
            </span>
            <button className="btn small" onClick={() => setConfirming(false)}>Cancel</button>
            <button className="btn primary small" onClick={start} disabled={starting || !affordable || editingProfile}>
              {starting ? 'Starting…' : 'Confirm & start'}
            </button>
          </>
        ) : (
          <button className="btn primary" onClick={() => setConfirming(true)} disabled={!enabled || editingProfile}>
            Start this channel
          </button>
        )}
      </div>

      {memoryOpen && <MemoryModal slug={channel.slug} name={channel.name} onClose={() => setMemoryOpen(false)} />}
    </div>
  )
}

export default function Channels({ onOpenVideo }) {
  const { channels, costs, notify, refreshLive } = useStore()
  const [batch, setBatch] = useState({ videos_per_channel: 2 })
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [runningAll, setRunningAll] = useState(false)
  const [creating,setCreating]=useState(false)

  const perImage = costs?.credits?.per_image ?? 0.004
  const enabledCount = channels.filter((c) => c.enabled).length
  const worstAll = enabledCount * batch.videos_per_channel * 30 * perImage
  const affordableAll = worstAll <= (costs?.credits?.remaining ?? 0)

  const startAll = async () => {
    setRunningAll(true)
    try {
      const result = await api.runAll(batch)
      notify(`Queued ${result.queued} video(s) across ${enabledCount} channel(s)`)
      await refreshLive()
    } catch (error) {
      notify(error.message, 'error')
    } finally {
      setRunningAll(false)
      setConfirmingAll(false)
    }
  }

  return (
    <>
      <h1 className="page-title">Channels & Runs</h1>
      <button className="btn primary" onClick={()=>setCreating(true)}>+ Create new channel</button>
      {creating && <Modal title="New channel" onClose={()=>setCreating(false)}><ChannelProfile onCreated={()=>setCreating(false)}/></Modal>}
      <p className="page-sub">
        Configure and start each channel individually, or run all enabled channels together with the
        same batch size.
      </p>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="row between">
          <div>
            <b>Start all enabled channels</b>
            <div className="dim tiny">{enabledCount} channel(s) enabled</div>
          </div>
          <div className="row">
            <Field label="Videos per channel">
              <Num value={batch.videos_per_channel} onChange={(v) => setBatch({ videos_per_channel: v })} min={1} max={20} />
            </Field>
            {confirmingAll ? (
              <>
                <span className={`pill ${affordableAll ? 'good' : 'bad'}`}>
                  worst case {worstAll.toFixed(2)} credits {affordableAll ? '' : '— exceeds remaining'}
                </span>
                <button className="btn small" onClick={() => setConfirmingAll(false)}>Cancel</button>
                <button className="btn primary small" onClick={startAll} disabled={runningAll || !affordableAll}>
                  {runningAll ? 'Starting…' : 'Confirm & start all'}
                </button>
              </>
            ) : (
              <button className="btn primary" onClick={() => setConfirmingAll(true)} disabled={!enabledCount}>
                Start all
              </button>
            )}
          </div>
        </div>
      </div>

      {channels.map((channel) => (
        <ChannelCard key={channel.slug} channel={channel} onOpenVideo={onOpenVideo} />
      ))}
    </>
  )
}
