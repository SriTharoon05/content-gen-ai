"""Stage orchestration.

Agents decide; deterministic tools spend money and render. Three ordering choices matter:

1. **Narration is generated and measured before any image is bought**, so a beat too short to watch is
   merged away before its picture is paid for.
2. **`build_video` is the single canonical path from narration to finished file.** Every regeneration
   re-enters it rather than patching a half-state, which is why changing the speech tempo re-derives
   the alignment, the captions, the shot spans, the timeline and the clips instead of reusing stale
   ones.
3. **Extra languages reuse the images.** The script is localised, narrated and re-timed per language;
   only the audio, the captions and the cut change.
"""
import json
import logging
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import content_ledger, memory, registry
from .agents import roles
from .alignment import align, normalized
from .captions import analyze_audio, beat_spans, pitch_frames, timing_report, write_ass
from .config import boot
from .db import session_scope
from .llm import attribute_to
from .media import assemble, build_timeline, seconds as media_seconds
from .models import Asset, Channel, Decision, ProviderCall, StoryHistory, Video
from .providers.images import generate_image, remaining_credits
from .providers.speech import synthesize
from .schemas import EditPlan, Premise, Script, VoiceDirection, spoken_text
from .settings_store import cfg, credits_per_image, language_name

log = logging.getLogger("pipeline")

STAGES = {
    "PREMISE": 6, "SCRIPT": 14, "QA": 20, "VOICE": 26, "NARRATION": 36, "ALIGNING": 44,
    "VISUALS": 54, "IMAGES": 72, "EDITING": 80, "CAPTIONS": 84, "RENDERING": 92,
    "COPY": 95, "TRANSLATING": 96, "AWAITING_APPROVAL": 99, "READY": 100,
}


class PipelineError(RuntimeError):
    pass


def work_dir(video_id: str) -> Path:
    path = boot().work_root / video_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def clips_dir(video_id: str) -> Path:
    """Intermediates live in the work directory, never beside the output in a synced folder."""
    path = work_dir(video_id) / "clips"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record(video_id: str, agent: str, kind: str, payload: dict, reasoning: str = "") -> None:
    with session_scope() as session:
        session.add(Decision(video_id=video_id, agent=agent, kind=kind, payload_json=payload,
                             reasoning=(reasoning or "")[:4000]))


def set_state(video_id: str, state: str, detail: str = "", **fields) -> None:
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            return
        video.state = state
        video.stage_detail = detail[:160]
        video.progress = STAGES.get(state, video.progress)
        for key, value in fields.items():
            setattr(video, key, value)


def channel_dict(slug: str) -> dict:
    with session_scope() as session:
        row = session.get(Channel, slug)
        if not row:
            raise PipelineError(f"Unknown channel '{slug}'")
        return {"slug": row.slug, "name": row.name, "tagline": row.tagline, "niche": row.niche,
                "strategy_json": row.strategy_json or {}, "overrides_json": row.overrides_json or {}}


def resolve_options(channel: dict, run_options: dict | None = None) -> dict:
    """Global settings <- per-channel overrides <- per-run options from the UI."""
    resolved = {
        "min_shots": cfg("video", "min_shots"),
        "max_shots": cfg("video", "max_shots"),
        "target_seconds": cfg("video", "target_seconds"),
        "voice": channel.get("strategy_json", {}).get("voice_name") or "",
        "speech_tempo": cfg("voice", "speech_tempo"),
        "pace_note": cfg("voice", "pace_note"),
        "agent_directs_voice": cfg("voice", "agent_directs_voice"),
        "music_enabled": cfg("music", "enabled"),
        "music_track": cfg('music', 'default_track', default=''),
        "music_volume_pct": cfg("music", "default_volume_pct"),
        "ducking": cfg("music", "ducking"),
        "languages": list(cfg("languages", "additional", default=[]) or []),
        "primary_language": cfg("languages", "primary", default="en"),
        "tone": "", "style_note": "", "must_include": "", "must_avoid": "",
    }
    for source in (channel.get("overrides_json") or {}, run_options or {}):
        for key, value in (source or {}).items():
            if value is not None and value != "":
                resolved[key] = value
    resolved["min_shots"] = max(20, min(30, int(resolved["min_shots"])))
    resolved["max_shots"] = min(30, max(resolved["min_shots"], int(resolved["max_shots"])))
    resolved["target_seconds"] = max(45, min(75, float(resolved["target_seconds"])))
    return resolved


def token_total(video_id: str) -> int:
    with session_scope() as session:
        rows = session.query(ProviderCall).filter_by(video_id=video_id, provider="gemini_text").all()
        return int(sum(r.units for r in rows))


# ------------------------------------------------------------------- stage 1-3: story and review


