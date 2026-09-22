"""Deterministic portrait renderer. Agents author data; only this module authors FFmpeg.

Two production lessons are baked in here.

**Scene clips are content-addressed and verified.** A clip's filename contains a hash of everything
that determines its pixels: the source image, its frame count, and the zoom parameters. A cached clip
can therefore never be stale, and a changed tempo simply produces new filenames instead of silently
reusing old ones. Before the assembly command is built, every clip is re-checked on disk and
re-rendered if it is missing, empty or unreadable. That is what stops
"Error opening input file ... scene-000-48.mp4" — which happens when a cloud-synced folder, an
antivirus scanner or a failed earlier pass removes a file between render and assemble.

**Clips live in the work directory, not next to the output.** Outputs are frequently inside synced
folders (OneDrive, Dropbox); intermediates should not be.
"""
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

# Non-directional transitions only; centered zoom-in or reveal pull-back, never lateral swipes.
XFADE = {
    "crossfade": "fade",
    "dissolve": "dissolve",
    "dip_to_black": "fadeblack",
    "flash_white": "fadewhite",
    "zoom_out": "fade",  # incoming shot pulls back from its centered crop
}
ZERO_OVERLAP = {"hard_cut", "match_cut"}
TRANSITION_KINDS = set(XFADE) | ZERO_OVERLAP


def binary(name: str) -> str:
    from .settings_store import cfg

    configured = cfg("runtime", name + "_path", default=name)
    bundled = ROOT / "tools" / (name + ".exe")
    if configured == name and bundled.exists():
        return str(bundled)
    return shutil.which(configured) or configured


def run(args: list[str], cwd: Path | None = None, timeout: int = 1800) -> str:
    # Bound decoder/filter/encoder threads for Render's small memory budget.
    from .render_profile import policy, guarded_run
    fast = policy().fast
    if fast and Path(args[0]).stem.lower() == 'ffmpeg':
        threads = str(policy().threads)
        args = list(args)
        for i, arg in enumerate(args[:-1]):
            if arg in ('-threads', '-filter_threads', '-filter_complex_threads'):
                args[i + 1] = threads
        args = [args[0], '-threads', threads, '-filter_threads', threads, *args[1:]]
    elif Path(args[0]).stem.lower() == "ffmpeg":
        args = [args[0], "-threads", "1", "-filter_threads", "1", *args[1:]]
    try:
        result = guarded_run(args, cwd, timeout) if fast else subprocess.run(  # noqa: S603
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{args[0]} was not found. Install FFmpeg or set its path in Settings.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"FFmpeg timed out after {timeout}s") from error
    if result.returncode:
        if fast and (result.returncode in (-9,137) or 'Cannot allocate memory' in (result.stderr or '')):
            from .render_profile import MemoryPressure
            raise MemoryPressure('Fast media pass exhausted memory; using low_memory')
        raise RuntimeError("Media processing failed: " + (result.stderr or "")[-2500:])
    return result.stdout


