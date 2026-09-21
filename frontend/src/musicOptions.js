// Channel preferences guide the agent, never restrict an operator's manual choice.
export function musicUnavailable(track) {
  if (!track.exists) return 'Audio file unavailable'
  if (!track.rights_cleared) return 'Usage rights not confirmed'
  return ''
}

export function musicOption(track) {
  const reason = musicUnavailable(track)
  return {value: track.id, label: `${track.name} (${track.category})${reason ? ` — ${reason}` : ''}`, disabled: !!reason}
}
