"""Targeted regeneration.

Every action here ends by re-entering `pipeline.build_video`, which re-derives the alignment, the
captions, the shot spans, the timeline and the scene clips from the current narration. That is the
whole design: there is no "patch the existing cut" path that could leave captions from one take
sitting on audio from another.

    regenerate_image     one image        1 image credit, then a full rebuild
    regenerate_visuals   listed shots     1 credit per shot, then a full rebuild
    regenerate_narration voice/pace/tags  1 TTS call, re-aligned and re-captioned from scratch
    regenerate_script    new words        1 TTS call + images for any genuinely new shot
    regenerate_edit      transitions/BGM  free
    regenerate_copy      title/caption    free
    rerender             nothing new      free
    add_language         one localisation 1 TTS call, zero image credits

Scene clips are content-addressed, so a tempo change automatically produces new clip names rather
than reusing the old ones. Invalidation is belt-and-braces on top of that.
"""
import json
import logging
from pathlib import Path

from . import pipeline, registry
from .agents import roles
from .db import session_scope
from .llm import attribute_to
from .models import Asset, Video
from .schemas import EditPlan, Premise, Script, TransitionChoice
from .settings_store import cfg, credits_per_image

log = logging.getLogger("regenerate")


def _load(video_id: str):
    from .storage import restore
    restore(video_id)
    with session_scope() as session:
        video = session.get(Video, video_id)
        if not video:
            raise pipeline.PipelineError("No such video")
        channel = pipeline.channel_dict(video.channel_slug)
        premise = Premise.model_validate(video.premise_json) if video.premise_json else None
        script = Script.model_validate(video.script_json) if video.script_json else None
        return channel, premise, script, dict(video.spec_json or {}), dict(video.options_json or {}), video.language


def _beats(video_id: str, script: Script | None) -> list[dict]:
    """The beats that survived alignment, in the language this video was actually narrated in."""
    work = pipeline.work_dir(video_id)
    measured = json.loads((work / "timeline.json").read_text(encoding="utf-8"))
    emphasis = {b.shot_id: b.emphasis_words for b in (script.beats if script else [])}
    return [
        {"shot_id": s["shot_id"], "narration": s["narration"],
         "emphasis_words": emphasis.get(s["shot_id"], []), "scene_note": ""}
        for s in measured["shots"]
    ]


def _saved_plan(video_id: str, spec: dict, shot_ids: list[str], overrides: dict | None = None) -> EditPlan:
    """Rebuild the last edit plan, then apply whatever the operator changed in the UI."""
    overrides = overrides or {}
    saved = spec.get("transitions") or []
    if saved and len(saved) == len(shot_ids):
        transitions = [
            TransitionChoice(shot_id=shot_ids[i], kind=t.get("kind", "hard_cut"),
                             duration_ms=int(t.get("duration_ms") or 0), why=t.get("why", ""))
            for i, t in enumerate(saved)
        ]
    else:
        transitions = [TransitionChoice(shot_id=s, kind="hard_cut", duration_ms=0) for s in shot_ids]

    if overrides.get("transitions"):
        supplied = {t["shot_id"]: t for t in overrides["transitions"] if t.get("shot_id")}
        transitions = [
            TransitionChoice(**{**t.model_dump(), **supplied[t.shot_id]}) if t.shot_id in supplied else t
            for t in transitions
        ]

    volume = overrides.get("music_volume_pct", spec.get("music_volume_pct"))
    return EditPlan(
        transitions=transitions,
        music_track=overrides.get("music_track", spec.get("music_track")),
        music_volume_pct=int(volume if volume is not None else cfg("music", "default_volume_pct", default=30)),
        music_start_seconds=float(overrides.get("music_start_seconds", spec.get("music_start_seconds") or 0.0)),
        reasoning=overrides.get("reason", "Operator edit from the dashboard"),
    )


