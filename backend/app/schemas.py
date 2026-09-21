"""Every agent output is validated against these before it touches money or FFmpeg."""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TransitionKind = Literal["hard_cut", "match_cut", "crossfade", "dissolve", "dip_to_black", "flash_white", "zoom_out"]
SubjectType = Literal["character", "location", "object", "generic"]
HookKind = Literal["question", "claim", "scene", "number", "contradiction"]
BLEND_KINDS = {"crossfade", "dissolve", "dip_to_black", "flash_white", "zoom_out"}

TAG_RE = re.compile(r"\[[^\]\n]{1,40}\]")
SPEAKER_RE = re.compile(r"^\s*([A-Z][\w .'-]{0,24}):\s*", re.MULTILINE)


def spoken_text(tagged: str) -> str:
    """The words a listener actually hears: audio tags and speaker labels removed."""
    without_tags = TAG_RE.sub(" ", tagged or "")
    without_speakers = SPEAKER_RE.sub(" ", without_tags)
    return " ".join(without_speakers.split())


class Premise(BaseModel):
    title_working: str
    premise_summary: str = Field(min_length=20)
    archetype: str
    theme_tags: list[str] = Field(min_length=2, max_length=8)
    plot_beat_summary: str = Field(min_length=40)
    reasoning: str = ""


class Beat(BaseModel):
    """One shot-worth of narration. The visual agent supplies the picture for it later."""

    shot_id: str
    speaker: Literal['', 'Alex', 'Sam'] = ''
    narration: str = Field(min_length=2, max_length=240)
    emphasis_words: list[str] = Field(default_factory=list, max_length=4)
    scene_note: str = Field(default="", max_length=300)

    @field_validator("narration")
    @classmethod
    def single_line(cls, value: str) -> str:
        return " ".join(value.split())


class Script(BaseModel):
    hook_kind: HookKind
    hook: str = Field(min_length=8, max_length=160)
    beats: list[Beat] = Field(min_length=6, max_length=80)
    flagged_claims: list[str] = Field(default_factory=list)
    reasoning: str = ""

    @model_validator(mode="after")
    def hook_leads(self):
        if not self.beats:
            raise ValueError("A script needs beats")
        if [b.shot_id for b in self.beats] != [f"s{i:03d}" for i in range(1, len(self.beats) + 1)]:
            raise ValueError("shot_id must be unique and sequential: s001, s002, ...")
        opening = " ".join(self.beats[0].narration.lower().split())
        if len(opening.split()) > 16:
            raise ValueError("Beat 1 is the hook and must be at most 16 words")
        return self


class SpeakerLine(BaseModel):
    speaker: str = Field(max_length=24)
    voice: str


class VoiceDirection(BaseModel):
    """The audio agent's full performance brief, following Google's prompt structure.

    `tagged_transcript` is the narration with inline audio tags such as [whispers] or [excited], and
    speaker labels when the piece is a two-hander. The spoken words inside it must still match the
    approved script: tags change delivery, never content.
    """

    audio_profile: str = Field(min_length=10, max_length=400)
    scene: str = Field(min_length=10, max_length=700)
    directors_notes: str = Field(min_length=20, max_length=900)
    sample_context: str = Field(default="", max_length=400)
    multi_speaker: bool = False
    cast: list[SpeakerLine] = Field(default_factory=list, max_length=2)
    tagged_transcript: str = Field(min_length=20)
    speech_tempo: float = Field(default=1.08, ge=0.8, le=1.4)
    reasoning: str = ""

    @model_validator(mode="after")
    def cast_matches_mode(self):
        if self.multi_speaker and len(self.cast) != 2:
            raise ValueError("Multi-speaker needs exactly two cast entries")
        if not self.multi_speaker and len(self.cast) > 1:
            raise ValueError("Single-speaker takes at most one cast entry")
        return self


class TranslatedBeat(BaseModel):
    shot_id: str
    narration: str = Field(min_length=1, max_length=300)


class Translation(BaseModel):
    """A localisation of the approved script. Shot ids and their order are preserved exactly."""

    language: str
    beats: list[TranslatedBeat] = Field(min_length=1, max_length=80)
    hook: str = ""
    title: str = ""
    description: str = ""
    instagram_caption: str = ""
    reasoning: str = ""


class ShotVisual(BaseModel):
    """Tags describe what is VISIBLE in frame, so identical-looking shots in one video share an image."""

    shot_id: str
    image_prompt: str = Field(min_length=120, max_length=1400)
    subject_type: SubjectType = "generic"
    action_tag: str = Field(min_length=3, max_length=80)
    setting_tag: str = Field(min_length=2, max_length=80)
    character_refs: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("image_prompt")
    @classmethod
    def clean(cls, value: str) -> str:
        return " ".join(value.split())


class VisualPlan(BaseModel):
    character_bible: dict[str, str] = Field(default_factory=dict)
    style_block: str = Field(default="", max_length=400)
    shots: list[ShotVisual] = Field(min_length=1, max_length=80)
    reasoning: str = ""


class TransitionChoice(BaseModel):
    shot_id: str
    kind: TransitionKind = "hard_cut"
    duration_ms: int = 0
    why: str = ""

    @field_validator("duration_ms")
    @classmethod
    def sane(cls, value: int) -> int:
        return max(0, min(1500, int(value)))

    def normalised(self) -> "TransitionChoice":
        if self.kind in BLEND_KINDS:
            self.duration_ms = min(1500, max(100, self.duration_ms or 320))
        else:
            self.duration_ms = 0
        return self


class EditPlan(BaseModel):
    transitions: list[TransitionChoice]
    music_track: str | None = None
    music_volume_pct: int = Field(default=30, ge=0, le=100)
    music_start_seconds: float = Field(default=0.0, ge=0.0, le=600.0)
    reasoning: str = ""


class QAFinding(BaseModel):
    passed: bool
    made_for_kids: bool = False
    findings: list[str] = Field(default_factory=list)
    reasoning: str = ""


class PublishCopy(BaseModel):
    youtube_title: str = Field(max_length=100)
    youtube_description: str
    instagram_caption: str
    hashtags: list[str] = Field(default_factory=list, max_length=12)
    reasoning: str = ""


class IdeaSuggestion(BaseModel):
    title: str
    angle: str
    why_it_works: str


class IdeaSet(BaseModel):
    suggestions: list[IdeaSuggestion] = Field(min_length=3, max_length=3)


class UniqueConcept(BaseModel):
    """A candidate in the content ledger's five-way uniqueness search."""

    core_entity: str = Field(min_length=2, max_length=160)
    content_angle: str = Field(min_length=2, max_length=160)
    core_concept: str = Field(min_length=20, max_length=600)


class UniqueConceptSet(BaseModel):
    candidates: list[UniqueConcept] = Field(min_length=5, max_length=5)