def stage_story(video_id: str, channel: dict, options: dict) -> tuple[Premise, Script, dict]:
    work = work_dir(video_id)
    with session_scope() as session:
        saved = session.get(Video, video_id)
        topic = saved.topic
        if saved.script_json and saved.premise_json and (saved.qa_json or {}).get("passed"):
            options['content_origin'] = 'model_generated_unverified'
            return Premise.model_validate(saved.premise_json), Script.model_validate(saved.script_json), saved.qa_json

    if not topic:
        seeds = channel["strategy_json"].get("topic_seeds") or ["an original story for this channel"]
        topic = random.choice(seeds)

    set_state(video_id, "PREMISE", "reserving a unique content concept", topic=topic)
    reservation = content_ledger.reserve_for_video(video_id, channel, topic, options)
    options.pop('source_evidence', None)
    options['content_origin'] = 'model_generated_unverified'
    with session_scope() as session:
        row = session.get(Video, video_id)
        saved_options = dict(row.options_json or {})
        saved_options.pop('source_evidence', None)
        row.options_json = {**saved_options, 'content_origin':'model_generated_unverified'}
    record(
        video_id,
        "orchestrator",
        "content_reservation",
        reservation,
        "Reserved the first non-colliding concept before script, narration or image generation",
    )
    topic = f"CREATIVE BRIEF (model-generated, not externally verified): {reservation['core_concept']}"
    set_state(video_id, "PREMISE", "checking story history after content reservation", topic=topic)
    prior = registry.prior_stories(channel["slug"])
    premise = roles.write_premise(channel, topic, prior, options=options)
    for _ in range(2):
        clashes = registry.similarity(premise.premise_summary, premise.theme_tags, prior)
        record(video_id, "orchestrator", "uniqueness_check", {"matches": clashes}, "Checked channel story history")
        if not clashes:
            break
        premise = roles.write_premise(channel, topic, prior, rejected=clashes, options=options)
    record(video_id, "orchestrator", "premise", premise.model_dump(), premise.reasoning)
    (work / "storyboard.md").write_text(premise.plot_beat_summary, encoding="utf-8")

    shot_target = random.randint(int(options["min_shots"]), int(options["max_shots"]))
    seconds_per_beat = max(0.9, float(options["target_seconds"]) / shot_target)
    set_state(video_id, "SCRIPT", f"{shot_target} beats at ~{seconds_per_beat:.1f}s",
              premise_json=premise.model_dump())
    script = roles.write_script(channel, premise, shot_target, seconds_per_beat, options=options)

    set_state(video_id, "QA", "independent review")
    finding = roles.review(channel, premise, script, options=options)
    rounds = 0
    while not finding.passed and rounds < int(cfg("runtime", "max_qa_rounds", default=2)):
        rounds += 1
        set_state(video_id, "QA", f"revision round {rounds}")
        script = roles.write_script(channel, premise, shot_target, seconds_per_beat,
                                    revision_notes=finding.findings, options=options)
        finding = roles.review(channel, premise, script, options=options)
    record(video_id, "qa", "qa", finding.model_dump(), finding.reasoning)
    if not finding.passed:
        raise PipelineError("QA failed after revisions: " + "; ".join(finding.findings[:4]))

    record(video_id, "script", "script", script.model_dump(), script.reasoning)
    (work / "script.md").write_text(" ".join(b.narration for b in script.beats), encoding="utf-8")
    set_state(video_id, "QA_PASSED", "ready for narration", script_json=script.model_dump(),
              qa_json=finding.model_dump(), made_for_kids=finding.made_for_kids)
    from .storage import checkpoint
    checkpoint(video_id)
    return premise, script, finding.model_dump()


# ------------------------------------------------------------- stage 4-5: voice, narrate, measure


