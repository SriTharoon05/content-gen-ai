"""Per-render wall time, resident memory and CPU accounting (including FFmpeg children)."""
import os
import threading
import time
from contextlib import contextmanager
import psutil
from .render_profile import memory_used, policy


@contextmanager
def measure():
    from . import media
    result = {'ffmpeg_passes':0, 'encode_passes':0, 'peak_ram_bytes':0}
    start = time.monotonic()
    process = psutil.Process()
    def cpu():
        own = process.cpu_times()
        if os.name == 'posix':
            import resource
            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            return own.user + own.system + children.ru_utime + children.ru_stime
        return own.user + own.system
    before = cpu()
    stop = threading.Event()
    def sample():
        while not stop.wait(.02):
            result['peak_ram_bytes'] = max(result['peak_ram_bytes'], memory_used())
    thread = threading.Thread(target=sample, daemon=True)
    original = media.run
    def counted(args, *a, **kw):
        from pathlib import Path
        if Path(args[0]).stem.lower() == 'ffmpeg':
            result['ffmpeg_passes'] += 1
            if args[-1] != '-':
                result['encode_passes'] += 1
        return original(args,*a,**kw)
    # Used by the single-job ephemeral worker / benchmark, never the concurrent API.
    media.run = counted
    thread.start()
    try:
        yield result
    finally:
        stop.set(); thread.join()
        media.run = original
        result['seconds'] = round(time.monotonic() - start,3)
        result['cpu_seconds'] = round(cpu() - before,3)
        result['cpu_utilization_pct'] = round(100 * result['cpu_seconds'] / result['seconds'] / policy().cpus,2)
        result['cpu_accounting'] = 'process_and_children' if os.name == 'posix' else 'python_only'
        result['profile'] = policy().name
        result['available_cpus'] = policy().cpus
        result['memory_limit_bytes'] = policy().memory
        result['auto_memory_ceiling_bytes'] = policy().ceiling
