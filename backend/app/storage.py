"""Supabase Storage client — the only file in the codebase that knows about Supabase.

Two buckets:
  - videos   → finished .mp4 outputs
  - music    → uploaded music tracks

Public URLs are stored in Video.output_path and MusicTrack.path.
A local temp copy is still written first (FFmpeg needs a real path); this module
uploads it then deletes the local file so disk stays clean.
"""
import logging
import mimetypes
import time
from pathlib import Path

import httpx

from .config import boot

log = logging.getLogger("storage")

VIDEOS_BUCKET = "videos"
MUSIC_BUCKET = "music"
ASSETS_BUCKET = "assets"


def _headers() -> dict:
    cfg = boot()
    return {
        "apikey": cfg.supabase_key,
        "Authorization": f"Bearer {cfg.supabase_key}",
    }


def _base() -> str:
    return boot().supabase_url.rstrip("/")


# --------------------------------------------------------------------------- core ops


def upload(bucket: str, key: str, local_path: Path, *, delete_local: bool = True) -> str:
    """Upload local_path to bucket/key. Returns the public URL. Optionally deletes the local file."""
    url = f"{_base()}/storage/v1/object/{bucket}/{key}"
    mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    for attempt in range(3):
        with local_path.open("rb") as source:
            resp = httpx.put(url, content=source,
                headers={**_headers(), "Content-Type": mime, "x-upsert": "true", "Content-Length": str(local_path.stat().st_size)}, timeout=180)
        if resp.status_code < 500 and resp.status_code != 429:
            break
        time.sleep(attempt + 1)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Supabase upload failed [{resp.status_code}]: {resp.text[:400]}")

    log.info("uploaded %s → %s/%s (%d bytes)", local_path.name, bucket, key, local_path.stat().st_size)

    if delete_local:
        local_path.unlink(missing_ok=True)
        log.debug("deleted local temp file %s", local_path)

    return public_url(bucket, key)


def delete(bucket: str, key: str) -> None:
    """Delete a single object. Silently ignores 404."""
    url = f"{_base()}/storage/v1/object/{bucket}/{key}"
    resp = httpx.delete(url, headers=_headers(), timeout=30)
    if resp.status_code not in (200, 204, 404):
        log.warning("Supabase delete failed [%d]: %s", resp.status_code, resp.text[:200])


def public_url(bucket: str, key: str) -> str:
    """Build the public URL for a bucket/key (bucket must have public policy enabled)."""
    return f"{_base()}/storage/v1/object/public/{bucket}/{key}"


def signed_url(bucket: str, key: str, expires_in: int = 3600) -> str:
    """Return a signed (time-limited) URL. Use for private buckets."""
    url = f"{_base()}/storage/v1/object/sign/{bucket}/{key}"
    resp = httpx.post(
        url,
        json={"expiresIn": expires_in},
        headers={**_headers(), "Content-Type": "application/json"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Supabase sign failed [{resp.status_code}]: {resp.text[:200]}")
    return _base() + resp.json()["signedURL"]


# --------------------------------------------------------------------------- helpers


def is_supabase_url(path_or_url: str) -> bool:
    """True when the stored value is already a Supabase URL (not a local path)."""
    base = boot().supabase_url.rstrip("/")
    return bool(base) and bool(path_or_url) and path_or_url.startswith(base + "/")


def enabled() -> bool:
    """True when Supabase credentials are configured."""
    cfg = boot()
    return bool(cfg.supabase_url and cfg.supabase_key)


def key_from_url(url: str, bucket: str) -> str:
    """Extract the storage key from a public URL."""
    marker = f"/object/public/{bucket}/"
    idx = url.find(marker)
    if idx == -1:
        raise ValueError(f"URL does not contain expected marker for bucket '{bucket}': {url}")
    return url[idx + len(marker):]


def ensure_buckets():
    if not enabled():
        return
    for bucket in (VIDEOS_BUCKET, MUSIC_BUCKET, ASSETS_BUCKET):
        response = httpx.get(f"{_base()}/storage/v1/bucket/{bucket}", headers=_headers(), timeout=30)
        if response.status_code == 200:
            continue
        if response.status_code not in (400, 404):
            response.raise_for_status()
        response = httpx.post(f"{_base()}/storage/v1/bucket", headers=_headers(), json={"id": bucket, "name": bucket, "public": True}, timeout=30)
        if response.status_code not in (200, 201, 409):
            raise RuntimeError(f"Cannot create Supabase bucket {bucket}: {response.status_code}")


def checkpoint(video_id: str, cleanup: bool = False):
    """Persist source media and rebuild metadata; FFmpeg clips are disposable cache."""
    if not enabled() or not video_id:
        return
    import hashlib
    from .db import session_scope
    from .models import Asset, RegistryAsset, Video
    root = (boot().work_root / video_id).resolve()
    if not root.exists():
        return
    with session_scope() as session:
        video = session.get(Video, video_id)
        manifest = dict((video.options_json or {}).get("media_manifest", {}))
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix == ".download" or "clips" in path.relative_to(root).parts or "fonts" in path.relative_to(root).parts or path.name.startswith("align-"):
            continue
        rel = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if manifest.get(rel, {}).get("sha256") != digest:
            url = upload(ASSETS_BUCKET, f"{video_id}/{rel}", path, delete_local=False)
            manifest[rel] = {"url": url, "sha256": digest}
    with session_scope() as session:
        video = session.get(Video, video_id)
        video.options_json = {**(video.options_json or {}), "media_manifest": manifest}
        if "narration.wav" in manifest:
            video.narration_path = manifest["narration.wav"]["url"]
        for asset in session.query(Asset).filter_by(video_id=video_id, kind="image"):
            entry = manifest.get(f"images/{asset.shot_id}.png")
            if entry:
                asset.path = entry["url"]
                registry = session.get(RegistryAsset, asset.registry_id)
                if registry:
                    registry.storage_path = entry["url"]
    if cleanup:
        import shutil
        base = boot().work_root.resolve()
        if root.parent != base or root.name != video_id:
            raise ValueError("Unsafe workspace cleanup path")
        shutil.rmtree(root)


def restore(video_id: str):
    if not video_id:
        return
    from .db import session_scope
    from .models import Video
    with session_scope() as session:
        video = session.get(Video, video_id)
        manifest = dict((video.options_json or {}).get("media_manifest", {})) if video else {}
    root = (boot().work_root / video_id).resolve()
    for rel, entry in manifest.items():
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not is_supabase_url(entry["url"]):
            raise ValueError("Invalid stored media manifest")
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", entry["url"], timeout=180) as response:
            response.raise_for_status()
            with target.open("wb") as out:
                for chunk in response.iter_bytes():
                    out.write(chunk)
