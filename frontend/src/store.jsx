import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'

const Ctx = createContext(null)
export const useStore = () => useContext(Ctx)

const clone = (value) => JSON.parse(JSON.stringify(value ?? null))

/**
 * Compare a draft against the saved server state.
 *
 * This is the function that decides whether the Save All bar appears, so it is deliberately strict
 * about what counts as a change. Numbers typed into a text box arrive as strings, and a form control
 * can re-emit an identical value on focus or blur; neither is an edit. Normalising both sides before
 * comparing is what stops the bar from appearing when nothing has actually been touched.
 */
function normalise(value) {
  if (Array.isArray(value)) return value.map(normalise)
  if (value && typeof value === 'object') {
    return Object.keys(value).sort().reduce((out, key) => {
      out[key] = normalise(value[key])
      return out
    }, {})
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (trimmed !== '' && !Number.isNaN(Number(trimmed))) return Number(trimmed)
    return trimmed
  }
  if (typeof value === 'number') return Object.is(value, -0) ? 0 : value
  return value
}

export const sameValue = (a, b) => JSON.stringify(normalise(a)) === JSON.stringify(normalise(b))

export function StoreProvider({ children }) {
  const [settings, setSettings] = useState(null)
  const [settingsDraft, setSettingsDraft] = useState(null)
  const [channels, setChannels] = useState([])
  const [channelDrafts, setChannelDrafts] = useState({})
  const [music, setMusic] = useState({ tracks: [], categories: [], max_intensity: 0.25, default_volume_pct: 30 })
  const [musicDrafts, setMusicDrafts] = useState({})
  const [choices, setChoices] = useState({ voices: [], languages: [], transitions: [], music_categories: [] })
  const [health, setHealth] = useState(null)
  const [costs, setCosts] = useState(null)
  const [loading, setLoading] = useState(true)
  const [bootError, setBootError] = useState('')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState(null)
  const [toast, setToast] = useState(null)
  const timer = useRef(null)

  const notify = useCallback((message, kind = 'success') => {
    setToast({ message, kind, id: Date.now() })
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setToast(null), kind === 'error' ? 9000 : 3800)
  }, [])

  const loadAll = useCallback(async () => {
    try {
      const [s, c, m, h, k, o] = await Promise.all([
        api.settings(), api.channels(), api.music(), api.health(), api.costs(), api.options(),
      ])
      setSettings(s.settings)
      setSettingsDraft(clone(s.settings))
      setChannels(c.channels)
      setMusic(m)
      setHealth(h)
      setCosts(k)
      setChoices(o)
      setChannelDrafts({})
      setMusicDrafts({})
      setBootError('')
    } catch (error) {
      setBootError(error.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  const refreshLive = useCallback(async () => {
    try {
      const [h, k] = await Promise.all([api.health(), api.costs()])
      setHealth(h); setCosts(k)
    } catch { /* transient; the next poll retries */ }
  }, [])

  // --- settings draft ------------------------------------------------------
  const setSetting = useCallback((section, key, value) => {
    setSettingsDraft((draft) => {
      if (!draft) return draft
      if (sameValue(draft[section]?.[key], value)) return draft   // no-op edits never mark dirty
      return { ...draft, [section]: { ...draft[section], [key]: value } }
    })
  }, [])

  const setSection = useCallback((section, patch) => {
    setSettingsDraft((draft) => (draft ? { ...draft, [section]: { ...draft[section], ...patch } } : draft))
  }, [])

  const setChannelDraft = useCallback((slug, patch) => {
    setChannelDrafts((drafts) => ({ ...drafts, [slug]: { ...(drafts[slug] || {}), ...patch } }))
  }, [])

  const setMusicDraft = useCallback((id, patch) => {
    setMusicDrafts((drafts) => ({ ...drafts, [id]: { ...(drafts[id] || {}), ...patch } }))
  }, [])

  const channelValue = useCallback((slug, field, fallback) => {
    const draft = channelDrafts[slug]
    if (draft && field in draft) return draft[field]
    const channel = channels.find((c) => c.slug === slug)
    if (!channel) return fallback
    if (field === 'enabled') return channel.enabled
    if (field === 'overrides') return channel.overrides || {}
    if (field === 'topic_seeds') return channel.strategy?.topic_seeds || []
    if (field === 'voice_name') return channel.strategy?.voice_name || ''
    return fallback
  }, [channels, channelDrafts])

  const trackValue = useCallback((id, field) => {
    const draft = musicDrafts[id]
    if (draft && field in draft) return draft[field]
    return music.tracks.find((t) => t.id === id)?.[field]
  }, [music, musicDrafts])

  // --- dirty accounting ----------------------------------------------------
  const dirty = useMemo(() => {
    const items = []
    if (settings && settingsDraft) {
      for (const section of Object.keys(settingsDraft)) {
        if (!sameValue(settings[section], settingsDraft[section])) items.push({ kind: 'settings', label: section })
      }
    }
    for (const [slug, patch] of Object.entries(channelDrafts)) {
      const channel = channels.find((c) => c.slug === slug)
      const changed = Object.entries(patch).some(([field, value]) => {
        if (field === 'enabled') return !sameValue(channel?.enabled, value)
        if (field === 'overrides') return !sameValue(channel?.overrides || {}, value)
        if (field === 'topic_seeds') return !sameValue(channel?.strategy?.topic_seeds || [], value)
        if (field === 'voice_name') return !sameValue(channel?.strategy?.voice_name || '', value)
        return true
      })
      if (changed) items.push({ kind: 'channel', label: channel?.name || slug })
    }
    for (const [id, patch] of Object.entries(musicDrafts)) {
      const track = music.tracks.find((t) => t.id === id)
      const changed = Object.entries(patch).some(([field, value]) => !sameValue(track?.[field], value))
      if (changed) items.push({ kind: 'music', label: track?.name || 'track' })
    }
    return items
  }, [settings, settingsDraft, channelDrafts, channels, musicDrafts, music])

  const saveAll = useCallback(async () => {
    setSaving(true)
    try {
      if (settings && settingsDraft && !sameValue(settings, settingsDraft)) {
        await api.saveSettings(settingsDraft)
      }
      for (const [slug, patch] of Object.entries(channelDrafts)) await api.patchChannel(slug, patch)
      for (const [id, patch] of Object.entries(musicDrafts)) await api.patchMusic(id, patch)
      await loadAll()
      setSavedAt(new Date())
      notify(`Saved ${dirty.length} change${dirty.length === 1 ? '' : 's'}`)
      return true
    } catch (error) {
      notify(error.message, 'error')
      return false
    } finally {
      setSaving(false)
    }
  }, [settings, settingsDraft, channelDrafts, musicDrafts, dirty.length, loadAll, notify])

  const discardAll = useCallback(() => {
    setSettingsDraft(clone(settings))
    setChannelDrafts({})
    setMusicDrafts({})
    notify('Discarded unsaved changes')
  }, [settings, notify])

  useEffect(() => {
    const handler = (event) => {
      if (!dirty.length) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty.length])

  const value = {
    loading, bootError, saving, savedAt, toast, notify,
    settings, settingsDraft, setSetting, setSection, setSettingsDraft,
    channels, channelValue, setChannelDraft, refreshChannels: async()=>{const r=await api.channels();setChannels(r.channels)},
    music, setMusic, trackValue, setMusicDraft,
    choices, health, costs, refreshLive, loadAll,
    dirty, saveAll, discardAll,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
