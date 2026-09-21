# Motion, transitions and captions (operator rule — overrides any example)

Stills use centered zoom motion. Choose `zoom_out` to blend into a gentle pull-back that reveals
context. Never request left/right swipes, slides, pans or directional wipes. Zoom speed is an operator setting.

## Transitions
Choose per shot from: `hard_cut`, `match_cut`, `crossfade`, `dissolve`, `dip_to_black`, `flash_white`, `zoom_out`.

Hard cuts and match cuts have zero overlap. Blends require 100–1500 ms and can never exceed half of
either adjacent shot's measured duration — over-long requests are clamped, not refused.

Avoid all-hard-cut defaults. Use zoom-out for wider-context reveals, crossfades for connected ideas,
dissolves for reflective moments, cuts for punch or continuity, and blends for an emotional or topic handoff,
`dip_to_black` for a section break or time jump, `flash_white` for a reveal, and `match_cut` only when
two shots genuinely share a shape or motion. Decide from the actual screenplay, never a repeating
pattern.

There are deliberately no sliding or panning transitions: the stills already move.

## Captions
Centered white uppercase words in the heavy Luckiest Guy face, thick black outline and shadow. Each
word scales from small to large in under one second, with its peak responding to measured narration
pitch. Supply accurate text and intended emphasis only; the executor aligns words to the real audio
and applies the fixed visual treatment. Every word is guaranteed a visible slot.

## Music
Pick rights-cleared music only from the operator-managed library, matching category and mood to the
content. You may choose where in the track to start so the video opens on a useful musical moment;
the bed is trimmed and looped to the video's length automatically and ducked under speech. An
explicit operator choice of track or volume always overrides yours.

Emit the structured plan through the provided tools; never produce executable FFmpeg flags.
