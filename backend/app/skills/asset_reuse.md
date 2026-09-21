# Image reuse and asset metadata

Reuse is deliberately **within a single video only**. If two shots in this video would produce
visually identical frames, they must share one image so it is generated once. Images are never
matched across different stories: two unrelated videos can have a "John" who looks nothing alike, and
a wrong reused image costs more than a new one.

## Tag by what is in frame, not why the shot exists
"John driving to his hometown" and "John driving back to the office" are different beats. If neither
frame shows anything destination-specific — no hometown sign, no office building — they are the same
picture. Give them the same `action_tag` and the second one costs nothing.

Conversely, if one shot shows a face and the other shows the same car from behind, the tags must
differ even though the narration is about the same moment.

## Fields to supply per shot
- `subject_type`: `character` | `location` | `object` | `generic`
- `action_tag`: canonical snake_case for the visible action, e.g. `surprised_reaction_closeup`,
  `car_driving_generic_exterior`
- `setting_tag`: canonical snake_case for the environment, e.g. `rural_two_lane_road_night`
- `character_refs`: `name:expression` for each visible identifiable person, e.g. `john:surprised`.
  Empty when nobody identifiable is in frame.

Matching is exact and deterministic — same subject type, action, setting and cast means the same
image. No extra model call is spent judging candidates, so tag carefully: the tags **are** the
decision.

Every generated image is also written to the asset library with this metadata and a content hash, so
a cross-story library can be built later without regenerating anything.
