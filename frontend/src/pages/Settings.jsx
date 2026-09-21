import { useState } from 'react'
import { getToken, setToken, describeKey } from '../api'
import { useStore } from '../store.jsx'
import { Field, Num, Select, Slider, Text, Toggle, Chips } from '../components/ui.jsx'

function KeyList({ label, hint, values, onChange }) {
  const update = (index, value) => onChange(values.map((v, i) => (i === index ? value : v)))
  const remove = (index) => onChange(values.filter((_, i) => i !== index))

  return (
    <div style={{ marginBottom: 18 }}>
      <div className="row between" style={{ marginBottom: 6 }}>
        <span className="muted tiny">{label} — {values.length} configured</span>
        <button className="btn small" onClick={() => onChange([...values, ''])}>+ Add key</button>
      </div>
      {values.length === 0 && <p className="dim tiny">No keys yet.</p>}
      {values.map((value, index) => {
        const stored = describeKey(value)
        return (
          <div className="keyrow" key={index}>
            <span className="idx">{index + 1}</span>
            {stored ? (
              <>
                <span className="val mono dim">saved key ••••{stored.tail}</span>
                <button className="btn small" onClick={() => update(index, '')}>Replace</button>
              </>
            ) : (
              <input type="password" className="val" style={{ width: 'auto', flex: 1 }} value={value}
                placeholder="paste key" onChange={(e) => update(index, e.target.value)} />
            )}
            <button className="btn small danger" onClick={() => remove(index)}>Remove</button>
          </div>
        )
      })}
      <small className="dim tiny">{hint}</small>
    </div>
  )
}

function SingleKey({ label, hint, value, onChange }) {
  const stored = describeKey(value)
  return (
    <Field label={label} hint={hint}>
      {stored ? (
        <div className="keyrow" style={{ margin: 0 }}>
          <span className="val mono dim">saved key ••••{stored.tail}</span>
          <button className="btn small" onClick={() => onChange('')}>Replace</button>
        </div>
      ) : (
        <input type="password" value={value || ''} placeholder="paste key" onChange={(e) => onChange(e.target.value)} />
      )}
    </Field>
  )
}