def stage_voice(video_id: str, channel: dict, premise: Premise, script: Script, beats: list[dict],
                language: str, options: dict) -> dict:
    """The audio agent casts and directs. Its tagged transcript is verified against the script."""
    fallback_voice = options.get("voice") or cfg("voice", "default_voice", default="Charon")
    plain = " ".join(b["narration"] for b in beats)
    if channel.get('strategy_json', {}).get('conversation'):
        cast = [{'speaker':'Alex', 'voice':'Puck'}, {'speaker':'Sam', 'voice':'Zephyr'}]
        turns = []
        for i,b in enumerate(beats):
            speaker = b.get('speaker') or cast[i % 2]['speaker']
            if turns and turns[-1][0] == speaker:
                turns[-1][1] += ' ' + b['narration']
            else:
                turns.append([speaker,b['narration']])
        payload = {'voice':'Puck + Zephyr', 'cast':cast, 'multi_speaker':True,
            'tagged_transcript':'\n'.join(f"{speaker}: {line}" for speaker,line in turns),
            'speech_tempo':float(options.get('speech_tempo',1.0)),
            'direction':{'audio_profile':'Two adult friends: Alex is upbeat, Sam bright and curious.',
                'scene':'A high-quality recording studio. Two friends talking casually into dynamic microphones.',
                'directors_notes':'Genuine two-friend chemistry: actively listen and respond, smile on agreement, lift pitch on curious questions, warm surprise and playful emphasis where earned. Flow through image boundaries without stopping. Complete thoughts, natural breaths, relaxed expressive rhythm—not memorized facts. Clear turn-taking, never rush or overlap meaningful words.',
                'sample_context':'Friends exploring a surprising real topic together.'}}
        with session_scope() as session:
            session.get(Video, video_id).voice_json = payload
        return payload
    # Stable direction on a full-job retry keeps narration cache identity stable.
    with session_scope() as session:
        saved = dict(session.get(Video, video_id).voice_json or {})
    if options.get("_resume_voice") and saved and spoken_text(saved.get("tagged_transcript", "")) == spoken_text(plain) and saved.get("voice") == fallback_voice and abs(float(saved.get("speech_tempo", 0)) - float(options["speech_tempo"])) < 0.001:
        return saved

    if not options.get("agent_directs_voice", True):
        return {
            "voice": fallback_voice, "cast": [], "multi_speaker": False,
            "tagged_transcript": plain, "speech_tempo": float(options["speech_tempo"]),
            "direction": {
                "audio_profile": channel["strategy_json"].get("voice", "clear narrator"),
                "scene": "A quiet treated room, close to the microphone.",
                "directors_notes": f"Pace: {options['pace_note']}. Keep momentum forward and land the "
                                   f"final line cleanly.",
                "sample_context": "Short-form vertical video narration.",
            },
            "source": "operator",
        }

    set_state(video_id, "VOICE", f"casting and directing the {language_name(language)} read")
    direction = roles.direct_voice(channel, premise, script, language, beats, options=options)

    ok, note = roles.validate_tagged(direction, beats)
    if not ok:
        # Retry once with the failure spelled out; if it drifts again, perform the plain script.
        log.warning("voice direction rejected: %s", note)
        direction = roles.direct_voice(channel, premise, script, language, beats,
                                       options={**options, "must_avoid": f"{note}. Repeat the transcript exactly."})
        ok, note = roles.validate_tagged(direction, beats)
    if not ok:
        record(video_id, "audio", "voice_direction_rejected", {"note": note},
               "Tagged transcript changed the words; performing the approved script untagged instead")
        tagged = plain
        cast: list[dict] = []
        multi = False
    else:
        tagged = direction.tagged_transcript
        cast = [c.model_dump() for c in direction.cast]
        multi = direction.multi_speaker

    voice = options.get("voice") or (cast[0]["voice"] if cast else None) or fallback_voice
    payload = {
        "voice": voice,
        "cast": cast if multi else [],
        "multi_speaker": multi,
        "tagged_transcript": tagged,
        "speech_tempo": float(options.get("speech_tempo") or direction.speech_tempo),
        "direction": {
            "audio_profile": direction.audio_profile,
            "scene": direction.scene,
            "directors_notes": direction.directors_notes,
            "sample_context": direction.sample_context,
        },
        "source": "agent",
        "validated": ok,
    }
    record(video_id, "audio", "voice_direction", payload, direction.reasoning or note)
    with session_scope() as session:
        session.get(Video, video_id).voice_json = payload
    return payload


