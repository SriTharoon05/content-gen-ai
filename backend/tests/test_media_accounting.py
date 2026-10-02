"""Offline regressions for canonical caching, resource ceilings and pass accounting."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import media, render_profile
from app.render_metrics import measure


class MediaAccountingTests(unittest.TestCase):
    def test_image_cache_address_uses_content_even_same_size_and_mtime(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'scene.png'
            image.write_bytes(b'first')
            stamp=image.stat().st_mtime
            with patch.object(media,'zoom_amount',return_value=.1):
                original=media.clip_name(image,60,30,720,1280)
                image.write_bytes(b'other')
                os.utime(image,(stamp,stamp))
                self.assertNotEqual(original,media.clip_name(image,60,30,720,1280))

    def test_measure_counts_imported_run_alias_and_restores_observer(self):
        from app.audio import run as imported_run
        outcome=subprocess.CompletedProcess([],0,'','')
        with render_profile.using('low_memory'),patch.object(media.subprocess,'run',return_value=outcome):
            with measure() as metrics:
                imported_run(['ffmpeg','-i','source.wav','out.wav'])
                media.run(['ffmpeg','-i','out.wav','-f','null','-'])
                media.run(['ffprobe','-v','error','out.wav'])
            self.assertEqual(metrics['ffmpeg_passes'],2)
            self.assertEqual(metrics['encode_passes'],1)
            self.assertGreater(metrics['peak_ram_bytes'],0)
            self.assertIsNone(media._run_observer.get())
            self.assertIs(media.run,imported_run)

    def test_nested_cgroup_accounts_the_tightest_ancestor_limit(self):
        gib=1024**3
        values={'/nested/memory.current':gib//4,'/nested/memory.max':4*gib,
                '/parent/memory.current':gib,'/parent/memory.max':2*gib,
                '/host/memory.current':20*gib,'/host/memory.max':None}
        with patch.object(render_profile,'cgroup_paths',side_effect=lambda filename,controller='':
                ['/nested/'+filename,'/parent/'+filename,'/host/'+filename] if not controller else []), \
             patch.object(render_profile,'read_number',side_effect=lambda path:values.get(path)), \
             patch.object(render_profile.psutil,'virtual_memory',return_value=SimpleNamespace(total=32*gib)), \
             patch.object(Path,'read_text',return_value='inactive_file 0'):
            self.assertEqual(render_profile.memory_used(),gib)

    def test_low_memory_profile_and_one_cpu_auto_thread_cap_are_unchanged(self):
        with patch.object(render_profile,'resources',return_value=(512*1024**2,1)),render_profile.using('low_memory'):
            selected=render_profile.policy()
            self.assertFalse(selected.fast)
            self.assertEqual(selected.threads,1)
        with patch.object(render_profile,'resources',return_value=(4*1024**3,1)),render_profile.using('auto'):
            self.assertEqual(render_profile.policy().threads,1)


if __name__=='__main__':unittest.main()
