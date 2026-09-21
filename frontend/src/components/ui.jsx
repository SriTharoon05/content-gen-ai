import { useEffect, useRef, useState } from 'react'

export function Stat({ label, value, sub, tone }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className="value" style={tone ? { color: `var(--${tone})` } : undefined}>{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  )
}

export function Field({ label, value, hint, children }) {
  return (
    <label className="field">
      <span className="lbl">{label}{value !== undefined ? <b>{value}</b> : null}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  )
}

export function Text({ value, onChange, placeholder, type = 'text', disabled }) {
  return (
    <input
      type={type}
      value={value ?? ''}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

/**
 * Number input that keeps its own text state while you type.
 *
 * Committing on every keystroke is what made half-typed values ("1.", "") reach the draft and light
 * up the Save All bar. Here the draft is only updated when the parsed number actually differs from
 * what is already stored, and an empty or unparseable box simply commits nothing.
 */
export function Num({ value, onChange, min, max, step = 1, disabled }) {
  const [text, setText] = useState(String(value ?? ''))
  const focused = useRef(false)

  useEffect(() => {
    if (!focused.current) setText(String(value ?? ''))
  }, [value])

  const commit = (raw) => {
    setText(raw)
    if (raw.trim() === '') return
    const parsed = Number(raw)
    if (Number.isNaN(parsed) || parsed === value) return
    onChange(parsed)
  }

  return (
    <input
      type="number"
      value={text}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onFocus={() => { focused.current = true }}
      onBlur={() => {
        focused.current = false
        if (text.trim() === '' || Number.isNaN(Number(text))) setText(String(value ?? ''))
      }}
      onChange={(e) => commit(e.target.value)}
    />
  )
}

export function Toggle({ label, value, onChange, hint, disabled }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label className="row tight" style={{ cursor: disabled ? 'not-allowed' : 'pointer', gap: 8 }}>
        <input type="checkbox" checked={!!value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
        <span style={{ fontSize: 13 }}>{label}</span>
      </label>
      {hint ? <small className="dim tiny" style={{ display: 'block', marginLeft: 23 }}>{hint}</small> : null}
    </div>
  )
}

export function Slider({ label, value, onChange, min = 0, max = 100, step = 1, suffix = '', hint }) {
  return (
    <Field label={label} value={`${value}${suffix}`} hint={hint}>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </Field>
  )
}

export function Select({ value, onChange, options, disabled }) {
  return (
    <select value={value ?? ''} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={String(o.value)} value={o.value ?? ''} disabled={o.disabled}>{o.label}</option>
      ))}
    </select>
  )
}

export function Segmented({ value, onChange, options }) {
  return (
    <div className="seg">
      {options.map((o) => (
        <button key={String(o.value)} className={value === o.value ? 'on' : ''} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Chips({ value = [], onChange, options, max }) {
  const toggle = (item) => {
    if (value.includes(item)) onChange(value.filter((v) => v !== item))
    else if (!max || value.length < max) onChange([...value, item])
  }
  return (
    <div className="row tight">
      {options.map((o) => (
        <button type="button" aria-label={o.label} aria-pressed={value.includes(o.value)} key={String(o.value)} className={`chip ${value.includes(o.value) ? 'on' : ''}`}
          onClick={() => toggle(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

const STATE_TONE = {
  READY: 'good', AWAITING_APPROVAL: 'warn', FAILED: 'bad', RETRY: 'warn', QUEUED: '',
}

export function StatePill({ state }) {
  return <span className={`pill ${STATE_TONE[state] ?? 'busy'}`}>{(state || '').replace(/_/g, ' ').toLowerCase()}</span>
}

export function Progress({ value }) {
  return <div className="bar"><div style={{ width: `${Math.max(2, Math.min(100, value || 0))}%` }} /></div>
}

export function Modal({ title, subtitle, onClose, children, footer, wide }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className={`modal ${wide ? 'wide' : ''}`} onClick={(e) => e.stopPropagation()}>
        <div className="row between" style={{ marginBottom: 16, alignItems: 'flex-start' }}>
          <div>
            <b style={{ fontSize: 15 }}>{title}</b>
            {subtitle ? <div className="dim tiny" style={{ marginTop: 3 }}>{subtitle}</div> : null}
          </div>
          <button className="btn small ghost" onClick={onClose}>Close</button>
        </div>
        {children}
        {footer ? <div className="row" style={{ marginTop: 18 }}>{footer}</div> : null}
      </div>
    </div>
  )
}

export function Empty({ children }) {
  return <p className="muted" style={{ padding: '6px 0' }}>{children}</p>
}