def stage_narrate(video_id: str, channel: dict, beats: list[dict], voice_plan: dict, language: str,
                  force: bool = False, playback_rate: float | None = None) -> dict:
    """Synthesize narration, then measure per-shot spans from the final paced waveform."""
    work = work_dir(video_id)
    set_state(video_id, "NARRATION",
              f"{language_name(language)} · {voice_plan['voice']} · {voice_plan['speech_tempo']:.2f}x")

    narration = work / "narration.wav"
    if force and playback_rate is None:
        narration.unlink(missing_ok=True)
    if playback_rate is not None:
        from .audio import pace_and_trim
        if not 0.75 <= playback_rate <= 1.25:
            raise PipelineError("Playback speed must be between 0.75 and 1.25")
        base = work / "narration-speed-base.wav"
        if not base.exists():
            import shutil
            shutil.copy2(narration, base)
        total = pace_and_trim(base, narration, playback_rate, 120, 250)
        previous = json.loads((work / 'timeline.json').read_text(encoding='utf-8')).get('tts', {})
        result = {**previous, "duration": total, "transport": "retimed_existing_audio", "playback_rate": playback_rate}
    else:
        if force:
            (work / 'narration-speed-base.wav').unlink(missing_ok=True)
        result = synthesize(
        video_id=video_id,
        tagged_transcript=voice_plan["tagged_transcript"],
        destination=narration,
        voice=voice_plan["voice"],
        direction=voice_plan["direction"],
        language=language,
        cast=voice_plan.get("cast") or None,
        tempo=float(voice_plan["speech_tempo"]),
        force=force,
    )
    total = float(result["duration"])
    if playback_rate is None and not 45 <= total <= 90:
        # Fit the final waveform BEFORE ASR; no audio speed changes after word timestamps exist.
        from .audio import pace_and_trim
        paced = work / "narration-fit.wav"
        # Preserve natural delivery through 90 seconds; only tiny timing corrections outside it.
        target = 89.5 if total > 90 else 45.0
        ratio = total / target
        if not 0.85 <= ratio <= 1.05:
            raise PipelineError(f"Narration is {total:.1f}s; shorten the script rather than rushing speech")
        total = pace_and_trim(narration, paced, ratio, 120, 250)
        paced.replace(narration)
        if not 45 <= total <= 90:
            raise PipelineError(f"Duration gate failed after pacing: {total:.2f}s")

    set_state(video_id, "ALIGNING", "measuring word timings from the audio")
    transcript = spoken_text(voice_plan["tagged_transcript"])
    alignment = align(narration, transcript, work, total, language=language)

    counts = [len(b["narration"].split()) for b in beats]
    spans, kept = beat_spans(alignment["reference"], counts, total)
    if len(spans) < 20:
        # Preserve every approved visual; scene timing may be evenly distributed, captions never are.
        kept = list(range(len(beats)))
        spans = [(i * total / len(beats), (i + 1) * total / len(beats)) for i in kept]

    payload = {
        "language": language,
        "duration": round(total, 3),
        "timing_source": alignment["source"],
        "caption_source": alignment["caption_source"],
        "match_ratio": alignment["match_ratio"],
        "spans": [[round(s, 4), round(e, 4)] for s, e in spans],
        "kept": kept,
        "shots": [
            {"shot_id": beats[i]["shot_id"], "narration": beats[i]["narration"], "duration": round(e - s, 2)}
            for i, (s, e) in zip(kept, spans)
        ],
        "alignment": alignment,
        "tts": {k: v for k, v in result.items() if k != "path"},
    }
    (work / "timeline.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    record(video_id, "audio", "narration",
           {k: payload[k] for k in ("timing_source", "caption_source", "match_ratio", "duration", "shots")},
           "Shot spans derived from measured narration, never from guessed durations")
    set_state(video_id, "ALIGNING", f"{len(spans)} shots over {total:.1f}s",
              narration_seconds=round(total, 2), narration_path=str(narration))
    from .storage import checkpoint
    checkpoint(video_id)
    return payload


# ------------------------------------------------------------- stage 6-7: visuals and image buying


def _decorate(prompt: str, style_block: str) -> str:
    text = prompt
    if style_block and style_block.lower() not in text.lower():
        text = f"{text} {style_block}"
    if "no text" not in text.lower():
        text = f"{text} Vertical 9:16 composition, no text, no captions, no watermark, no logo."
    return " ".join(text.split())


