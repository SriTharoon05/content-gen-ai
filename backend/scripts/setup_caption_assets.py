"""Install the caption font and (optionally) the offline alignment model.

    python scripts/setup_caption_assets.py
    python scripts/setup_caption_assets.py --with-vosk
"""
import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/apache/luckiestguy/LuckiestGuy-Regular.ttf"
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-vosk", action="store_true", help="download the 40 MB offline aligner")
    args = parser.parse_args()

    font = ROOT / "assets" / "fonts" / "LuckiestGuy-Regular.ttf"
    if font.exists():
        print(f"font already present: {font}")
    else:
        font.parent.mkdir(parents=True, exist_ok=True)
        font.write_bytes(fetch(FONT_URL))
        print(f"font installed: {font}")

    if args.with_vosk:
        target = ROOT / "models" / "vosk-model-small-en-us-0.15"
        if target.exists():
            print(f"vosk model already present: {target}")
        else:
            print("downloading vosk model (~40 MB)...")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(fetch(VOSK_URL))) as archive:
                archive.extractall(target.parent)
            print(f"vosk model installed: {target}")
    else:
        print("Skipping optional offline Vosk assets. Production captions require Groq audio transcription.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
