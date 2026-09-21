"""One deterministic presentation layer for dashboard previews and actual uploads."""
import re
import unicodedata

HASHTAG = re.compile(r'(?<!\w)#([^\s#.,!?;:()\[\]{}]+)')

GENERIC_TAGS = {'viral', 'fyp', 'foryou', 'foryoupage', 'trending', 'explorepage', 'followforfollow', 'likeforlike'}


def clean_hashtags(values):
    tags, seen = [], set()
    for value in values or []:
        tag = ''.join(c for c in unicodedata.normalize('NFC', str(value).lstrip('#'))
                      if c == '_' or unicodedata.category(c)[0] in 'LNM')[:40]
        key = tag.casefold()
        if not tag or key in seen or key in GENERIC_TAGS or tag.isnumeric():
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) == 5:
            break
    return tags


def with_hashtags(body, tags, limit, max_tags=5):
    # Consolidate existing hashtags into a single small footer, including legacy copy.
    inline = HASHTAG.findall(body or '')
    body = HASHTAG.sub('', body or '').strip()
    footer = ' '.join('#' + t for t in clean_hashtags([*tags, *inline])[:max_tags])
    allowance = limit - len(footer) - (2 if footer else 0)
    body = body[:allowance].rstrip()
    return '\n\n'.join(filter(None, [body, footer]))


def publication_copy(video):
    title = (video.title or '').strip()
    model = (video.options_json or {}).get('image_model', '').split('/')[-1]
    # Strip only a known internal model suffix; preserve legitimate bracketed titles.
    for label in {model, 'flux.1-schnell', 'dreamshaper-8-lcm', 'z-image-turbo'} - {''}:
        if title.endswith(f' [{label}]'):
            title = title[:-(len(label) + 3)].rstrip()
    tags = clean_hashtags(video.hashtags)
    return {
        'youtube_title': title[:100],
        'youtube_description': with_hashtags(video.description, tags[:3], 5000, max_tags=3),
        'youtube_tags': tags,
        'instagram_caption': with_hashtags(video.instagram_caption, tags, 2200),
    }