def stage_visuals(video_id: str, channel: dict, premise: Premise, script: Script, measured: dict,
                  options: dict) -> dict:
    set_state(video_id, "VISUALS", "writing image prompts")
    kept_ids = [shot["shot_id"] for shot in measured["shots"]]
    beats = [b.model_dump() for b in script.beats if b.shot_id in set(kept_ids)]
    plan = roles.plan_visuals(channel, premise, beats, options=options)
    by_shot = {shot.shot_id: shot for shot in plan.shots}

    missing = [s for s in kept_ids if s not in by_shot]
    if missing:
        raise PipelineError(f"Visual agent skipped shots: {missing[:5]}")

    shots = [
        {
            "shot_id": shot_id,
            "prompt": _decorate(by_shot[shot_id].image_prompt, plan.style_block),
            "subject_type": by_shot[shot_id].subject_type,
            "action_tag": by_shot[shot_id].action_tag,
            "setting_tag": by_shot[shot_id].setting_tag,
            "character_refs": by_shot[shot_id].character_refs,
        }
        for shot_id in kept_ids
    ]
    payload = {"character_bible": plan.character_bible, "style_block": plan.style_block, "shots": shots}
    (work_dir(video_id) / "visuals.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                                     encoding="utf-8")
    record(video_id, "visual", "visual_plan", plan.model_dump(), plan.reasoning)
    with session_scope() as session:
        session.get(Video, video_id).visual_json = payload
    return payload


def _save_asset(video_id: str, shot_id: str, path: Path, registry_id: str, reused: bool, shot: dict) -> None:
    metadata = {"prompt": shot["prompt"],
                "tags": {k: shot[k] for k in ("subject_type", "action_tag", "setting_tag", "character_refs")}}
    with session_scope() as session:
        existing = session.query(Asset).filter_by(video_id=video_id, shot_id=shot_id, kind="image").first()
        if existing:
            existing.path = str(path)
            existing.registry_id = registry_id
            existing.reused = reused
            existing.metadata_json = metadata
            return
        session.add(Asset(video_id=video_id, shot_id=shot_id, kind="image", path=str(path),
                          registry_id=registry_id, reused=reused, metadata_json=metadata))


def stage_images(video_id: str, channel: dict, visuals: dict, only: list[str] | None = None) -> dict:
    """Buy images for shots that need one. Identical shots inside this video share a single image."""
    shots = [s for s in visuals["shots"] if not only or s["shot_id"] in set(only)]
    images_dir = work_dir(video_id) / "images"
    images_dir.mkdir(exist_ok=True)

    duplicates = {}  # Every beat receives a distinct visual for the 20–25 image pacing contract.
    fresh = [s for s in shots if s["shot_id"] not in duplicates]

    with session_scope() as session:
        model = (session.get(Video, video_id).options_json or {}).get('image_model') or cfg('models', 'image_model')
    per_image = credits_per_image(model)
    needed = sum(not (images_dir / f"{s['shot_id']}.png").exists() for s in fresh) * per_image
    if remaining_credits() < needed:
        raise PipelineError(
            f"Need {needed:.3f} credits for {len(fresh)} images on {cfg('models', 'image_model')}; "
            f"only {remaining_credits():.3f} remain"
        )

    set_state(video_id, "IMAGES", f"{len(fresh)} to generate, {len(duplicates)} shared within this video")

    failures: list[str] = []
    workers = max(1, int(cfg("runtime", "image_concurrency", default=4)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(generate_image, video_id, s["shot_id"], s["prompt"], images_dir / f"{s['shot_id']}.png", model): s
            for s in fresh
        }
        for future in as_completed(futures):
            shot = futures[future]
            try:
                future.result()
            except Exception as error:  # noqa: BLE001
                failures.append(f"{shot['shot_id']}: {error}")
                continue
            path = images_dir / f"{shot['shot_id']}.png"
            asset_id = registry.register_asset(
                video_id, channel["slug"], shot["shot_id"], path, shot["prompt"],
                shot["subject_type"], shot["action_tag"], shot["setting_tag"], shot["character_refs"],
            )
            _save_asset(video_id, shot["shot_id"], path, asset_id, False, shot)
            from .storage import checkpoint
            checkpoint(video_id)

    if failures:
        raise PipelineError(f"{len(failures)} image(s) failed: " + "; ".join(failures[:3]))

    # copy the shared images into place, and log why each one was shared
    by_shot = {s["shot_id"]: s for s in shots}
    for duplicate_id, source_id in duplicates.items():
        source = images_dir / f"{source_id}.png"
        if not source.exists():
            raise PipelineError(f"Shot {duplicate_id} was matched to {source_id}, whose image is missing")
        target = images_dir / f"{duplicate_id}.png"
        target.write_bytes(source.read_bytes())
        shot = by_shot[duplicate_id]
        asset_id = registry.register_asset(
            video_id, channel["slug"], duplicate_id, target, shot["prompt"],
            shot["subject_type"], shot["action_tag"], shot["setting_tag"], shot["character_refs"],
        )
        _save_asset(video_id, duplicate_id, target, asset_id, True, shot)

    if duplicates:
        record(video_id, "visual", "asset_reuse",
               {"shared": duplicates, "generated": len(fresh), "credits_saved": round(len(duplicates) * per_image, 5)},
               "Shots with identical visible tags share one image; reuse never crosses stories")
    return {"generated": len(fresh), "reused": len(duplicates)}


# ---------------------------------------------------------------- stage 8-11: captions and render


def apply_edit_plan(shot_ids: list[str], plan: EditPlan) -> list[dict]:
    order = {shot_id: index for index, shot_id in enumerate(shot_ids)}
    ordered = [{"kind": "hard_cut", "duration_ms": 0, "why": ""} for _ in shot_ids]
    for choice in plan.transitions:
        normalised = choice.normalised()
        if normalised.shot_id in order:
            ordered[order[normalised.shot_id]] = normalised.model_dump()
    ordered[0] = {"kind": "hard_cut", "duration_ms": 0, "why": "opening shot"}
    return ordered


def build_video(video_id: str, channel: dict, plan: EditPlan, options: dict, beats: list[dict]) -> dict:
    """The single canonical path from measured narration to a finished, sync-validated file.

    Captions, spans, the timeline and the scene clips are all re-derived here from `timeline.json`, so
    this is correct whether it is a first render or a re-render after a tempo, voice or image change.
    """
    work = work_dir(video_id)
    timeline_path = work / "timeline.json"
    if not timeline_path.exists():
        raise PipelineError("No measured timeline; regenerate narration before rendering")
    measured = json.loads(timeline_path.read_text(encoding="utf-8"))

    spans = [(float(a), float(b)) for a, b in measured["spans"]]
    total = float(measured["duration"])
    shot_ids = [shot["shot_id"] for shot in measured["shots"]]
    language = measured.get("language", "en")

    ordered = apply_edit_plan(shot_ids, plan)
    record(video_id, "editing", "edit_plan", plan.model_dump(), plan.reasoning)
    timeline = build_timeline(spans, ordered, int(cfg("video", "fps")), total)

    set_state(video_id, "CAPTIONS", "burning word-level captions")
    narration = work / "narration.wav"
    if not narration.exists():
        raise PipelineError("Narration audio is missing; regenerate narration before rendering")

    _, pitches = analyze_audio(narration, work)
    alignment = measured.get("alignment") or {}
    if not alignment.get("captions"):
        transcript = spoken_text(measured.get("tagged_transcript", "")) or " ".join(
            b["narration"] for b in beats
        )
        alignment = align(narration, transcript, work, total, language=language)
        measured["alignment"] = alignment
        timeline_path.write_text(json.dumps(measured, indent=2, ensure_ascii=False), encoding="utf-8")

    kept = set(shot_ids)
    emphasis = {
        normalized(word)
        for beat in beats if beat["shot_id"] in kept
        for word in beat.get("emphasis_words", [])
    }
    captions = work / "captions.ass"
    from .english_captions import english_captions
    subtitle_words = english_captions(alignment["captions"], language, work)
    translated_captions = language.lower().replace('_', '-').split('-')[0] != 'en'
    caption_stats = write_ass(subtitle_words, pitches, emphasis, captions, total, phrase_mode=translated_captions)
    caption_stats['enabled'] = True
    caption_stats['policy'] = 'english_for_all_languages'
    caption_stats['timing_mode'] = 'translated_phrase' if translated_captions else 'word'
    timing_report(work / "caption-timing.json", alignment, spans, pitches, caption_stats)

    set_state(video_id, "RENDERING", f"{len(shot_ids)} shots, {timeline.duration:.1f}s")
    images = [work / "images" / f"{shot_id}.png" for shot_id in shot_ids]
    absent = [p.name for p in images if not p.exists()]
    if absent:
        raise PipelineError(f"Missing images: {absent[:4]}")

    track = registry.music_track(plan.music_track) if options.get("music_enabled", True) else None
    volume_pct = int(options.get("music_volume_pct") if options.get("music_volume_pct") is not None
                     else (track or {}).get("default_volume_pct", plan.music_volume_pct))
    intensity = registry.intensity_from_pct(volume_pct) if track else 0.0
    music_start = float(options.get('music_start_seconds') if options.get('music_start_seconds') is not None else (track or {}).get('trim_start', 0))
    music_end = float(options.get('music_end_seconds') if options.get('music_end_seconds') is not None else (track or {}).get('trim_end', 0))

    suffix = "" if language == cfg("languages", "primary", default="en") else f"-{language}"
    import uuid
    model_label = str(options.get('image_model') or cfg('models', 'image_model')).split('/')[-1]
    suffix += f"-{model_label}-{uuid.uuid4().hex[:8]}"
    output = boot().outputs / f"{channel['slug']}-{video_id[:8]}{suffix}.mp4"

    # Crop the selected region first; looping must never include audio outside it.
    from . import storage as _storage
    from .music_edit import prepared_music
    with prepared_music(track, music_start, music_end) as music_path:
        assemble(
            images=images, narration=narration, timeline=timeline, transitions=ordered, captions=captions,
            output=output, clips_dir=clips_dir(video_id),
            music=music_path,
            music_intensity=intensity, ducking=bool(options.get("ducking", True)), music_start=0,
        )

    # Upload finished video to Supabase Storage when credentials are present.
    # The local file is deleted after upload; output_path becomes the public URL.
    if _storage.enabled():
        storage_key = f"{channel['slug']}/{video_id[:8]}{suffix}.mp4"
        final_output_path = _storage.upload(
            _storage.VIDEOS_BUCKET, storage_key, output, delete_local=True
        )
        log.info("video uploaded to Supabase: %s", final_output_path)
    else:
        final_output_path = str(output)

    with session_scope() as session:
        assets = session.query(Asset).filter_by(video_id=video_id, kind="image").all()
        video = session.get(Video, video_id)
        video.output_path = final_output_path
        video.duration_seconds = round(timeline.duration, 2)
        video.image_count = len(images)
        video.reused_count = len([a for a in assets if a.reused])
        video.spec_json = {
            **(video.spec_json or {}),
            "shots": measured["shots"],
            "transitions": ordered,
            "music_track": (track or {}).get("id"),
            "music_name": (track or {}).get("name"),
            "music_volume_pct": volume_pct,
            "music_intensity": intensity,
            "music_start_seconds": music_start,
            "music_end_seconds": music_end,
            "ducking": bool(options.get("ducking", True)),
            "timing_source": alignment.get("source"),
            "caption_source": alignment.get("caption_source"),
            "match_ratio": alignment.get("match_ratio"),
            "captions": caption_stats,
            "speech_tempo": (video.voice_json or {}).get("speech_tempo"),
            "voice": measured.get("tts", {}).get("voice") or (video.voice_json or {}).get("voice"),
            "tts_model": measured.get("tts", {}).get("model") or cfg("models", "tts"),
            "multi_speaker": measured.get("tts", {}).get("multi_speaker", (video.voice_json or {}).get("multi_speaker", False)),
            "language": language,
        }
    (work / "shotlist.json").write_text(
        json.dumps({"shots": measured["shots"], "transitions": ordered}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"output": final_output_path, "duration": round(timeline.duration, 2), "shots": len(shot_ids),
            "captions": caption_stats}


def choose_edit(video_id: str, channel: dict, measured: dict, options: dict) -> EditPlan:
    set_state(video_id, "EDITING", "choosing transitions from measured durations")
    music = registry.music_library(channel["slug"]) if options.get("music_enabled", True) else []
    plan = roles.plan_edit(channel, measured["shots"], music, options=options)
    # Only use the owner's selected default/override, never substitute another bed.
    plan.music_track = options.get('music_track') or None
    if plan.music_track and not registry.music_track(plan.music_track):
        raise PipelineError('Default music is unavailable; choose another track in Music / BGM')
    return plan


def stage_copy(video_id: str, channel: dict, premise: Premise, script: Script, options: dict) -> dict:
    set_state(video_id, "COPY", "writing publishing copy")
    copy = roles.write_copy(channel, premise, script, options=options)
    record(video_id, "publishing", "copy", copy.model_dump(), copy.reasoning)
    with session_scope() as session:
        video = session.get(Video, video_id)
        video.title = copy.youtube_title
        video.description = copy.youtube_description
        video.instagram_caption = copy.instagram_caption
        video.hashtags = copy.hashtags
    return copy.model_dump()


def settle(video_id: str, channel: dict, premise: Premise | None, write_history: bool = True) -> dict:
    # The ledger is finalized before the video is exposed as READY. A failed render is handled by
    # run() below and is explicitly marked failed so the next retry can reserve a fresh concept.
    content_ledger.finalize_for_video(video_id, True)
    with session_scope() as session:
        video = session.get(Video, video_id)
        calls = session.query(ProviderCall).filter_by(video_id=video_id).all()
        image_model = (video.options_json or {}).get('image_model')
        if image_model:
            label = image_model.split('/')[-1]
            if not video.title.endswith(f'[{label}]'):
                import re
                video.title = re.sub(r'\s*\[[^\]]+\]$', '', video.title or '') + f' [{label}]'
        video.credits_spent = round(sum(c.credits for c in calls if c.status == "settled"), 6)
        video.text_tokens = int(sum(c.units for c in calls if c.provider == "gemini_text"))
        from .social import review_required
        video.approved = False  # A new edit must never inherit an older approval.
        video.state = "AWAITING_APPROVAL" if review_required(video) else "READY"
        video.progress = STAGES[video.state]
        video.error = ""
        if write_history and premise and not session.get(StoryHistory, video_id):
            session.add(StoryHistory(video_id=video_id, channel_slug=channel["slug"],
                                     premise_summary=premise.premise_summary,
                                     theme_tags=premise.theme_tags,
                                     plot_beat_summary=premise.plot_beat_summary))
        result = {"video_id": video_id, "language": video.language, "output": video.output_path,
                  "duration": video.duration_seconds, "images": video.image_count,
                  "reused": video.reused_count, "credits_spent": video.credits_spent,
                  "text_tokens": video.text_tokens, "state": video.state}
        title = video.title
    if write_history and premise:
        memory.remember_video(channel["slug"], premise.premise_summary, title or premise.title_working,
                              premise.archetype)
    return result


# ------------------------------------------------------------------------------ language variants


def create_variant(parent_id: str, language: str) -> str:
    with session_scope() as session:
        parent = session.get(Video, parent_id)
        existing = session.query(Video).filter_by(parent_id=parent_id, language=language).first()
        if existing:
            return existing.id
        variant = Video(
            channel_slug=parent.channel_slug, parent_id=parent_id, language=language,
            state="QUEUED", topic=parent.topic, premise_json=parent.premise_json,
            script_json=parent.script_json, qa_json=parent.qa_json, visual_json=parent.visual_json,
            options_json=parent.options_json, made_for_kids=parent.made_for_kids,
        )
        session.add(variant)
        session.flush()
        return variant.id


def build_language_variant(parent_id: str, language: str) -> dict:
    """Same images, localised narration, re-timed cut. Costs one TTS call and zero image credits."""
    variant_id = create_variant(parent_id, language)
    with attribute_to(variant_id, f"variant:{language}"):
        with session_scope() as session:
            parent = session.get(Video, parent_id)
            channel = channel_dict(parent.channel_slug)
            premise = Premise.model_validate(parent.premise_json)
            script = Script.model_validate(parent.script_json)
            run_options = dict(parent.options_json or {})
            parent_copy = {"title": parent.title, "description": parent.description,
                           "instagram_caption": parent.instagram_caption, "hashtags": list(parent.hashtags or [])}
        options = resolve_options(channel, run_options)

        parent_work = work_dir(parent_id)
        from .storage import restore
        restore(parent_id)
        measured_parent = json.loads((parent_work / "timeline.json").read_text(encoding="utf-8"))
        kept_ids = [shot["shot_id"] for shot in measured_parent["shots"]]
        source_beats = [b.model_dump() for b in script.beats if b.shot_id in set(kept_ids)]

        set_state(variant_id, "TRANSLATING", f"localising into {language_name(language)}")
        translation = roles.translate(channel, script, source_beats, language, parent_copy)
        by_shot = {b.shot_id: b.narration for b in translation.beats}
        missing = [s for s in kept_ids if s not in by_shot]
        if missing:
            raise PipelineError(f"Translation skipped shots: {missing[:5]}")
        record(variant_id, "audio", "translation",
               {"language": language, "beats": len(translation.beats)}, translation.reasoning)

        beats = [
            {"shot_id": b["shot_id"], "narration": by_shot[b["shot_id"]],
             "speaker": b.get('speaker',''),
             "emphasis_words": [], "scene_note": b.get("scene_note", "")}
            for b in source_beats
        ]

        # the images already exist on the parent: hard-link or copy them into the variant's work dir
        work = work_dir(variant_id)
        (work / "images").mkdir(parents=True, exist_ok=True)
        for shot_id in kept_ids:
            source = parent_work / "images" / f"{shot_id}.png"
            target = work / "images" / f"{shot_id}.png"
            if source.exists() and not target.exists():
                target.write_bytes(source.read_bytes())
        (work / "visuals.json").write_bytes((parent_work / "visuals.json").read_bytes())
        with session_scope() as session:
            for asset in session.query(Asset).filter_by(video_id=parent_id, kind="image").all():
                session.add(Asset(video_id=variant_id, shot_id=asset.shot_id, kind="image",
                                  path=str(work / "images" / f"{asset.shot_id}.png"),
                                  registry_id=asset.registry_id, reused=True,
                                  metadata_json=asset.metadata_json))

        voice_plan = stage_voice(variant_id, channel, premise, script, beats, language, options)
        measured = stage_narrate(variant_id, channel, beats, voice_plan, language)

        plan = choose_edit(variant_id, channel, measured, options)
        build_video(variant_id, channel, plan, options, beats)

        with session_scope() as session:
            variant = session.get(Video, variant_id)
            variant.title = translation.title or parent_copy["title"]
            variant.description = translation.description or parent_copy["description"]
            variant.instagram_caption = translation.instagram_caption or parent_copy["instagram_caption"]
            variant.hashtags = parent_copy["hashtags"]
        return settle(variant_id, channel, None, write_history=False)


# -------------------------------------------------------------------------------- full pipeline


def produce(video_id: str) -> dict:
    with attribute_to(video_id, "produce"):
        with session_scope() as session:
            video = session.get(Video, video_id)
            if not video:
                raise PipelineError("No such video")
            channel = channel_dict(video.channel_slug)
            run_options = dict(video.options_json or {})

        options = resolve_options(channel, run_options)
        language = options.get("primary_language") or "en"
        options["_resume_voice"] = True
        with session_scope() as session:
            session.get(Video, video_id).language = language

        premise, script, _finding = stage_story(video_id, channel, options)
        beats = [b.model_dump() for b in script.beats]

        voice_plan = stage_voice(video_id, channel, premise, script, beats, language, options)
        measured = stage_narrate(video_id, channel, beats, voice_plan, language)
        kept = {s["shot_id"] for s in measured["shots"]}
        beats = [b for b in beats if b["shot_id"] in kept]

        with session_scope() as session:
            saved_visuals = dict(session.get(Video, video_id).visual_json or {})
        visuals = saved_visuals if saved_visuals.get("shots") else stage_visuals(video_id, channel, premise, script, measured, options)
        stage_images(video_id, channel, visuals)

        plan = choose_edit(video_id, channel, measured, options)
        build_video(video_id, channel, plan, options, beats)
        stage_copy(video_id, channel, premise, script, options)
        result = settle(video_id, channel, premise)

    variants = []
    for extra in options.get("languages") or []:
        if extra == language:
            continue
        try:
            variants.append(build_language_variant(video_id, extra))
        except Exception as error:  # noqa: BLE001
            # one failed localisation must not invalidate a finished primary video
            log.error("language variant %s failed: %s", extra, error)
            variants.append({"language": extra, "error": str(error)[:400]})
    result["variants"] = variants
    # The worker routes publishing only after all source/output assets are persisted.
    return result


def run(video_id: str) -> dict:
    try:
        return produce(video_id)
    except Exception:
        try:
            content_ledger.finalize_for_video(video_id, False)
        except Exception:  # noqa: BLE001
            log.exception("could not finalize failed content reservation for %s", video_id)
        raise
