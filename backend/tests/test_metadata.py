from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest

from backend.metadata import BoundedResolver, SnapshotCache


def test_cache_updates_once_and_does_not_share_mutable_responses():
    time = [0]
    cache = SnapshotCache(ttl=3, clock=lambda: time[0])
    calls = []

    def load():
        calls.append("load")
        return {"replicas": [2]}

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: cache.get("rangos", load), range(8)))
    assert calls == ["load"]
    values[0]["replicas"].append(99)
    assert cache.get("rangos", load)["replicas"] == [2]
    time[0] = 3.01
    cache.get("rangos", load)
    assert calls == ["load", "load"]


def test_slow_dns_is_bounded_and_pending_resolution_is_not_duplicated(monkeypatch):
    resolver = BoundedResolver(timeout=0.03)
    release = Event()
    calls = []

    def slow_lookup(host):
        calls.append(host)
        release.wait(3)
        return "127.0.0.1"

    monkeypatch.setattr(resolver, "lookup", slow_lookup)
    try:
        start = monotonic()
        with pytest.raises(OSError, match="DNS"):
            resolver.resolve("central-detenido")
        with pytest.raises(OSError, match="DNS"):
            resolver.resolve("central-detenido")
        assert monotonic() - start < 0.5
        assert calls == ["central-detenido"]
    finally:
        release.set()
        resolver.close()
