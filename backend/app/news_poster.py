"""Offline editorial news cards. Indices are one-based; bytes is the JPEG size.

Only root/hero.png is read. Unsupported Unicode glyphs become visible '?' marks
instead of disappearing. Oversized fields or text that cannot fit raise ValueError.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
import unicodedata

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 1080, 1350
WHITE = (248, 249, 252)
YELLOW = (255, 213, 55)
MUTED = (181, 190, 206)
ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'fonts'


@lru_cache(maxsize=128)
def _font(size, bold=False):
    names = (['DejaVuSans-Bold.ttf', 'LiberationSans-Bold.ttf', 'arialbd.ttf'] if bold else
             ['DejaVuSans.ttf', 'LiberationSans-Regular.ttf', 'arial.ttf'])
    for directory in (ASSETS, Path('/usr/share/fonts/truetype/dejavu'),
                      Path('/usr/share/fonts/truetype/liberation2'),
                      Path('/usr/share/fonts/truetype/liberation'), Path('C:/Windows/Fonts'), Path('.')):
        for name in names:
            try:
                return ImageFont.truetype(str(directory / name), size)
            except OSError:
                pass
    for path in sorted(ASSETS.glob('*.ttf')):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    raise ValueError('No local font available: install DejaVu or Liberation')


def _clean(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def _safe_text(text, font):
    missing = font.getmask('\U0010ffff')
    signature = (missing.size, bytes(missing))
    def supported(char):
        mask = font.getmask(char)
        return char.isspace() or (mask.size, bytes(mask)) != signature
    return ''.join(char if supported(char) else '?' for char in text)


def _validate(manifest):
    if not isinstance(manifest, dict) or manifest.get('operation') != 'news_slide':
        raise ValueError('operation must be news_slide')
    slide = manifest.get('slide')
    if not isinstance(slide, dict):
        raise ValueError('slide must be an object')
    result = {}
    for key, limit in [('headline', 100), ('body', 260), ('brand', 35),
                       ('source', 60), ('published_at', None)]:
        value = slide.get(key)
        if not isinstance(value, str) or not _clean(value):
            raise ValueError(f'{key} must be a non-empty string')
        if limit is not None and len(value) > limit:
            raise ValueError(f'{key} exceeds {limit} characters')
        # Dates have no contract length cap; their measured footer must still fit.
        if any(unicodedata.category(c) in ('Cc', 'Cs') and not c.isspace() for c in value):
            raise ValueError(f'{key} contains invalid control characters')
        result[key] = _clean(value)
    for key in ('index', 'total'):
        value = slide.get(key)
        if type(value) is not int or value < 1:
            raise ValueError(f'{key} must be a positive integer')
        result[key] = value
    if result['index'] > result['total']:
        raise ValueError('index exceeds total')
    highlight = slide.get('highlight')
    if highlight is not None:
        if not isinstance(highlight, str) or not _clean(highlight):
            raise ValueError('highlight must be a non-empty phrase')
        highlight = _clean(highlight)
        if highlight not in result['headline'] and highlight not in result['body']:
            raise ValueError('highlight must occur in headline or body')
    result['highlight'] = highlight
    return result


def _wrap(text, font, width):
    """Return (line, source offset), including hard wraps for long tokens."""
    lines = []
    start = 0
    while start < len(text):
        end = start
        while end < len(text):
            box = font.getbbox(text[start:end + 1])
            if box[2] - min(0, box[0]) > width:
                break
            end += 1
        if end == start:
            raise ValueError('text overflow: glyph wider than text box')
        if end < len(text) and text[end] != ' ':
            boundary = text.rfind(' ', start, end)
            if boundary > start:
                end = boundary
        lines.append((text[start:end].rstrip(), start))
        start = end
        while start < len(text) and text[start] == ' ':
            start += 1
    return lines


def _fit(text, box, maximum, minimum, bold=False):
    width, height = box[2] - box[0], box[3] - box[1]
    for size in range(maximum, minimum - 1, -1):
        font = _font(size, bold)
        safe = _safe_text(text, font)
        lines = _wrap(safe, font, width)
        step = sum(font.getmetrics()) + 6
        if len(lines) * step <= height:
            return font, lines, step
    raise ValueError('text overflow: cannot fit at minimum readable font size')


def _text(draw, text, box, maximum, minimum, bold=False, color=WHITE, highlight=None):
    font, lines, step = _fit(text, box, maximum, minimum, bold)
    phrase = _safe_text(highlight, font) if highlight else None
    safe = _safe_text(text, font)
    spans = []
    offset = 0
    while phrase and (offset := safe.find(phrase, offset)) >= 0:
        spans.append((offset, offset + len(phrase)))
        offset += len(phrase)
    for row, (line, start) in enumerate(lines):
        bounds = font.getbbox(line)
        x = box[0] - min(0, bounds[0])
        y = box[1] + row * step - bounds[1]
        draw.text((x, y), line, font=font, fill=color)
        # Redraw the complete line through a mask to preserve kerning/shaping.
        for first, last in spans:
            a, b = max(first - start, 0), min(last - start, len(line))
            if a < b:
                left = x + font.getlength(line[:a])
                right = x + font.getlength(line[:b])
                layer = Image.new('L', (WIDTH, HEIGHT))
                ImageDraw.Draw(layer).text((x, y), line, font=font, fill=255)
                crop = (int(left), box[1] + row * step,
                        int(right + 1), box[1] + (row + 1) * step)
                draw.bitmap(crop[:2], layer.crop(crop), fill=YELLOW)


def render_slide(manifest, root: Path, output: Path):
    """Render a validated news_slide to an sRGB JPEG and return dimensions/size."""
    slide = _validate(manifest)
    root, output = Path(root).resolve(), Path(output)
    hero = root / 'hero.png'
    if hero.resolve().parent != root:
        raise ValueError('hero.png must remain inside root')
    if output.resolve() == hero.resolve():
        raise ValueError('output must not overwrite hero.png')
    cover = slide['index'] == 1
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB'))
    with Image.open(hero) as source:
        source = ImageOps.exif_transpose(source)
        rgb = source.convert('RGB')
        if source.info.get('icc_profile'):
            rgb = ImageCms.profileToProfile(rgb, BytesIO(source.info['icc_profile']), srgb, outputMode='RGB')
        if 'A' in source.getbands():
            base = Image.new('RGB', source.size, (10, 16, 27))
            base.paste(rgb, mask=source.getchannel('A'))
            rgb = base
        canvas = ImageOps.fit(rgb, (WIDTH, HEIGHT if cover else 610), method=Image.Resampling.LANCZOS)
    if not cover:
        full = Image.new('RGB', (WIDTH, HEIGHT), (10, 16, 27))
        full.paste(canvas, (0, 0))
        canvas = full
    gradient = Image.new('RGBA', (1, HEIGHT))
    gradient.putdata([(7, 12, 22, int(max(180 * max(0, 1 - y / 300),
                       min(255, max(0, (y - (340 if cover else 280)) /
                                    (430 if cover else 300) * 255)))))
                      for y in range(HEIGHT)])
    canvas = Image.alpha_composite(canvas.convert('RGBA'), gradient.resize((WIDTH, HEIGHT)))
    draw = ImageDraw.Draw(canvas)
    _text(draw, slide['brand'], (64, 50, 830, 110), 32, 22, True)
    _text(draw, f"{slide['index']} / {slide['total']}", (850, 50, 1016, 110), 26, 16)
    top = 670 if cover else 590
    draw.rectangle((64, top - 28, 150, top - 21), fill=YELLOW)
    _text(draw, slide['headline'], (64, top, 1016, top + 290),
          76 if cover else 66, 36, True, highlight=slide['highlight'])
    _text(draw, slide['body'], (64, top + 310, 1016, 1220),
          32 if cover else 36, 22, highlight=slide['highlight'])
    draw.line((64, 1240, 1016, 1240), fill=MUTED, width=1)
    _text(draw, slide['source'], (64, 1260, 650, 1320), 22, 16, color=MUTED)
    _text(draw, slide['published_at'], (690, 1260, 1016, 1320), 22, 16, color=MUTED)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert('RGB').save(output, 'JPEG', quality=94, subsampling=0, icc_profile=srgb.tobytes())
    return {'width': WIDTH, 'height': HEIGHT, 'bytes': output.stat().st_size}
