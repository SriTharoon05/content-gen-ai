import { moneyShort } from '../api'
import { useStore } from '../store.jsx'
import { Stat } from '../components/ui.jsx'

export default function Costs() {
  const { costs } = useStore()
  if (!costs) return null

  return (
    <>
      <h1 className="page-title">Costs</h1>
      <p className="page-sub">Every figure shown in USD and INR, computed from the actual ledger of provider calls.</p>

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <Stat label="Credit balance" value={costs.credits.balance} sub={`${costs.credits.used.toFixed(3)} used`} />
        <Stat label="Remaining credits" value={costs.credits.remaining} sub={`${costs.credits.images_affordable} images left`} />
        <Stat label="Per-image cost" value={`${costs.credits.per_image}`} sub={`${moneyShort(costs.credits.per_image_price)} on ${costs.credits.model}`} />
        <Stat label="Total spent" value={moneyShort(costs.total.spent)} sub={`${costs.total.videos} videos rendered`} />
      </div>

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <div className="card">
          <h3>Images</h3>
          <p className="dim tiny">{costs.images.count} generated · {costs.images.last_24h} in the last 24h</p>
          <div className="stat"><div className="value">{moneyShort(costs.images.spent)}</div></div>
        </div>
        <div className="card">
          <h3>Narration</h3>
          <p className="dim tiny">{costs.narration.calls} calls · {costs.narration.seconds}s total audio</p>
          <div className="stat"><div className="value">{moneyShort(costs.narration.spent)}</div></div>
        </div>
        <div className="card">
          <h3>Text ({costs.text.tier}{costs.text.tier === 'paid' ? ` / ${costs.text.service_tier}` : ''})</h3>
          <p className="dim tiny">{costs.text.calls} calls · {costs.text.tokens.toLocaleString()} tokens</p>
          <div className="stat"><div className="value">{moneyShort(costs.text.spent)}</div></div>
        </div>
      </div>

      <div className="card">
        <h3>Reuse savings</h3>
        <p className="muted tiny" style={{ marginTop: -6 }}>
          Images shared between shots within the same video, never across different stories.
        </p>
        <div className="grid cols-3">
          <Stat label="Assets in library" value={costs.reuse_savings.assets} />
          <Stat label="Times reused" value={costs.reuse_savings.reuses} />
          <Stat label="Saved" value={moneyShort({ usd: costs.reuse_savings.usd_saved, inr: costs.reuse_savings.inr_saved })} />
        </div>
      </div>
    </>
  )
}