def _rebuild(video_id: str, channel: dict, spec: dict, run_options: dict, script: Script | None,
             overrides: dict | None = None, plan: EditPlan | None = None) -> dict:
    work = pipeline.work_dir(video_id)
    measured = json.loads((work / "timeline.json").read_text(encoding="utf-8"))
    shot_ids = [shot["shot_id"] for shot in measured["shots"]]
    options = pipeline.resolve_options(channel, {**run_options, **(overrides or {})})
    plan = plan or _saved_plan(video_id, spec, shot_ids, overrides)
    result = pipeline.build_video(video_id, channel, plan, options, _beats(video_id, script))
    return {**result, **pipeline.settle(video_id, channel, None, write_history=False)}


def _invalidate_clips(video_id: str, shot_ids: list[str] | None = None) -> int:
    """Drop cached scene clips. Content addressing already prevents staleness; this reclaims space."""
    clips = pipeline.clips_dir(video_id)
    if not clips.exists():
        return 0
    patterns = [f"{shot_id}-*.mp4" for shot_id in shot_ids] if shot_ids else ["*.mp4"]
    removed = 0
    for pattern in patterns:
        for clip in clips.glob(pattern):
            clip.unlink(missing_ok=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------------- free actions


def change_speed(video_id: str, payload: dict) -> dict:
    """Re-time saved audio, re-transcribe and re-render; never generate images or new speech."""
    rate = float(payload.get("playback_rate", 0.94))
    if not 0.75 <= rate <= 1.25:
        raise ValueError("Playback speed must be between 0.75 and 1.25")
    channel, premise, script, spec, options, language = _load(video_id)
    work = pipeline.work_dir(video_id)
    beats = _beats(video_id, script)
    if any(not (work / 'images' / (b['shot_id'] + '.png')).exists() for b in beats):
        raise pipeline.PipelineError("A saved image is missing; speed changes never purchase replacements")
    with session_scope() as session:
        voice = dict(session.get(Video, video_id).voice_json or {})
    # Reuse measured timestamps; an audio speed edit must not call TTS/ASR/text providers.
    from . import media
    import copy
    import hashlib
    import shutil
    base = work / 'narration-speed-base.wav'
    timing_base = work / 'speed-timeline-base.json'
    if not base.exists() or not timing_base.exists():
        shutil.copy2(work / 'narration.wav', base)
        shutil.copy2(work / 'timeline.json', timing_base)
        if (work / 'english-captions.json').exists():
            shutil.copy2(work / 'english-captions.json', work / 'speed-english-base.json')
        else:
            (work / 'speed-english-base.json').unlink(missing_ok=True)
    measured = json.loads(timing_base.read_text(encoding='utf-8'))
    if not measured.get('alignment', {}).get('captions'):
        raise pipeline.PipelineError('Saved alignment is required for a provider-free speed change')
    expected = float(measured['duration']) / rate
    media.run([media.binary('ffmpeg'),'-y','-v','error','-i',str(base),'-af',f'atempo={rate},apad,atrim=duration={expected:.9f}',
               '-c:a','pcm_s16le',str(work / 'narration.wav')])
    total = media.seconds(work / 'narration.wav')
    ratio = total / float(measured['duration'])
    def scaled(value):
        if isinstance(value, list):
            return [scaled(item) for item in value]
        if isinstance(value, dict):
            return {key: (float(item)*ratio if key in ('start','end','duration') and isinstance(item,(int,float)) else scaled(item))
                    for key,item in value.items()}
        return value
    measured = scaled(copy.deepcopy(measured))
    measured['spans'] = [[a*ratio,b*ratio] for a,b in measured['spans']]
    measured['duration'] = total
    measured['tts'] = {**measured.get('tts',{}),'transport':'retimed_existing_audio','playback_rate':rate}
    (work / 'timeline.json').write_text(json.dumps(measured),encoding='utf-8')
    translated = work / 'speed-english-base.json'
    if translated.exists():
        saved = json.loads(translated.read_text(encoding='utf-8'))
        signature = hashlib.sha256(json.dumps([language, measured['alignment']['captions'], 'english-phrases-v1'],ensure_ascii=False).encode()).hexdigest()
        (work / 'english-captions.json').write_text(json.dumps({'signature':signature,'captions':scaled(saved['captions'])}),encoding='utf-8')
    with session_scope() as session:
        row = session.get(Video, video_id)
        row.options_json = {**(row.options_json or {}), 'playback_rate': rate}
    _invalidate_clips(video_id)
    result = _rebuild(video_id, channel, spec, {**options, '_reuse_assets':True}, script)
    return {**result, 'playback_rate': rate, 'images_added': 0, 'narration_seconds': measured['duration']}


def rerender(video_id: str, payload: dict | None = None) -> dict:
    """Re-run captions, timeline and FFmpeg. Nothing is regenerated, nothing is charged."""
    channel, _premise, script, spec, run_options, _language = _load(video_id)
    return _rebuild(video_id, channel, spec, {**run_options, '_reuse_assets':True}, script, payload or {})


def regenerate_edit(video_id: str, payload: dict | None = None) -> dict:
    """New transitions and/or new music. Free: no provider call is made."""
    payload = payload or {}
    channel, _premise, script, spec, run_options, _language = _load(video_id)
    work = pipeline.work_dir(video_id)
    measured = json.loads((work / "timeline.json").read_text(encoding="utf-8"))

    plan = None
    if payload.get("ask_agent"):
        raise pipeline.PipelineError('Reuse edits are provider-free. Choose transitions/music manually.')
    return _rebuild(video_id, channel, spec, {**run_options, '_reuse_assets':True}, script, payload, plan)


def regenerate_copy(video_id: str, payload: dict | None = None) -> dict:
    """New title, description and caption only."""
    channel, premise, script, _spec, run_options, _language = _load(video_id)
    if not premise or not script:
        raise pipeline.PipelineError("This video has no saved script to write copy from")
    with attribute_to(video_id, "regenerate_copy"):
        options = pipeline.resolve_options(channel, {**run_options, **(payload or {})})
        return pipeline.stage_copy(video_id, channel, premise, script, options)


# --------------------------------------------------------------------------------- paid actions


def regenerate_image(video_id: str, payload: dict) -> dict:
    """Regenerate exactly one shot's image, then rebuild the film around it."""
    shot_id = payload.get("shot_id")
    if not shot_id:
        raise pipeline.PipelineError("shot_id is required")
    channel, _premise, script, spec, run_options, _language = _load(video_id)
    work = pipeline.work_dir(video_id)
    visuals = json.loads((work / "visuals.json").read_text(encoding="utf-8"))

    shot = next((s for s in visuals["shots"] if s["shot_id"] == shot_id), None)
    if not shot:
        raise pipeline.PipelineError(f"Shot {shot_id} is not part of this video")

    if payload.get("prompt"):
        shot = {**shot, "prompt": pipeline._decorate(payload["prompt"], visuals.get("style_block", ""))}
    else:
        # A different prompt means a different idempotency key, which is what buys a new picture.
        variation = int(payload.get("variation") or 2)
        shot = {**shot, "prompt": f"{shot['prompt']} [alternate take {variation}]"}

    visuals["shots"] = [shot if s["shot_id"] == shot_id else s for s in visuals["shots"]]
    (work / "visuals.json").write_text(json.dumps(visuals, indent=2, ensure_ascii=False), encoding="utf-8")
    with session_scope() as session:
        session.get(Video, video_id).visual_json = visuals

    (work / "images" / f"{shot_id}.png").unlink(missing_ok=True)
    with attribute_to(video_id, "regenerate_image"):
        pipeline.stage_images(video_id, channel,
                              {"shots": [shot], "style_block": visuals.get("style_block", "")}, only=[shot_id])
    _invalidate_clips(video_id, [shot_id])
    result = _rebuild(video_id, channel, spec, run_options, script)
    return {**result, "regenerated_shot": shot_id, "credits_charged": credits_per_image()}


def regenerate_visuals(video_id: str, payload: dict) -> dict:
    """Rewrite prompts and buy new images for a listed subset of shots."""
    shot_ids = payload.get("shot_ids") or []
    if not shot_ids:
        raise pipeline.PipelineError("shot_ids is required; use rerender for a free re-render")
    channel, premise, script, spec, run_options, _language = _load(video_id)
    work = pipeline.work_dir(video_id)
    measured = json.loads((work / "timeline.json").read_text(encoding="utf-8"))

    with attribute_to(video_id, "regenerate_visuals"):
        options = pipeline.resolve_options(channel, {**run_options, **payload})
        fresh = pipeline.stage_visuals(video_id, channel, premise, script, measured, options)
        subset = {"style_block": fresh.get("style_block", ""),
                  "shots": [s for s in fresh["shots"] if s["shot_id"] in set(shot_ids)]}
        for shot_id in shot_ids:
            (work / "images" / f"{shot_id}.png").unlink(missing_ok=True)
        _invalidate_clips(video_id, shot_ids)
        pipeline.stage_images(video_id, channel, subset, only=shot_ids)

    result = _rebuild(video_id, channel, spec, run_options, script)
    return {**result, "regenerated_shots": shot_ids,
            "credits_charged": round(len(shot_ids) * credits_per_image(), 5)}


def regenerate_narration(video_id: str, payload: dict | None = None) -> dict:
    """New voice, pacing or performance. One TTS call, then a complete re-time from the new audio.

    This is the path that used to drift: changing the tempo changes every word timing, every shot
    span and every clip length. Nothing is carried over from the previous take except the images.
    """
    payload = payload or {}
    channel, premise, script, spec, run_options, language = _load(video_id)
    if not script:
        raise pipeline.PipelineError("This video has no saved script")

    language = payload.get("language") or language or "en"
    merged = {**run_options, **{k: v for k, v in payload.items() if v not in (None, "")}}
    merged.pop('playback_rate', None)
    with session_scope() as session:
        session.get(Video, video_id).options_json = merged
    options = pipeline.resolve_options(channel, merged)

    with attribute_to(video_id, "regenerate_narration"):
        beats = _beats(video_id, script)
        if payload.get("keep_direction"):
            with session_scope() as session:
                voice_plan = dict(session.get(Video, video_id).voice_json or {})
            if payload.get("voice"):
                voice_plan["voice"] = payload["voice"]
            if payload.get("speech_tempo"):
                voice_plan["speech_tempo"] = float(payload["speech_tempo"])
            if not voice_plan.get("tagged_transcript"):
                voice_plan = pipeline.stage_voice(video_id, channel, premise, script, beats, language, options)
        else:
            voice_plan = pipeline.stage_voice(video_id, channel, premise, script, beats, language, options)

        measured = pipeline.stage_narrate(video_id, channel, beats, voice_plan, language, force=True)

        # re-timing can change which shots survive; buy images only for genuinely new ones
        surviving = {shot["shot_id"] for shot in measured["shots"]}
        work = pipeline.work_dir(video_id)
        visuals = json.loads((work / "visuals.json").read_text(encoding="utf-8"))
        have = {s["shot_id"] for s in visuals["shots"] if (work / "images" / f"{s['shot_id']}.png").exists()}
        missing = sorted(surviving - have)
        if missing:
            log.info("re-alignment needs %d new image(s)", len(missing))
            pipeline.stage_images(
                video_id, channel,
                {"style_block": visuals.get("style_block", ""),
                 "shots": [s for s in visuals["shots"] if s["shot_id"] in set(missing)]},
                only=missing,
            )

    _invalidate_clips(video_id)
    beats = _beats(video_id, script)
    result = _rebuild(video_id, channel, spec, merged, script)
    return {**result, "narration_seconds": measured["duration"],
            "timing_source": measured["timing_source"], "caption_source": measured["caption_source"],
            "images_added": len(missing)}


def regenerate_script(video_id: str, payload: dict | None = None) -> dict:
    """Rewrite the words, then rebuild everything downstream. Costs one TTS call plus any new images."""
    payload = payload or {}
    channel, premise, _script, spec, run_options, language = _load(video_id)
    if not premise:
        raise pipeline.PipelineError("This video has no saved premise")

    merged = {**run_options, **{k: v for k, v in payload.items() if v not in (None, "")}}
    options = pipeline.resolve_options(channel, merged)

    with attribute_to(video_id, "regenerate_script"):
        with session_scope() as session:
            video = session.get(Video, video_id)
            previous = Script.model_validate(video.script_json)
            video.options_json = merged
        shot_target = len(previous.beats)
        seconds_per_beat = max(0.9, float(options["target_seconds"]) / shot_target)
        notes = [payload["notes"]] if payload.get("notes") else None

        script = roles.write_script(channel, premise, shot_target, seconds_per_beat,
                                    revision_notes=notes, options=options)
        finding = roles.review(channel, premise, script, options=options)
        pipeline.record(video_id, "qa", "qa", finding.model_dump(), finding.reasoning)
        if not finding.passed and not payload.get("skip_qa"):
            raise pipeline.PipelineError("QA rejected the rewrite: " + "; ".join(finding.findings[:3]))
        pipeline.record(video_id, "script", "script", script.model_dump(), script.reasoning)
        with session_scope() as session:
            session.get(Video, video_id).script_json = script.model_dump()

        beats = [b.model_dump() for b in script.beats]
        voice_plan = pipeline.stage_voice(video_id, channel, premise, script, beats, language, options)
        measured = pipeline.stage_narrate(video_id, channel, beats, voice_plan, language, force=True)
        kept = {s["shot_id"] for s in measured["shots"]}
        beats = [b for b in beats if b["shot_id"] in kept]

        visuals = pipeline.stage_visuals(video_id, channel, premise, script, measured, options)
        work = pipeline.work_dir(video_id)
        for shot in visuals["shots"]:
            (work / "images" / f"{shot['shot_id']}.png").unlink(missing_ok=True)
        _invalidate_clips(video_id)
        pipeline.stage_images(video_id, channel, visuals)

        plan = pipeline.choose_edit(video_id, channel, measured, options)
        pipeline.stage_copy(video_id, channel, premise, script, options)
        pipeline.build_video(video_id, channel, plan, options, beats)
    return pipeline.settle(video_id, channel, None, write_history=False)


def add_language(video_id: str, payload: dict) -> dict:
    """Produce one more localisation of a finished video. Same images, new audio and captions."""
    language = payload.get("language")
    if not language:
        raise pipeline.PipelineError("language is required")
    with session_scope() as session:
        video = session.get(Video, video_id)
        parent_id = video.parent_id or video_id
    return pipeline.build_language_variant(parent_id, language)


# ------------------------------------------------------------------------------------- helpers


def shot_view(video_id: str) -> list[dict]:
    """What the dashboard shows per shot: prompt, tags, image, reuse status, timing, transition."""
    work = pipeline.work_dir(video_id)
    timeline_path = work / "timeline.json"
    visuals_path = work / "visuals.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8")) if timeline_path.exists() else {"shots": []}
    visuals = json.loads(visuals_path.read_text(encoding="utf-8")) if visuals_path.exists() else {"shots": []}
    prompts = {s["shot_id"]: s for s in visuals.get("shots", [])}

    with session_scope() as session:
        assets = {a.shot_id: a for a in session.query(Asset).filter_by(video_id=video_id, kind="image").all()}
        video = session.get(Video, video_id)
        spec = dict(video.spec_json or {})
        if not timeline.get("shots"):
            timeline = {"shots": spec.get("shots", [])}
        if not prompts:
            prompts = {s["shot_id"]: s for s in (video.visual_json or {}).get("shots", [])}

    transitions = spec.get("transitions") or []
    rows = []
    for index, shot in enumerate(timeline.get("shots", [])):
        shot_id = shot["shot_id"]
        visual = prompts.get(shot_id, {})
        asset = assets.get(shot_id)
        rows.append({
            "index": index,
            "shot_id": shot_id,
            "narration": shot["narration"],
            "duration": shot["duration"],
            "prompt": visual.get("prompt", ""),
            "subject_type": visual.get("subject_type", ""),
            "action_tag": visual.get("action_tag", ""),
            "setting_tag": visual.get("setting_tag", ""),
            "character_refs": visual.get("character_refs", []),
            "reused": bool(asset.reused) if asset else False,
            "has_image": bool(asset and (asset.path.startswith("https://") or Path(asset.path).exists())),
            "transition": transitions[index] if index < len(transitions) else {"kind": "hard_cut"},
        })
    return rows
