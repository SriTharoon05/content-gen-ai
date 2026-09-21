"""Read-only acceptance checks on an existing video, using its Supabase artifacts."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import httpx
import numpy as np
from app.db import session_scope
from app.models import Video, Asset, ProviderCall
from app.media import run, binary, probe
from app.audio import _read_wav
from sqlalchemy import select

def verify(video_id):
    with session_scope() as s:
        video=s.get(Video,video_id)
        manifest=(video.options_json or {}).get('media_manifest',{})
        assets=s.scalars(select(Asset).where(Asset.video_id==video_id,Asset.kind=='image')).all()
        text_calls=s.scalars(select(ProviderCall).where(ProviderCall.video_id==video_id,ProviderCall.provider=='gemini_text')).all()
    assert video.state=='READY',video.error
    assert 20<=video.image_count<=25
    assert 45<=video.duration_seconds<=55
    assert len(assets)>=20 and all(a.path.startswith('https://') for a in assets)
    assert all(c.model=='gemini-3.1-flash-lite' for c in text_calls)
    assert 'narration.wav' in manifest and 'captions.ass' in manifest
    with tempfile.TemporaryDirectory(prefix='story-verify-') as folder:
        root=Path(folder)
        for name,url in [('video.mp4',video.output_path),('reference.wav',manifest['narration.wav']['url'])]:
            with httpx.stream('GET',url,timeout=180) as r:
                r.raise_for_status()
                with (root/name).open('wb') as out:
                    for chunk in r.iter_bytes():out.write(chunk)
        info=probe(root/'video.mp4')
        stream=next(s for s in info['streams'] if s['codec_type']=='video')
        assert stream['r_frame_rate']=='30/1'
        assert int(stream['nb_frames'])==round(float(stream['duration'])*30)
        for source,target in [('video.mp4','rendered.wav'),('reference.wav','source.wav')]:
            run([binary('ffmpeg'),'-y','-v','error','-i',str(root/source),'-ac','1','-ar','8000','-c:a','pcm_s16le',str(root/target)])
        a,_=_read_wav(root/'rendered.wav');b,_=_read_wav(root/'source.wav')
        n=1<<(len(a)+len(b)-1).bit_length()
        corr=np.fft.irfft(np.fft.rfft(a,n)*np.conj(np.fft.rfft(b,n)),n)
        lag=int(np.argmax(corr));lag=lag-n if lag>n//2 else lag
        lag_ms=lag/8
        assert abs(lag_ms)<=1000/30,f'Audio offset {lag_ms}ms exceeds one frame'
        timeline=httpx.get(manifest['timeline.json']['url'],timeout=30).json()
        assert timeline['caption_source'].startswith('groq:')
        result={'video_id':video_id,'duration_seconds':video.duration_seconds,'images':video.image_count,
            'fps':30,'audio_mux_offset_ms':lag_ms,'caption_source':timeline['caption_source'],
            'stored_artifacts':len(manifest),'text_models':sorted({c.model for c in text_calls}),
            'text_tiers':sorted({c.detail_json.get('tier') for c in text_calls})}
        print(json.dumps(result,indent=2))
        return result

if __name__=='__main__':verify(sys.argv[1])