def probe(path: Path) -> dict:
    return json.loads(
        run([binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=120)
    )


def seconds(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def readable(path: Path, minimum_bytes: int = 2048) -> bool:
    """A clip counts as usable only if it exists, has real content and decodes."""
    try:
        if not path.exists() or path.stat().st_size < minimum_bytes:
            return False
        info = probe(path)
        return any(stream["codec_type"] == "video" for stream in info.get("streams", []))
    except Exception:  # noqa: BLE001
        return False


@dataclass
class Timeline:
    frames: list[int]
    blends: list[int]
    starts: list[int]
    total_frames: int
    fps: int

    @property
    def duration(self) -> float:
        return self.total_frames / self.fps


def build_timeline(spans, transitions, fps: int, total: float) -> Timeline:
    """Shot i becomes fully visible exactly when its narration slice begins.

        start[i]  = beat_start[i] - blend[i]
        frames[i] = beat_duration[i] + blend[i]

    so a blend lands on the first spoken word of its shot and the assembled picture is exactly as long
    as the narration. Frame counts are re-derived from the clamped blends, so a clamp can never leave
    the chain and the audio disagreeing.
    """
    count = len(spans)
    total_frames = max(1, round(total * fps))
    durations = [max(1, round((end - start) * fps)) for start, end in spans]

    blends = [0]
    for index in range(1, count):
        choice = transitions[index] if index < len(transitions) else {}
        kind = choice.get("kind", "hard_cut")
        if kind in ZERO_OVERLAP or kind not in TRANSITION_KINDS:
            blends.append(0)
            continue
        requested_ms = min(1500, max(100, int(choice.get("duration_ms") or 320)))
        limit_ms = int(0.5 * min(durations[index - 1], durations[index]) / fps * 1000)
        frames = round(min(requested_ms, limit_ms) / 1000 * fps)
        blends.append(frames if frames >= 2 else 0)

    raw_starts = [max(0, round(start * fps) - blends[index]) for index, (start, _) in enumerate(spans)]
    raw_starts[0] = 0

    frames = []
    for index in range(count):
        if index + 1 < count:
            frames.append(max(2, raw_starts[index + 1] + blends[index + 1] - raw_starts[index]))
        else:
            frames.append(max(2, total_frames - raw_starts[index]))

    cursor = 0
    starts = []
    for index in range(count):
        cursor -= blends[index]
        starts.append(max(0, cursor))
        cursor += frames[index]

    drift = total_frames - cursor
    if drift and count:
        frames[-1] = max(2, frames[-1] + drift)
        cursor = starts[-1] + frames[-1]
    return Timeline(frames=frames, blends=blends, starts=starts, total_frames=cursor, fps=fps)


def zoom_amount(frames: int, fps: int) -> float:
    """Zoom grows with screen time at a configurable speed, bounded so it never gets cartoonish."""
    from .settings_store import cfg

    speed = float(cfg("video", "zoom_speed", default=0.045))
    ceiling = float(cfg("video", "zoom_max", default=0.16))
    return max(0.0, min(ceiling, speed * frames / max(1, fps)))


def zoom_filter(frames: int, width: int, height: int, fps: int, zoom_out: bool = False) -> str:
    """Centered, bounded motion; zoom-out reveals the incoming scene without a lateral swipe."""
    amount = zoom_amount(frames, fps)
    position = f'(1-on/{max(1, frames - 1)})' if zoom_out else f'on/{max(1, frames - 1)}'
    return (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},setsar=1,"
        f"zoompan=z='1+{amount:.5f}*{position}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
        f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p,setsar=1"
    )


def clip_name(image: Path, frames: int, fps: int, width: int, height: int, zoom_out: bool = False) -> str:
    """Content address: same inputs, same file; different inputs, different file. Never stale."""
    try:
        stat = image.stat()
        signature = f"{image.name}:{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        signature = image.name
    digest = hashlib.sha1(
        f"{signature}|{frames}|{fps}|{width}x{height}|{zoom_amount(frames, fps):.5f}|{zoom_out}".encode()
    ).hexdigest()[:12]
    return f"{image.stem}-{frames}f-{digest}.mp4"


def render_clip(image: Path, frames: int, destination: Path, zoom_out: bool = False) -> Path:
    from .settings_store import cfg

    width, height, fps = cfg("video", "width"), cfg("video", "height"), cfg("video", "fps")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".partial.mp4")
    partial.unlink(missing_ok=True)
    run(
        [
            binary("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(image.resolve()),
            "-vf", zoom_filter(frames, width, height, fps, zoom_out),
            "-frames:v", str(frames), "-an",
            "-c:v", "libx264", "-threads", "1", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-r", str(fps), str(partial.resolve()),
        ]
    )
    if not readable(partial):
        raise RuntimeError(f"FFmpeg produced an unusable clip for {image.name}")
    partial.replace(destination)  # atomic: a half-written clip is never visible under its final name
    return destination


def ensure_clip(image: Path, frames: int, clips_dir: Path, zoom_out: bool = False) -> Path:
    """Return a verified clip, rebuilding it whenever the cached one is missing or unreadable."""
    if not image.exists():
        raise RuntimeError(f"Missing source image: {image.name}")
    from .settings_store import cfg

    destination = clips_dir / clip_name(
        image, frames, int(cfg("video", "fps")), int(cfg("video", "width")), int(cfg("video", "height")), zoom_out
    )
    if readable(destination):
        return destination
    destination.unlink(missing_ok=True)
    return render_clip(image, frames, destination, zoom_out)


def cfr_filter(fps: int) -> str:
    """Restore actual CFR metadata after trim/setpts/concat, before any xfade.

    Output -r cannot repair a filter link during graph configuration. Keep fps AFTER
    setpts: timestamp filters and concat can leave frame_rate unknown on some FFmpeg builds.
    AVTB gives both sides of each blend the same time base without retiming narration.
    """
    if not isinstance(fps, int) or fps <= 0:
        raise ValueError('Renderer frame rate must be a positive integer')
    return f'setpts=PTS-STARTPTS,fps={fps}:start_time=0,settb=AVTB'


def assemble(
    images: list[Path],
    narration: Path,
    timeline: Timeline,
    transitions: list[dict],
    captions: Path,
    output: Path,
    clips_dir: Path,
    music: Path | None = None,
    music_intensity: float = 0.08,
    ducking: bool = True,
    music_start: float = 0.0,
) -> Path:
    """Per-shot zoom clips -> transition chain -> burned captions -> narration (+ fitted music)."""
    from .settings_store import cfg

    if len(images) != len(timeline.frames):
        raise ValueError("Every shot needs exactly one image")
    music_intensity = max(0.0, min(0.5, float(music_intensity)))
    fps = timeline.fps
    clips_dir.mkdir(parents=True, exist_ok=True)

    pulls_back = [i < len(transitions) and transitions[i].get('kind') == 'zoom_out' for i in range(len(images))]
    from .render_profile import policy, using, MemoryPressure
    if policy().fast:
        try:
            pieces = direct_segments(images, timeline, transitions, clips_dir)
            return assemble_bounded(pieces, narration, captions, output, timeline, music,
                                    music_intensity, ducking, music_start)
        except MemoryPressure:
            # No provider calls and no changed output settings: retry with the proven two-input path.
            with using('low_memory'):
                return assemble(images, narration, timeline, transitions, captions, output, clips_dir,
                                music, music_intensity, ducking, music_start)
    clips = [ensure_clip(image, timeline.frames[index], clips_dir, pulls_back[index]) for index, image in enumerate(images)]

    # Re-verify immediately before the command is built: this window is where synced folders and
    # scanners have been observed to remove files out from under a render.
    for index, clip in enumerate(clips):
        if not readable(clip):
            clips[index] = ensure_clip(images[index], timeline.frames[index], clips_dir, pulls_back[index])
    missing = [clip.name for clip in clips if not clip.exists()]
    if missing:
        raise RuntimeError(f"Scene clips vanished before assembly: {missing[:4]}")

    from .config import boot
    # An explicit execution profile takes precedence over the legacy dashboard flag.
    # Keep the old non-bounded branch available only to pre-profile local installations.
    if cfg("runtime", "low_memory_render", default=True) or 'render_profile' in boot().model_fields_set:
        if any(timeline.blends):
            clips = transition_segments(clips, timeline, transitions, clips_dir)
        return assemble_bounded(clips, narration, captions, output, timeline, music,
                                music_intensity, ducking, music_start)

    args = [binary("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-filter_complex_threads", "1"]
    for clip in clips:
        args += ["-i", str(clip.resolve())]
    narration_index = len(clips)
    args += ["-i", str(narration.resolve())]

    duration = timeline.duration
    music_index = None
    if music and music.exists() and music_intensity > 0:
        music_index = narration_index + 1
        start = max(0.0, float(music_start))
        if start > 0:
            args += ["-ss", f"{start:.3f}"]
        # loop the bed so a 60-90 second track always covers the whole video
        args += ["-stream_loop", "-1", "-i", str(music.resolve())]

    timing = cfr_filter(fps)
    filters = [f"[{i}:v]{timing}[v{i}]" for i in range(len(clips))]
    video = "v0"
    for index in range(1, len(clips)):
        label = f"chain{index}"
        if not timeline.blends[index]:
            filters.append(f"[{video}][v{index}]concat=n=2:v=1:a=0,{timing}[{label}]")
        else:
            kind = transitions[index].get("kind", "crossfade") if index < len(transitions) else "crossfade"
            effect = XFADE.get(kind, "fade")
            filters.append(
                f"[{video}][v{index}]xfade=transition={effect}:"
                f"duration={timeline.blends[index] / fps:.4f}:offset={timeline.starts[index] / fps:.4f},"
                f"{timing}[{label}]"
            )
        video = label

    filters.append(
        f"[{video}]trim=duration={duration:.4f},setpts=PTS-STARTPTS,"
        f"subtitles=filename={captions.name}:fontsdir=fonts,"
        f"fade=t=in:d=0.25,fade=t=out:st={max(0.0, duration - 0.35):.4f}:d=0.35[film]"
    )

    voice = (
        f"[{narration_index}:a]aresample=48000:async=1:first_pts=0,"
        f"apad,atrim=duration={duration:.4f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:d=0.05,afade=t=out:st={max(0.0, duration - 0.3):.4f}:d=0.3"
    )
    if music_index is None:
        filters.append(voice + ",alimiter=limit=0.95[mix]")
    else:
        filters.append(voice + "[voice]")
        filters.append(
            f"[{music_index}:a]aresample=48000:first_pts=0,volume={music_intensity:.4f},"
            f"atrim=duration={duration:.4f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, duration - 0.8):.4f}:d=0.8[bed]"
        )
        if ducking:
            filters.append("[voice]asplit=2[voiceout][side]")
            filters.append(
                f"[bed][side]sidechaincompress="
                f"threshold={cfg('music', 'duck_threshold', default=0.025)}:"
                f"ratio={cfg('music', 'duck_ratio', default=8)}:attack=10:release=250[duck]"
            )
            filters.append("[voiceout][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[mix]")
        else:
            filters.append("[voice][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[mix]")

    (clips_dir / "assembly.filters").write_text(";\n".join(filters), encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".partial.mp4")
    partial.unlink(missing_ok=True)
    run(
        args + [
            "-filter_complex", ";".join(filters),
            "-map", "[film]", "-map", "[mix]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-fps_mode", "cfr",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-shortest", "-t", f"{duration:.4f}",
            str(partial.resolve()),
        ],
        cwd=captions.parent,
    )
    validate(partial, duration)
    partial.replace(output)
    return output


def direct_segments(images, timeline, transitions, work):
    """Bounded batches share prepared frames, avoiding full-scene intermediate encodes."""
    from .settings_store import cfg
    from .render_profile import policy
    width, height, fps = int(cfg('video','width')), int(cfg('video','height')), timeline.fps
    timing = cfr_filter(fps)
    # Leave a Python/muxing reserve, and budget 256 MiB per prepared scene.
    # On one CPU, the measured single-scene + boundary path beat larger batches.
    # More RAM is permitted, not allocated for its own sake; multi-CPU workers can batch.
    batch = min(1 if policy().cpus < 2 else 4,
                max(1, (policy().ceiling - 256*1024**2) // (256*1024**2)))
    pieces = []
    for start in range(0,len(images),batch):
        stop = min(len(images),start+batch)
        last_input = stop + int(stop < len(images) and bool(timeline.blends[stop]))
        args = [binary('ffmpeg'), '-y', '-v', 'error', '-filter_complex_threads', '1']
        filters, outputs = [], []
        for i in range(start,last_input):
            args += ['-i',str(images[i].resolve())]
            incoming = timeline.blends[i]
            outgoing = timeline.blends[i+1] if i+1 < len(images) else 0
            end = timeline.frames[i]-outgoing
            parts = (['head'] if i>start and incoming else []) + (['body'] if i<stop else []) + (['tail'] if i<stop and outgoing else [])
            zoom = zoom_filter(timeline.frames[i],width,height,fps,transitions[i].get('kind')=='zoom_out')
            labels = ''.join(f'[{part}in{i}]' for part in parts)
            filters.append(f'[{i-start}:v]{zoom}' + (f',split={len(parts)}' if len(parts)>1 else '') + labels)
            if 'head' in parts:
                filters.append(f'[headin{i}]trim=end_frame={incoming},{timing}[head{i}]')
            if 'body' in parts:
                # zoompan supplies CFR. A redundant fps filter here loses the last EOF tick.
                filters.append(f'[bodyin{i}]trim=start_frame={incoming}:end_frame={end},setpts=PTS-STARTPTS[body{i}]')
                outputs.append((f'body{i}',end-incoming,work/f'auto-body-{i:03d}.mp4'))
            if 'tail' in parts:
                filters.append(f'[tailin{i}]trim=start_frame={end}:end_frame={timeline.frames[i]},{timing}[tail{i}]')
                filters.append(f'[tail{i}][head{i+1}]xfade=transition={XFADE[transitions[i+1]["kind"]]}:duration={outgoing/fps:.9f}:offset=0,format=yuv420p[boundary{i}]')
                outputs.append((f'boundary{i}',outgoing,work/f'auto-boundary-{i:03d}.mp4'))
        args += ['-filter_complex', ';'.join(filters)]
        for label, frames, path in outputs:
            args += ['-map', f'[{label}]', '-an', '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast',
                     '-crf', '21', '-pix_fmt', 'yuv420p', '-r', str(fps), '-frames:v', str(frames), str(path.resolve())]
            pieces.append(path)
        run(args)
    return pieces


def transition_segments(clips, timeline, transitions, work):
    """Render scene bodies and short boundaries separately: at most two decoders, no long chain.

    Incoming overlaps are removed from bodies and emitted once as blended boundary clips.
    Thus the concatenation preserves the measured narration timeline exactly.
    """
    pieces = []
    fps = timeline.fps
    timing = cfr_filter(fps)
    for i, clip in enumerate(clips):
        incoming = timeline.blends[i]
        outgoing = timeline.blends[i + 1] if i + 1 < len(clips) else 0
        end = timeline.frames[i] - outgoing
        if end > incoming:
            path = work / f'body-{i:03d}.mp4'
            run([binary('ffmpeg'), '-y', '-v', 'error', '-i', str(clip.resolve()),
                 '-vf', f'trim=start_frame={incoming}:end_frame={end},{timing}',
                 '-an', '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '21',
                 '-pix_fmt', 'yuv420p', '-r', str(fps), '-frames:v', str(end - incoming), str(path.resolve())])
            pieces.append(path)
        if outgoing:
            path = work / f'boundary-{i:03d}.mp4'
            effect = XFADE[transitions[i + 1]['kind']]
            filters = (f'[0:v]trim=start_frame={end}:end_frame={timeline.frames[i]},{timing}[a];'
                       f'[1:v]trim=end_frame={outgoing},{timing}[b];'
                       f'[a][b]xfade=transition={effect}:duration={outgoing/fps:.9f}:offset=0,format=yuv420p[v]')
            run([binary('ffmpeg'), '-y', '-v', 'error', '-filter_complex_threads', '1',
                 '-i', str(clip.resolve()), '-i', str(clips[i + 1].resolve()), '-filter_complex', filters,
                 '-map', '[v]', '-an', '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast',
                 '-crf', '21', '-pix_fmt', 'yuv420p', '-r', str(fps), '-frames:v', str(outgoing), str(path.resolve())])
            pieces.append(path)
    return pieces


def assemble_bounded(clips, narration, captions, output, timeline, music, intensity, ducking, music_start):
    """Concat demuxer decodes one scene at a time instead of opening 25 decoders."""
    listing = captions.parent / "scenes.concat"
    listing.write_text("\n".join("file '" + str(p.resolve()).replace("\\", "/").replace("'", "'\\''") + "'" for p in clips), encoding="utf-8")
    duration = timeline.duration
    args = [binary("ffmpeg"), "-y", "-v", "error", "-filter_complex_threads", "1",
            "-f", "concat", "-safe", "0", "-i", str(listing.resolve()), "-i", str(narration.resolve())]
    vf = f"{cfr_filter(timeline.fps)},subtitles=filename={captions.name}:fontsdir=fonts,format=yuv420p"
    filters = [f"[0:v]{vf}[film]", f"[1:a]aresample=48000:first_pts=0,apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[voice]"]
    if music and music.exists() and intensity > 0:
        args += ["-ss", str(max(0.0, music_start)), "-stream_loop", "-1", "-i", str(music.resolve())]
        filters += [f"[2:a]aresample=48000,volume={intensity:.4f},atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[bed]"]
        if ducking:
            filters += ["[voice]asplit=2[main][side]", "[bed][side]sidechaincompress=threshold=0.025:ratio=8:attack=10:release=250[duck]", "[main][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[mix]"]
        else:
            filters += ["[voice][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[mix]"]
    else:
        filters += ["[voice]alimiter=limit=0.95:latency=1[mix]"]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".partial.mp4")
    run(args + ["-filter_complex", ";".join(filters), "-map", "[film]", "-map", "[mix]",
                "-c:v", "libx264", "-threads", "1", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                "-r", str(timeline.fps), "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-movflags", "+faststart", "-t", f"{duration:.6f}", str(partial.resolve())], cwd=captions.parent, timeout=7200)
    validate(partial, duration)
    partial.replace(output)
    return output


def validate(path: Path, expected_duration: float) -> dict:
    """Refuse to hand back a file whose picture and sound disagree."""
    info = probe(path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not video or not audio:
        raise ValueError("Final output is missing picture or sound")
    if video["width"] * 16 != video["height"] * 9:
        raise ValueError("Final output must be vertical 9:16")

    container = float(info["format"]["duration"])
    video_duration = float(video.get("duration", container))
    audio_duration = float(audio.get("duration", container))
    if abs(container - expected_duration) > 0.25:
        raise ValueError(f"Duration mismatch: expected {expected_duration:.2f}s, produced {container:.2f}s")
    if abs(video_duration - audio_duration) > 0.12:
        raise ValueError(
            f"Picture/sound drift of {abs(video_duration - audio_duration) * 1000:.0f} ms; refusing this render"
        )
    run([binary("ffmpeg"), "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], timeout=900)
    return info
