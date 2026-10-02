"""Per-render wall time, resident memory and CPU accounting (including FFmpeg children)."""
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
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
    sampled_children = {}
    def sample_children():
        if os.name == 'posix':
            return
        for child in process.children(recursive=True):
            try:
                identity = (child.pid, child.create_time())
                times = child.cpu_times()
                sampled_children[identity] = times.user + times.system
            except psutil.Error:
                pass
    def sample():
        while not stop.wait(.02):
            result['peak_ram_bytes'] = max(result['peak_ram_bytes'], memory_used())
            sample_children()
    thread = threading.Thread(target=sample, daemon=True)
    def counted(args):
        if Path(args[0]).stem.lower() == 'ffmpeg':
            result['ffmpeg_passes'] += 1
            if '-f' not in args or not any(args[i:i+2] == ['-f', 'null'] for i in range(len(args)-1)):
                result['encode_passes'] += 1
    # Observe inside the canonical function: audio/alignment import `run` by value.
    # Replacing media.run misses those calls and also leaks between concurrent jobs.
    token = media._run_observer.set(counted)
    result['peak_ram_bytes'] = memory_used()
    thread.start()
    try:
        yield result
    finally:
        stop.set(); thread.join()
        sample_children()
        media._run_observer.reset(token)
        result['seconds'] = round(time.monotonic() - start,3)
        result['cpu_seconds'] = round(cpu() - before + sum(sampled_children.values()),3)
        selected = policy()
        result['cpu_utilization_pct'] = round(100 * result['cpu_seconds'] / max(.001,result['seconds']) / selected.cpus,2)
        result['cpu_accounting'] = 'process_and_children' if os.name == 'posix' else 'process_and_sampled_children'
        result['ram_accounting'] = 'cgroup_working_set_or_process_tree'
        result['profile'] = policy().name
        result['available_cpus'] = selected.cpus
        result['memory_limit_bytes'] = selected.memory
        result['auto_memory_ceiling_bytes'] = selected.ceiling
