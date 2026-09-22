"""Round-robin key rotation with per-key cooldown, shared by every provider.

Adding a fifth Pollinations key, or switching text work onto paid keys, is a settings change rather
than a code change: the pools are rebuilt from saved settings whenever Save All runs.
"""
import itertools
import threading
import time

COOLDOWN_SECONDS = 65
RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "resource_exhausted", "quota", "exhausted", "too many")
AUTH_MARKERS = ("401", "403", "invalid api key", "unauthorized", "forbidden", "api key not valid")

POOL_SOURCES = {
    "gemini_free": ("keys", "gemini_free"),
    "gemini_paid": ("keys", "gemini_paid"),
    "pollinations": ("keys", "pollinations"),
    "groq": ("keys", "groq"),
    "groq_text": ("keys", "groq"),
}


class NoKeysConfigured(RuntimeError):
    pass


class KeyPool:
    def __init__(self, name: str, keys: list[str]):
        self.name = name
        self._keys = [k for k in keys if k]
        self._cycle = itertools.cycle(range(max(1, len(self._keys))))
        self._cooldown: dict[int, float] = {}
        self._dead: set[int] = set()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def healthy(self) -> int:
        return len([i for i in range(len(self._keys)) if i not in self._dead])

    def acquire(self, wait: bool = True) -> tuple[int, str]:
        if not self._keys:
            raise NoKeysConfigured(f"No {self.name} keys configured. Add one in Settings.")
        with self._lock:
            for _ in range(len(self._keys)):
                index = next(self._cycle)
                if index in self._dead:
                    continue
                if self._cooldown.get(index, 0) <= time.time():
                    return index, self._keys[index]
            live = {i: t for i, t in self._cooldown.items() if i not in self._dead}
            if not live:
                raise NoKeysConfigured(f"Every {self.name} key was rejected as invalid. Check Settings.")
            if not wait:
                raise NoKeysConfigured(f"All {self.name} keys are cooling down")
            index = min(live, key=live.get)
            wait = max(0.0, live[index] - time.time())
        time.sleep(min(wait, COOLDOWN_SECONDS))
        return index, self._keys[index]

    def retry_rounds(self, rounds: int = 3):
        """Ordered, bounded attempts. Never bypass a key's Retry-After deadline."""
        for round_number in range(rounds):
            if round_number:
                with self._lock:
                    if len(self._dead) == len(self._keys):
                        return
                time.sleep(COOLDOWN_SECONDS)
            for index, key in enumerate(self._keys):
                with self._lock:
                    available = index not in self._dead and self._cooldown.get(index, 0) <= time.time()
                if available:
                    yield index, key

    def penalise(self, index: int, seconds: int = COOLDOWN_SECONDS) -> None:
        with self._lock:
            self._cooldown[index] = time.time() + seconds

    def mark_dead(self, index: int) -> None:
        """An auth failure is permanent for this process; a rate limit is not."""
        with self._lock:
            self._dead.add(index)

    def status(self) -> dict:
        now = time.time()
        return {
            "name": self.name,
            "total": len(self._keys),
            "healthy": self.healthy,
            "cooling": len([i for i, t in self._cooldown.items() if t > now and i not in self._dead]),
            "rejected": len(self._dead),
        }


def is_rate_limited(error: Exception) -> bool:
    return any(marker in str(error).lower() for marker in RATE_LIMIT_MARKERS)


def is_auth_error(error: Exception) -> bool:
    return any(marker in str(error).lower() for marker in AUTH_MARKERS)


_pools: dict[str, KeyPool] = {}
_pool_lock = threading.Lock()


def reset_pools() -> None:
    with _pool_lock:
        _pools.clear()


def pool(name: str) -> KeyPool:
    """`text` resolves to the free or paid Gemini pool depending on the configured tier."""
    from .settings_store import cfg

    if name == "text":
        name = "gemini_paid" if cfg("models", "text_tier", default="free") == "paid" else "gemini_free"
    with _pool_lock:
        if name not in _pools:
            if name not in POOL_SOURCES:
                raise ValueError(f"Unknown key pool '{name}'")
            values = list(cfg(*POOL_SOURCES[name], default=[]) or [])
            if name == "gemini_paid" and cfg("keys", "gemini_audio_paid", default=""):
                values.append(cfg("keys", "gemini_audio_paid"))
            _pools[name] = KeyPool(name, list(dict.fromkeys(values)))
        return _pools[name]


def pool_status() -> list[dict]:
    from .settings_store import cfg

    names = ["gemini_free", "gemini_paid", "pollinations", "groq"]
    active = "gemini_paid" if cfg("models", "text_tier", default="free") == "paid" else "gemini_free"
    out = []
    for name in names:
        status = pool(name).status()
        status["active_for_text"] = name == active
        out.append(status)
    return out
