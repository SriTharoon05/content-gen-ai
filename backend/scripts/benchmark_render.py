"""Offline 25-scene smoke benchmark; uses existing smoke image/speech fixtures, never a DB."""
import argparse
import json
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ['DATABASE_URL'] = 'postgresql+psycopg://unused:unused@127.0.0.1:1/unused'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from smoke_render import fake_image, fake_speech, WORDS, KINDS
from app import media
from app.settings_store import DEFAULTS, render_settings
from app.render_profile import using
from app.render_metrics import measure


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=['low_memory','auto'],required=True)
    parser.add_argument('--output-dir',required=True)
    args = parser.parse_args()
    from app.db import engine
    from sqlalchemy import event
    @event.listens_for(engine,'do_connect')
    def no_database(*a,**kw):
        raise RuntimeError('Benchmark may not access the database')
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='render-benchmark-') as folder, render_settings(DEFAULTS), using(args.profile):
        from app.audio import pace_and_trim
        from app.captions import write_ass
        root = Path(folder)
        raw, narration = root/'raw.wav',root/'narration.wav'
        fake_speech(raw,54)
        total = pace_and_trim(raw,narration,1.0,120,250)
        words = [{'word':WORDS[i%len(WORDS)],'start':i*total/200,'end':(i+1)*total/200-.02} for i in range(200)]
        captions = root/'captions.ass'
        write_ass(words,[],{'night','morning'},captions,total)
        images=[]
        for i in range(25):
            path = root/f's{i:03d}.png'
            fake_image(i,path)
            images.append(path)
        random.seed(7)
        transitions=[{'kind':'hard_cut','duration_ms':0}]
        for i in range(24):
            kind=random.choice(KINDS+['zoom_out'])
            transitions.append({'kind':kind,'duration_ms':0 if kind in media.ZERO_OVERLAP else 320})
        timeline=media.build_timeline([(i*total/25,(i+1)*total/25) for i in range(25)],transitions,30,total)
        output=output_dir/f'{args.profile}.mp4'
        print(f'Benchmark {args.profile}: 25 scenes, {timeline.duration}s, 720x1280, 30fps, mixed transitions',flush=True)
        with measure() as metrics:
            media.assemble(images,narration,timeline,transitions,captions,output,root/'clips')
        info=media.probe(output)
        video=next(s for s in info['streams'] if s['codec_type']=='video')
        metrics.update(duration=float(video['duration']),fps=video['avg_frame_rate'],bytes=output.stat().st_size,
                       frames=int(video['nb_frames']),width=video['width'],height=video['height'],scenes=25,
                       ffmpeg_version=media.run([media.binary('ffmpeg'),'-version']).splitlines()[0])
        assert metrics['frames']==timeline.total_frames
        (output_dir/f'{args.profile}.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
        print(json.dumps(metrics,indent=2),flush=True)


if __name__=='__main__':
    main()
