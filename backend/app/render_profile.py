"""Resource-aware media policy. Explicit low_memory leaves the proven commands untouched."""
import math
import os
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import psutil

_override = ContextVar('render_profile', default=None)


def read_number(path):
    try:
        value = Path(path).read_text().strip()
        return int(value) if value != 'max' else None
    except (OSError, ValueError):
        return None


def cgroup_paths(filename, controller=''):
    """Support both cgroup namespaces and runners exposing the host's nested hierarchy."""
    roots = [Path('/sys/fs/cgroup')]
    if controller:
        roots = [Path('/sys/fs/cgroup') / controller]
        if controller == 'cpu':
            roots.append(Path('/sys/fs/cgroup/cpu,cpuacct'))
    candidates=[]
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            _,controllers,relative = line.split(':',2)
            if (not controller and not controllers) or controller in controllers.split(','):
                for root in roots:
                    path = (root / relative.lstrip('/')).resolve()
                    while path.is_relative_to(root):
                        candidates.append((path / filename).as_posix())
                        if path == root:
                            break
                        path = path.parent
    except (OSError,ValueError):
        pass
    return list(dict.fromkeys([*candidates, *((root / filename).as_posix() for root in roots)]))


def resources():
    memory = psutil.virtual_memory().total
    for path in cgroup_paths('memory.max') + cgroup_paths('memory.limit_in_bytes','memory'):
        limit = read_number(path)
        if limit and limit > 0:
            memory = min(memory, limit)
    cpus = float(len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count() or 1)
    for path in cgroup_paths('cpu.max'):
        try:
            quota, period = Path(path).read_text().split()
            if quota != 'max':
                cpus = min(cpus, int(quota) / int(period))
        except (OSError, ValueError):
            pass
    for path in cgroup_paths('cpu.cfs_quota_us','cpu'):
        quota = read_number(path)
        period = read_number(Path(path).with_name('cpu.cfs_period_us').as_posix())
        if quota and quota > 0 and period:
            cpus = min(cpus, quota / period)
    return memory, max(.1, cpus)


@dataclass(frozen=True)
class Policy:
    name: str
    memory: int
    cpus: float
    ceiling: int
    threads: int
    fast: bool


def policy():
    from .config import boot
    name = _override.get() or os.getenv('RENDER_PROFILE') or boot().render_profile
    if name not in ('low_memory', 'auto'):
        raise ValueError('RENDER_PROFILE must be low_memory or auto')
    memory, cpus = resources()
    # At 2 GiB: 1.5 GiB shared Python/FFmpeg ceiling; never consume the OS reserve.
    ceiling = int(min(memory * .75, memory - 384 * 1024**2))
    return Policy(name, memory, cpus, ceiling, max(1, min(4, math.floor(cpus))),
                  name == 'auto' and memory >= 1536 * 1024**2)


@contextmanager
def using(name):
    token = _override.set(name)
    try:
        yield
    finally:
        _override.reset(token)


class MemoryPressure(RuntimeError):
    pass


def memory_used():
    for path in cgroup_paths('memory.current') + cgroup_paths('memory.usage_in_bytes','memory'):
        value = read_number(path)
        if value is not None:
            # Reclaimable file cache isn't a live frame buffer.
            stat = Path(path).with_name('memory.stat')
            try:
                values = dict(line.split() for line in stat.read_text().splitlines())
                value -= int(values.get('inactive_file', values.get('total_inactive_file', 0)))
            except (OSError, ValueError):
                pass
            return value
    process = psutil.Process()
    total = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            pass
    return total


def guarded_run(args, cwd, timeout):
    """Terminate the current fast pass BEFORE the cgroup OOM killer takes Python down."""
    import tempfile
    selected = policy()
    with tempfile.TemporaryFile(mode='w+b') as stdout, tempfile.TemporaryFile(mode='w+b') as stderr:
        with subprocess.Popen(args, cwd=str(cwd) if cwd else None, stdout=stdout, stderr=stderr,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            start = time.monotonic()
            try:
                while process.poll() is None:
                    if memory_used() > selected.ceiling:
                        raise MemoryPressure('Fast render reached its safe memory ceiling; using low_memory')
                    if time.monotonic() - start > timeout:
                        raise subprocess.TimeoutExpired(args, timeout)
                    time.sleep(.05)
            except BaseException:
                process.kill()
                process.wait()
                raise
        stdout.seek(0); stderr.seek(0)
        return subprocess.CompletedProcess(args, process.returncode, stdout.read().decode(errors='replace'),
                                           stderr.read().decode(errors='replace'))
