"""Free Gemini Flash TTS first, then Groq. Never use a paid Gemini credential."""
import hashlib
import json
import logging
import wave

from ..audio import pace_and_trim
from ..db import session_scope
from ..models import ProviderCall
from ..settings_store import cfg
from .speech import _via_generate_content, _write_wav, build_prompt

log = logging.getLogger('free_speech')


def synthesize(video_id, transcript, destination, voice, direction, language, cast, tempo, force):
    model = 'gemini-2.5-flash-preview-tts'
    prompt = build_prompt(direction, transcript)
    identity = hashlib.sha256(json.dumps([model, prompt, voice, cast, language, tempo]).encode()).hexdigest()
    cache = destination.with_suffix('.cache.json')
    if destination.exists() and cache.exists() and not force:
        saved = json.loads(cache.read_text())
        if saved.get('identity') == identity:
            with wave.open(str(destination)) as audio:
                return {**saved['result'], 'duration': audio.getnframes()/audio.getframerate(), 'cached': True}
    pcm = None
    for index, key in enumerate(cfg('keys', 'gemini_free', default=[])):
        try:
            pcm, tokens = _via_generate_content(key, model, prompt, voice, cast)
            break
        except Exception as error:
            # Do not log provider exception strings, which may contain request URLs.
            log.warning('Free Gemini TTS credential %d unavailable (%s)', index+1, type(error).__name__)
    if pcm is None:
        from .groq_speech import synthesize as groq_synthesize
        log.warning('Free Gemini TTS unavailable; falling back to Groq narration')
        return groq_synthesize(video_id, transcript, destination, language, tempo, force,
                              model_override='canopylabs/orpheus-arabic-saudi' if language.startswith('ar') else 'canopylabs/orpheus-v1-english', cast=cast)
    raw = destination.with_name('narration-gemini-free-raw.wav')
    _write_wav(raw, pcm)
    duration = pace_and_trim(raw, destination, tempo if tempo is not None else 1.0, 120, 250)
    result = {'duration':duration, 'cached':False, 'voice':voice, 'model':model, 'transport':'gemini_free',
              'multi_speaker':bool(cast and len(cast)==2), 'input_tokens':tokens, 'path':str(destination)}
    cache.write_text(json.dumps({'identity':identity, 'result':result}), encoding='utf-8')
    call_id = hashlib.sha256(f'{video_id}:free-tts:{identity}'.encode()).hexdigest()[:32]
    with session_scope() as session:
        if not session.get(ProviderCall, call_id):
            session.add(ProviderCall(id=call_id, video_id=video_id, idempotency_key=call_id,
                provider='gemini_tts', model=model, status='settled', units=duration, usd=0,
                detail_json={'tier':'free', 'input_tokens':tokens, 'voice':voice, 'cast':cast or []}))
    return result
