"""Run directly to retain cover/inner sample JPEGs in a printed temp folder."""
from pathlib import Path
from io import BytesIO
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from PIL import Image, ImageCms, ImageDraw
from app import news_poster as poster


def manifest(index=1):
    return {'operation': 'news_slide', 'slide': {
        'headline': 'A new chapter for clean energy',
        'body': 'Researchers unveil a new approach to storing renewable power. '
                'The next challenge: bringing the technology from the lab to everyday life.',
        'highlight': 'clean energy', 'brand': 'THE DAILY BRIEF',
        'source': 'Source: Research desk', 'published_at': '27 SEP 2026',
        'index': index, 'total': 5}}


def hero(root):
    image = Image.new('RGB', (1200, 900), (54, 83, 114))
    draw = ImageDraw.Draw(image)
    draw.ellipse((680, 70, 830, 220), fill=(255, 211, 132))
    for x, y in [(200, 210), (580, 290), (950, 240)]:
        draw.line((x, y, x, 900), fill=(199, 211, 219), width=12)
        for dx, dy in [(0, -150), (-130, 95), (130, 95)]:
            draw.line((x, y, x + dx, y + dy), fill=(230, 236, 240), width=15)
    image.save(root / 'hero.png')


class NewsPosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        hero(self.root)

    def test_cover_inner_dimensions_and_srgb(self):
        outputs = []
        for index in (1, 2):
            output = self.root / f'{index}.jpg'
            result = poster.render_slide(manifest(index), self.root, output)
            self.assertEqual(result, {'width': 1080, 'height': 1350, 'bytes': output.stat().st_size})
            with Image.open(output) as image:
                self.assertEqual((image.format, image.mode, image.size), ('JPEG', 'RGB', (1080, 1350)))
                profile = ImageCms.ImageCmsProfile(BytesIO(image.info['icc_profile']))
                self.assertIn('sRGB', ImageCms.getProfileDescription(profile))
            outputs.append(output.read_bytes())
        self.assertNotEqual(*outputs)

    def test_hero_area_has_no_badge_on_cover_or_inner(self):
        Image.new('RGB', (1200, 900), (54, 83, 114)).save(self.root / 'hero.png')
        for index in (1, 2):
            with self.subTest(index=index):
                output = self.root / f'clean-{index}.jpg'
                poster.render_slide(manifest(index), self.root, output)
                with Image.open(output) as image:
                    # On a flat hero, each row is uniform across the former
                    # badge and surrounding image; allow JPEG rounding noise.
                    for y in range(135, 190):
                        row = image.crop((55, y, 400, y + 1))
                        for low, high in row.getextrema():
                            self.assertLessEqual(high - low, 3)

    def test_maximum_text_and_unicode_fallback(self):
        for index in (1, 2):
            data = manifest(index)
            data['slide'].update(headline=('Wide WWW café — 東京 🛰 ' * 5)[:100],
                                 body=('A long explanation with accents naïve résumé. ' * 8)[:260],
                                 brand='B' * 35, source='S' * 60, highlight=None)
            poster.render_slide(data, self.root, self.root / 'long.jpg')
        font = poster._font(32)
        self.assertEqual(poster._safe_text('hello\U0010ffff', font), 'hello?')

    def test_text_layout_stays_in_bounds(self):
        for text in ['W' * 100, 'Éj café 東京 ' * 9, 'word ' * 52]:
            font, lines, step = poster._fit(text, (0, 0, 952, 290), 76, 22, True)
            self.assertLessEqual(len(lines) * step, 290)
            for line, _ in lines:
                bounds = font.getbbox(line)
                self.assertLessEqual(bounds[2] - min(0, bounds[0]), 952)
                self.assertLessEqual(bounds[3] - bounds[1], step)

    def test_adaptive_layout_has_consistent_ink_gap_and_footer_clearance(self):
        for index in (1, 2):
            for headline in ['Addressing Calf Loss', 'Refiners Boost LPG Output',
                             'Advocating for Inclusive AI Governance', 'W' * 100]:
                for body in ['A short factual explanation.', ('A longer factual explanation. ' * 10)[:260]]:
                    slide = manifest(index)['slide'] | {'headline': headline, 'body': body}
                    layout = poster._content_layout(slide)
                    self.assertEqual(layout['body_top'] - layout['top'] - layout['title_height'], 36)
                    self.assertEqual(layout['body_top'] + layout['body_height'], 1130)
                    self.assertGreaterEqual(layout['top'], 630 if index == 1 else 570)

    def test_validation_and_overflow_leave_output_untouched(self):
        changes = [('headline', 'x' * 101), ('body', 'x' * 261), ('brand', 'x' * 36),
                   ('source', 'x' * 61), ('headline', ''), ('body', None),
                   ('index', 0), ('index', True), ('index', 6), ('total', 1.5),
                   ('published_at', 123), ('published_at', 'W' * 500),
                   ('highlight', 'absent'), ('highlight', ''), ('headline', 'a\x00b')]
        output = self.root / 'output.jpg'
        output.write_bytes(b'existing')
        for key, value in changes:
            with self.subTest(key=key, value=value):
                data = manifest()
                data['slide'][key] = value
                with self.assertRaises(ValueError):
                    poster.render_slide(data, self.root, output)
                self.assertEqual(output.read_bytes(), b'existing')
        for data in [None, {}, {'operation': 'other'}, {'operation': 'news_slide', 'slide': []}]:
            with self.assertRaises(ValueError):
                poster.render_slide(data, self.root, output)
        with self.assertRaisesRegex(ValueError, 'overflow'):
            poster._fit('too much text', (0, 0, 10, 10), 32, 22)

    def test_highlight_changes_pixels(self):
        data = manifest()
        poster.render_slide(data, self.root, self.root / 'yes.jpg')
        data['slide']['highlight'] = None
        poster.render_slide(data, self.root, self.root / 'no.jpg')
        self.assertNotEqual((self.root / 'yes.jpg').read_bytes(), (self.root / 'no.jpg').read_bytes())

    def test_local_font_fallback(self):
        poster._font.cache_clear()
        original = poster.ImageFont.truetype
        def only_asset(path, size):
            if Path(path).name != 'LuckiestGuy-Regular.ttf':
                raise OSError('font unavailable')
            return original(path, size)
        try:
            with patch.object(poster.ImageFont, 'truetype', side_effect=only_asset):
                self.assertIn('LuckiestGuy', poster._font(30).path)
        finally:
            poster._font.cache_clear()

    def test_missing_corrupt_and_input_overwrite(self):
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            poster.render_slide(manifest(), self.root, self.root / 'hero.png')
        (self.root / 'hero.png').unlink()
        with self.assertRaises(FileNotFoundError):
            poster.render_slide(manifest(), self.root, self.root / 'out.jpg')
        (self.root / 'hero.png').write_bytes(b'invalid')
        with self.assertRaises(OSError):
            poster.render_slide(manifest(), self.root, self.root / 'out.jpg')

    def test_duplicate_or_recompressed_slide_image_is_rejected(self):
        with Image.open(self.root / 'hero.png') as image:
            image.resize((600, 450)).save(self.root / 'reference-0.png')
        with self.assertRaisesRegex(ValueError, 'too similar'):
            poster.render_slide(manifest(), self.root, self.root / 'duplicate.jpg')
        Image.new('RGB', (600, 450), (220, 40, 20)).save(self.root / 'reference-0.png')
        poster.render_slide(manifest(), self.root, self.root / 'different.jpg')


if __name__ == '__main__':
    suite = unittest.main(exit=False)
    if not suite.result.wasSuccessful():
        sys.exit(1)
    root = Path(tempfile.mkdtemp(prefix='news-poster-samples-'))
    hero(root)
    for index, name in [(1, 'cover'), (2, 'inner')]:
        poster.render_slide(manifest(index), root, root / f'{name}.jpg')
    print(f'Visual inspection samples: {root}')
