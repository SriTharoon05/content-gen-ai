# What the executor can actually do

You describe intent; deterministic code writes every FFmpeg command. Everything below is available.
Anything not below does not exist, and asking for it will be ignored or rejected.

## Motion — fixed, not chooseable
Every still gets a **centered straight zoom-in**. The zoom rate is an operator setting and grows with
how long the shot is on screen, bounded so a long shot never becomes a close-up. There is no pan, no
tilt, no hold, no rotation, no shake. `zoom_out` switches the incoming still to a centered pull-back.

## Transitions — your decision, per shot
| kind | overlap | looks like |
|---|---|---|
| `hard_cut` | none | instantaneous change |
| `match_cut` | none | instantaneous, but the two frames rhyme in shape or motion |
| `crossfade` | 100–1500 ms | classic dissolve between stills |
| `zoom_out` | 100–1500 ms | soft blend into a centered pull-back revealing context |
| `dissolve` | 100–1500 ms | softer, grainier blend |
| `dip_to_black` | 100–1500 ms | a beat of black; reads as a section break or time jump |
| `flash_white` | 100–1500 ms | sharp white pop; reads as a reveal or a shock |

There are no sliding, pushing, wiping or panning transitions by design: the stills already move, and a
directional wipe fights the zoom.

A blend can never exceed half of either adjacent shot's measured duration. Ask for more and it is
clamped, not refused.

## Audio
- one narration track, already paced and silence-trimmed, whose length defines the video's length
- one optional music bed: trimmed from a start offset you choose, looped to fit, faded in and out,
  and ducked under speech by a sidechain compressor
- final mix is limited and encoded to AAC 48 kHz stereo

## Captions
Word-by-word burn-in, centered, heavy white face with a thick black outline, each word popping from
small to large in under a second with its peak tracking measured narration pitch. Timings come from
the real audio. You supply emphasis words; you do not supply timings.

## Guarantees the executor enforces regardless of your plan
- the assembled picture is exactly as long as the narration
- a render with more than 120 ms of picture/sound drift is refused outright
- every caption word gets a visible slot; none are skipped or left stuck