function ImageModelCatalog() {
  const { settingsDraft: draft, setSection } = useStore()
  const [adding, setAdding] = useState(false)
  const [newModel, setNewModel] = useState({ id: '', label: '', credits: 0.003 })

  const catalog = draft.models.image_catalog
  const selected = draft.models.image_model
  const selectedEntry = catalog.find((m) => m.id === selected)

  const setCatalog = (next) => setSection('models', { image_catalog: next })
  const updateEntry = (index, patch) => setCatalog(catalog.map((m, i) => (i === index ? { ...m, ...patch } : m)))
  const removeEntry = (index) => {
    const removed = catalog[index]
    const next = catalog.filter((_, i) => i !== index)
    setSection('models', {
      image_catalog: next,
      image_model: selected === removed.id ? (next[0]?.id || '') : selected,
    })
  }
  const addEntry = () => {
    if (!newModel.id.trim()) return
    setCatalog([...catalog, { id: newModel.id.trim(), label: newModel.label.trim() || newModel.id.trim(), credits: Number(newModel.credits) || 0.001 }])
    setNewModel({ id: '', label: '', credits: 0.003 })
    setAdding(false)
  }

  const affordable = draft.pricing.credit_balance > 0 && selectedEntry
    ? Math.floor(draft.pricing.credit_balance / selectedEntry.credits) : 0

  return (
    <div>
      <Field label="Active image model" hint="Used for every new image from the next Save All onward. Switching models changes cost instantly, everywhere.">
        <Select value={selected} onChange={(v) => setSection('models', { image_model: v })}
          options={catalog.map((m) => ({ value: m.id, label: `${m.label} — ${m.credits} credits/image` }))} />
      </Field>

      {selectedEntry && (
        <div className="okbox note info" style={{ marginBottom: 14 }}>
          At {selectedEntry.credits} credits/image, your {draft.pricing.credit_balance} credit balance
          buys about <b>{affordable} images</b> on this model
          (~{Math.floor(affordable / draft.video.max_shots)} videos at {draft.video.max_shots} shots).
        </div>
      )}

      <div className="row between" style={{ marginBottom: 8 }}>
        <span className="muted tiny">Catalog ({catalog.length} model{catalog.length === 1 ? '' : 's'})</span>
        <button className="btn small" onClick={() => setAdding(true)}>+ Add model</button>
      </div>

      {catalog.map((model, index) => (
        <div className="keyrow" key={index}>
          <span className="mono tiny" style={{ flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {model.id}
          </span>
          <input value={model.label} onChange={(e) => updateEntry(index, { label: e.target.value })}
            style={{ width: 130 }} placeholder="label" />
          <input type="number" step="0.001" value={model.credits}
            onChange={(e) => updateEntry(index, { credits: Number(e.target.value) })} style={{ width: 90 }} />
          <span className="dim tiny">credits</span>
          <button className="btn small danger" onClick={() => removeEntry(index)} disabled={catalog.length <= 1}>Remove</button>
        </div>
      ))}

      {adding && (
        <div className="keyrow">
          <input value={newModel.id} onChange={(e) => setNewModel((m) => ({ ...m, id: e.target.value }))}
            placeholder="model id, e.g. black-forest-labs/flux.1-schnell" style={{ flex: 1 }} />
          <input value={newModel.label} onChange={(e) => setNewModel((m) => ({ ...m, label: e.target.value }))}
            placeholder="label" style={{ width: 130 }} />
          <input type="number" step="0.001" value={newModel.credits}
            onChange={(e) => setNewModel((m) => ({ ...m, credits: e.target.value }))} style={{ width: 90 }} />
          <button className="btn small primary" onClick={addEntry}>Add</button>
          <button className="btn small ghost" onClick={() => setAdding(false)}>Cancel</button>
        </div>
      )}
    </div>
  )
}

export default function Settings() {
  const { settingsDraft: draft, setSetting, setSection, health, choices, channels } = useStore()
  const [token, setLocalToken] = useState(getToken())
  const [section, setSectionTab] = useState('publishing')

  if (!draft) return null
  const setKeys = (key, value) => setSection('keys', { [key]: value })

  return (
    <>
      <h1 className="page-title">Settings</h1>
      <p className="page-sub">
        Defaults for new videos. Changes are saved to the database when you select Save All.
      </p>
      <div className="workspace-tabs" role="tablist" aria-label="Settings sections">{[['publishing','Publishing & schedule'],['content','Content & media'],['providers','Providers & runtime'],['budget','Budget & reuse']].map(([value,label])=><button key={value} role="tab" aria-selected={section===value} className={section===value?'on':''} onClick={()=>setSectionTab(value)}>{label}</button>)}</div>
      <div className="grid cols-2">
        <div className="card" hidden={section!=='providers'} style={{ gridColumn: '1 / -1' }}>
          <h3>API keys</h3>
          <p className="dim tiny" style={{ marginTop: -6, marginBottom: 12 }}>
            A saved key shows as ••••, never the value. Click Replace to swap it — the field only
            commits a new key when you actually type one; leaving it alone keeps the original exactly
            as stored, byte for byte.
          </p>
          <KeyList label="Gemini free-tier keys (text, planning, QA)" values={draft.keys.gemini_free}
            onChange={(v) => setKeys('gemini_free', v)}
            hint="Rotated on every call, 65s cooldown on a rate limit, retired on an auth failure until the next save." />
          <KeyList label="Additional paid fallback keys" values={draft.keys.gemini_paid}
            onChange={(v) => setKeys('gemini_paid', v)}
            hint="Used only after the free key pool cannot complete the request." />
          <SingleKey label="Gemini paid key — audio and text fallback" value={draft.keys.gemini_audio_paid}
            onChange={(v) => setKeys('gemini_audio_paid', v)}
            hint="GEMINI_AUDIO_PAID_KEY funds narration and serves as paid text fallback after free keys fail." />
          <KeyList label="Pollinations keys (image generation)" values={draft.keys.pollinations}
            onChange={(v) => setKeys('pollinations', v)} hint="Add as many as you have; the pool rebuilds on save." />
          <KeyList label="Groq keys (word alignment for captions)" values={draft.keys.groq}
            onChange={(v) => setKeys('groq', v)} hint="Free-tier Whisper large-v3-turbo. Used instead of the offline aligner when configured." />
          <Field label="Dashboard admin token" hint="Stored in this browser only; applies immediately, not through Save All.">
            <input type="password" value={token}
              onChange={(e) => { setLocalToken(e.target.value); setToken(e.target.value) }} />
          </Field>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Text generation</h3>
          <Field label="Text routing" hint="Every request tries available free keys first, then paid keys, including the audio key.">
            <Select value={draft.models.text_tier} onChange={(v) => setSetting('models', 'text_tier', v)}
              options={[{ value: 'free', label: 'Free first → paid fallback' }]} />
          </Field>
          {draft.models.text_tier === 'paid' && (
            <Field label="Service tier" hint="Flex is roughly half price with higher latency; fine for overnight batches.">
              <Select value={draft.models.service_tier} onChange={(v) => setSetting('models', 'service_tier', v)}
                options={[{ value: 'standard', label: 'Standard' }, { value: 'flex', label: 'Flex (cheaper, slower)' }]} />
            </Field>
          )}
          <Field label="Thinking level" hint="Text is cheap relative to images and audio — higher thinking trades a little latency for noticeably better scripts and decisions.">
            <Select value={draft.models.thinking} onChange={(v) => setSetting('models', 'thinking', v)}
              options={[{ value: 'low', label: 'Low' }, { value: 'medium', label: 'Medium' }, { value: 'high', label: 'High (recommended)' }]} />
          </Field>
          <p className="muted">Text model: Gemini 3.1 Flash-Lite (fixed for both free and paid requests).</p>
          <Toggle label="Allow paid text fallback" value={draft.models.allow_paid_text_fallback ?? true} onChange={(v) => setSetting('models','allow_paid_text_fallback',v)} hint="Off means text generation stops if every free key is unavailable. The comparison batch uses free-only text." />
          <p className="dim tiny">Gemini creates content directly: unique concept, script, writing QA, voice, images, captions and render. External fact-checking is disabled; duplicate-content checks remain.</p>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Image model</h3>
          <ImageModelCatalog />
          <Field label="Image endpoint" hint="Pollinations-compatible /v1/images/generations">
            <Text value={draft.models.image_endpoint} onChange={(v) => setSetting('models', 'image_endpoint', v)} />
          </Field>
          <Field label="Image size"><Text value={draft.models.image_size} onChange={(v) => setSetting('models', 'image_size', v)} /></Field>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Video & motion</h3>
          <div className="grid cols-2">
            <Field label="Width"><Num value={draft.video.width} onChange={(v) => setSetting('video', 'width', v)} /></Field>
            <Field label="Height"><Num value={draft.video.height} onChange={(v) => setSetting('video', 'height', v)} /></Field>
            <Field label="FPS">
              <Select value={String(draft.video.fps)} onChange={(v) => setSetting('video', 'fps', Number(v))}
                options={[24, 25, 30].map((f) => ({ value: String(f), label: `${f} fps` }))} />
            </Field>
            <Field label="Target seconds"><Num value={draft.video.target_seconds} onChange={(v) => setSetting('video', 'target_seconds', v)} /></Field>
            <Field label="Min shots"><Num value={draft.video.min_shots} onChange={(v) => setSetting('video', 'min_shots', v)} /></Field>
            <Field label="Max shots"><Num value={draft.video.max_shots} onChange={(v) => setSetting('video', 'max_shots', v)} /></Field>
          </div>
          <Field label="Minimum shot length (s)" hint="Beats shorter than this are merged away before their image is bought.">
            <Num value={draft.video.min_shot_seconds} onChange={(v) => setSetting('video', 'min_shot_seconds', v)} step={0.05} />
          </Field>
          <Slider label="Zoom speed" value={draft.video.zoom_speed} onChange={(v) => setSetting('video', 'zoom_speed', v)}
            min={0} max={0.15} step={0.005} hint="Fractional zoom added per second on screen. Every still zooms in only — never pans, holds or zooms out." />
          <Slider label="Maximum zoom" value={draft.video.zoom_max} onChange={(v) => setSetting('video', 'zoom_max', v)}
            min={0.02} max={0.4} step={0.01} hint="Ceiling however long a shot stays on screen." />
          <div className="grid cols-2">
            <Field label="Lead silence (ms)"><Num value={draft.video.lead_silence_ms} onChange={(v) => setSetting('video', 'lead_silence_ms', v)} /></Field>
            <Field label="Tail silence (ms)" hint="The fixed pad that replaced the TTS model's variable trailing silence — this is the A/V sync fix.">
              <Num value={draft.video.tail_silence_ms} onChange={(v) => setSetting('video', 'tail_silence_ms', v)} />
            </Field>
          </div>
          <Field label="Max seconds a single caption word may hold" hint="Hard cap so an uncertain alignment can never leave a word stuck on screen.">
            <Num value={draft.video.caption_max_word_seconds} onChange={(v) => setSetting('video', 'caption_max_word_seconds', v)} step={0.1} />
          </Field>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Voice</h3>
          <Toggle label="Free Gemini TTS first, then Groq" value={draft.voice.free_tts_first ?? true} onChange={(v) => setSetting('voice', 'free_tts_first', v)} hint="Applies to Gemini 2.5 Flash Preview TTS. Uses free Gemini keys, then Groq; no paid Gemini narration fallback." />
          <Field label="Audio generation model" hint="Applies to newly generated narration after Save All.">
            <Select value={draft.models.tts} onChange={(v) => setSetting('models', 'tts', v)}
              options={['canopylabs/orpheus-v1-english', 'canopylabs/orpheus-arabic-saudi', 'gemini-3.1-flash-tts-preview', 'gemini-2.5-flash-preview-tts', 'gemini-2.5-pro-preview-tts'].map((id) => ({ value: id, label: id }))} />
          </Field>
          {draft.models.tts.startsWith('canopylabs/') && <>
            <Field label="Groq English voice"><Select value={draft.voice.groq_voice || 'troy'} onChange={(v) => setSetting('voice', 'groq_voice', v)} options={['troy','austin','daniel','autumn','diana','hannah'].map(v => ({value:v,label:v}))} /></Field>
            <Field label="Groq Arabic voice"><Select value={draft.voice.groq_arabic_voice || 'fahad'} onChange={(v) => setSetting('voice', 'groq_arabic_voice', v)} options={['fahad','abdullah','sultan','lulwa','noura','aisha'].map(v => ({value:v,label:v}))} /></Field>
            <p className="dim tiny">Preview model — evaluation only, not recommended by Groq for production. Requires model terms acceptance. Uses Groq keys above; single-speaker narration. Gemini casting/direction settings below do not apply. Organization quotas are shared across keys.</p>
          </>}
          <Field label="Default voice" hint="Used only when a channel or run does not specify one">
            <Select value={draft.voice.default_voice} onChange={(v) => setSetting('voice', 'default_voice', v)}
              options={choices.voices.map((v) => ({ value: v.name, label: `${v.name} (${v.descriptor})` }))} />
          </Field>
          <Slider label="Default speech tempo" value={draft.voice.speech_tempo} onChange={(v) => setSetting('voice', 'speech_tempo', v)}
            min={0.8} max={1.4} step={0.01} suffix="×" />
          <Toggle label="Let the audio agent direct the full performance" value={draft.voice.agent_directs_voice}
            onChange={(v) => setSetting('voice', 'agent_directs_voice', v)}
            hint="Casts the voice, writes the scene and director's notes, and adds audio tags like [excited] or [whispers]." />
          <Toggle label="Allow multi-speaker dialogue" value={draft.voice.allow_multi_speaker}
            onChange={(v) => setSetting('voice', 'allow_multi_speaker', v)}
            hint="Only used when a story genuinely reads as two people; most shorts stay single-narrator." />
          <Field label="Default pacing note"><Text value={draft.voice.pace_note} onChange={(v) => setSetting('voice', 'pace_note', v)} /></Field>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Languages</h3>
          <Field label="Primary language">
            <Select value={draft.languages.primary} onChange={(v) => setSetting('languages', 'primary', v)}
              options={choices.languages.map((l) => ({ value: l.code, label: l.name }))} />
          </Field>
          <Field label="Default additional languages" hint="Applied to every new run unless overridden on that run. Same images, localised narration and captions per language.">
            <Chips value={draft.languages.additional} onChange={(v) => setSection('languages', { additional: v })}
              options={choices.languages.filter((l) => l.code !== draft.languages.primary).map((l) => ({ value: l.code, label: l.name }))} />
          </Field>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Caption alignment</h3>
          <Field label="Provider" hint="Groq's free-tier Whisper handles every listed language, including Tamil, Telugu, Malayalam, Kannada and Hindi.">
            <Select value={draft.align.provider} onChange={(v) => setSetting('align', 'provider', v)}
              options={[{ value: 'groq', label: 'Groq Whisper — measured audio timestamps' }]} />
          </Field>
          <Field label="Groq model"><Text value={draft.align.groq_model} onChange={(v) => setSetting('align', 'groq_model', v)} /></Field>
          <Field label="Minimum match ratio" hint="Captions always use the actual audio transcription. This score reports how closely narration matches the script.">
            <Num value={draft.align.min_match_ratio} onChange={(v) => setSetting('align', 'min_match_ratio', v)} step={0.05} min={0} max={1} />
          </Field>
          <p className="dim tiny">
            {health?.alignment?.groq_keys || 0} Groq key(s) configured · vosk {health?.alignment?.vosk_installed ? 'installed' : 'not installed'}
          </p>
        </div>

        <div className="card" hidden={section!=='content'}>
          <h3>Music</h3>
          <Toggle label="Use background music by default" value={draft.music.enabled} onChange={(v) => setSetting('music', 'enabled', v)} />
          <Toggle label="Duck music under speech" value={draft.music.ducking} onChange={(v) => setSetting('music', 'ducking', v)} />
          <Slider label="Default volume" value={draft.music.default_volume_pct} onChange={(v) => setSetting('music', 'default_volume_pct', v)} suffix="%" />
          <Field label="Mix ceiling" hint="Mix level at 100% on the volume slider, relative to narration. 0.08–0.15 is typical.">
            <Num value={draft.music.max_intensity} onChange={(v) => setSetting('music', 'max_intensity', v)} step={0.01} />
          </Field>
          <Field label="Category folders" hint="Add a category here, then upload tracks into it from the Music page.">
            <div className="row tight" style={{ flexWrap: 'wrap' }}>
              {draft.music.categories.map((cat, i) => (
                <span key={cat} className="chip on" style={{ cursor: 'default' }}>
                  {cat}
                  <span style={{ cursor: 'pointer', marginLeft: 4 }}
                    onClick={() => setSection('music', { categories: draft.music.categories.filter((_, idx) => idx !== i) })}>×</span>
                </span>
              ))}
              <input placeholder="+ new category" style={{ width: 130 }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && e.target.value.trim()) {
                    setSection('music', { categories: [...draft.music.categories, e.target.value.trim().toLowerCase()] })
                    e.target.value = ''
                  }
                }} />
            </div>
          </Field>
        </div>

        <div className="card" hidden={section!=='budget'}>
          <h3>Reuse</h3>
          <Toggle label="Reuse matching images within the same video" value={draft.reuse.enabled} onChange={(v) => setSetting('reuse', 'enabled', v)}
            hint="Never across different stories — only within one video, matched on exact visible tags." />
          <Toggle label="Check story uniqueness across all channels" value={draft.reuse.cross_channel_dedup}
            onChange={(v) => setSetting('reuse', 'cross_channel_dedup', v)}
            hint="Off checks only within each channel's own history." />
          <Field label="Premise similarity threshold" hint="Tag overlap above this sends a premise back for revision.">
            <Num value={draft.reuse.similarity_threshold} onChange={(v) => setSetting('reuse', 'similarity_threshold', v)} step={0.01} />
          </Field>
        </div>

        <div className="card" hidden={section!=='budget'}>
          <h3>Pricing</h3>
          <div className="grid cols-2">
            <Field label="USD per credit"><Num value={draft.pricing.credit_price_usd} onChange={(v) => setSetting('pricing', 'credit_price_usd', v)} step={0.001} /></Field>
            <Field label="USD → INR"><Num value={draft.pricing.usd_to_inr} onChange={(v) => setSetting('pricing', 'usd_to_inr', v)} step={0.1} /></Field>
            <Field label="Credit balance" hint="Top up here when you buy more.">
              <Num value={draft.pricing.credit_balance} onChange={(v) => setSetting('pricing', 'credit_balance', v)} step={0.1} />
            </Field>
          </div>
          <div className="section-label">Text (gemini-3.1-flash-lite, paid tier)</div>
          <div className="grid cols-2">
            <Field label="Input $/1M tokens"><Num value={draft.pricing.text_input_usd_per_1m} onChange={(v) => setSetting('pricing', 'text_input_usd_per_1m', v)} step={0.01} /></Field>
            <Field label="Output $/1M tokens"><Num value={draft.pricing.text_output_usd_per_1m} onChange={(v) => setSetting('pricing', 'text_output_usd_per_1m', v)} step={0.01} /></Field>
          </div>
          <div className="section-label">Narration TTS</div>
          <div className="grid cols-2">
            <Field label="Input $/1M tokens"><Num value={draft.pricing.tts_input_usd_per_1m} onChange={(v) => setSetting('pricing', 'tts_input_usd_per_1m', v)} step={0.1} /></Field>
            <Field label="Audio $/1M tokens"><Num value={draft.pricing.tts_audio_usd_per_1m} onChange={(v) => setSetting('pricing', 'tts_audio_usd_per_1m', v)} step={0.1} /></Field>
          </div>
          <Field label="Audio tokens per second" hint="Google bills TTS audio output at this rate.">
            <Num value={draft.pricing.tts_tokens_per_second} onChange={(v) => setSetting('pricing', 'tts_tokens_per_second', v)} />
          </Field>
        </div>

        <div className="card" hidden={section!=='providers'}>
          <h3>Runtime</h3>
          <div className="grid cols-2">
            <Field label="Worker threads" hint="Videos rendered at once"><Num value={draft.runtime.worker_concurrency} onChange={(v) => setSetting('runtime', 'worker_concurrency', v)} min={1} max={8} /></Field>
            <Field label="Image concurrency" hint="Parallel image calls per video"><Num value={draft.runtime.image_concurrency} onChange={(v) => setSetting('runtime', 'image_concurrency', v)} min={1} max={12} /></Field>
            <Field label="Max QA revision rounds"><Num value={draft.runtime.max_qa_rounds} onChange={(v) => setSetting('runtime', 'max_qa_rounds', v)} min={0} max={5} /></Field>
            <Field label="Provider retries"><Num value={draft.runtime.provider_retries} onChange={(v) => setSetting('runtime', 'provider_retries', v)} min={1} max={6} /></Field>
            <Field label="ffmpeg path"><Text value={draft.runtime.ffmpeg_path} onChange={(v) => setSetting('runtime', 'ffmpeg_path', v)} /></Field>
            <Field label="ffprobe path"><Text value={draft.runtime.ffprobe_path} onChange={(v) => setSetting('runtime', 'ffprobe_path', v)} /></Field>
          </div>
          <p className="dim tiny">
            ffmpeg {health?.ffmpeg ? 'found' : 'NOT found'} · ffprobe {health?.ffprobe ? 'found' : 'NOT found'} ·
            caption font {health?.caption_font ? 'found' : 'NOT found'}
          </p>
        </div>

        <div className="card span-2" hidden={section!=='publishing'}>
          <h3>Publishing & daily schedule</h3>
          <Toggle label="Review before upload" value={draft.publishing?.review_before_upload ?? true}
            onChange={v => setSetting('publishing', 'review_before_upload', v)}
            hint="On: approve each finished video manually. Off: new eligible videos publish to the destinations below. Existing waiting videos are not uploaded retroactively." />
          <Field label="Publishing destinations" hint="Used by scheduled runs and runs set to use global destinations. Connect each selected platform before enabling direct publishing.">
            <Chips value={draft.publishing?.platforms || ['youtube']} onChange={v=>setSetting('publishing','platforms',v)} options={[{value:'youtube',label:'YouTube'},{value:'instagram',label:'Instagram'}]}/>
          </Field>
          <Field label="YouTube upload visibility">
            <Select value={draft.publishing?.youtube_privacy || 'private'} onChange={v => setSetting('publishing','youtube_privacy',v)}
              options={['private','unlisted','public'].map(value => ({value,label:value}))} />
          </Field>
          <Toggle label="Enable publishing after generation" value={draft.schedule.auto_publish}
            onChange={(v) => setSetting('schedule', 'auto_publish', v)}
            hint="Uses your selected YouTube / Instagram destinations. Review mode still requires approval; comparison-only runs remain excluded." />
          <Toggle label="Run automatically every day" value={draft.schedule.enabled} onChange={(v) => setSetting('schedule', 'enabled', v)} />
          <Field label="Scheduled channels" hint="Nothing selected = all enabled channels. Select only LoreHush for a single-channel test.">
            <Chips value={draft.schedule.channels || []} onChange={v=>setSetting('schedule','channels',v)} options={channels.filter(c=>c.enabled).map(c=>({value:c.slug,label:c.name || c.slug}))}/>
          </Field>
          <div className="grid cols-2">
            <Field label="Run at (HH:MM local)"><Text value={draft.schedule.run_at} onChange={(v) => setSetting('schedule', 'run_at', v)} /></Field>
            <Field label="Timezone offset (min)" hint="330 = IST"><Num value={draft.schedule.timezone_offset_minutes} onChange={(v) => setSetting('schedule', 'timezone_offset_minutes', v)} /></Field>
            <Field label="Videos per channel per run"><Num value={draft.schedule.videos_per_channel} onChange={(v) => setSetting('schedule', 'videos_per_channel', v)} min={1} max={20} /></Field>
            <Field label="Daily credit ceiling"><Num value={draft.schedule.daily_credit_ceiling} onChange={(v) => setSetting('schedule', 'daily_credit_ceiling', v)} step={0.05} /></Field>
          </div>
          <div className="note warn">
            A scheduled batch that would exceed the daily ceiling is trimmed, not failed — channels
            skipped that way are shown in the Jobs tab.
          </div>
        </div>
      </div>
    </>
  )
}
