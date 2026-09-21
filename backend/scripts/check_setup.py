"""Pre-flight: binaries, keys, font, aligner, database. Run before the first real batch."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ROOT  # noqa: E402
from app.db import init_db  # noqa: E402
from app.seed import seed_all  # noqa: E402

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def main() -> int:
    init_db()
    seed_all()
    from app.media import binary
    from app.providers.images import images_affordable, remaining_credits
    from app.settings_store import cfg

    problems = 0
    for name in ("ffmpeg", "ffprobe"):
        path = binary(name)
        found = shutil.which(path) or (Path(path).exists() and path)
        print(f"[{OK if found else BAD}] {name}: {found or 'not found on PATH'}")
        problems += 0 if found else 1

    text_tier = cfg("models", "text_tier", default="free")
    active_text = len(cfg("keys", "gemini_paid" if text_tier == "paid" else "gemini_free", default=[]))
    print(f"[{OK if active_text else BAD}] gemini {text_tier} text keys: {active_text}")
    problems += 0 if active_text else 1

    pollinations = len(cfg("keys", "pollinations", default=[]))
    print(f"[{OK if pollinations else BAD}] pollinations keys: {pollinations}")
    problems += 0 if pollinations else 1

    paid = bool(cfg("keys", "gemini_audio_paid", default=""))
    print(f"[{OK if paid else BAD}] gemini paid audio key: {'set' if paid else 'MISSING'}")
    problems += 0 if paid else 1

    groq = bool(cfg("keys", "groq", default=[]))
    print(f"[{OK if groq else BAD}] Groq measured caption alignment: {'configured' if groq else 'MISSING'}")
    problems += 0 if groq else 1

    font = ROOT / "assets/fonts/LuckiestGuy-Regular.ttf"
    print(f"[{OK if font.exists() else BAD}] caption font: {'bundled' if font.exists() else 'run setup_caption_assets.py'}")
    problems += 0 if font.exists() else 1

    vosk = ROOT / "models/vosk-model-small-en-us-0.15"
    print(f"[{OK if vosk.exists() else WARN}] word aligner: "
          f"{'installed' if vosk.exists() else 'absent - captions fall back to estimated timing'}")

    print(f"[{OK}] credits remaining: {remaining_credits():.4f} (~{images_affordable()} images)")
    print("\nREADY" if not problems else f"\n{problems} problem(s) to fix. Most are fixable in the dashboard Settings tab.")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
