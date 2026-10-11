"""Caché de metadatos y HTTP con espera DNS limitada para la demo local."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from http.client import HTTPConnection
import json
import socket
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit


class SnapshotCache:
    def __init__(self, ttl=3.0, clock=monotonic):
        self.ttl = ttl
        self.clock = clock
        self.entries = {}
        self.locks = {}
        self.guard = Lock()

    def get(self, key, load):
        with self.guard:
            lock = self.locks.setdefault(key, Lock())
        # Una sola actualización por clave aunque lleguen varias peticiones.
        with lock:
            entry = self.entries.get(key)
            if entry is None or self.clock() >= entry[0]:
                value = load()
                entry = (self.clock() + self.ttl, deepcopy(value))
                self.entries[key] = entry
            return deepcopy(entry[1])


class BoundedResolver:
    def __init__(self, ttl=3.0, timeout=0.15):
        self.ttl = ttl
        self.timeout = timeout
        self.lock = Lock()
        self.executor = None
        self.futures = {}

    @staticmethod
    def lookup(host):
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        # localhost en Windows puede resolver primero ::1 aunque Docker publique IPv4.
        addresses.sort(key=lambda entry: entry[0] != socket.AF_INET)
        return addresses[0][4][0]

    def resolve(self, host):
        with self.lock:
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="uptc-dns")
            entry = self.futures.get(host)
            if entry is not None and entry[1].done() and entry[0] is None:
                entry[0] = monotonic() + self.ttl
            if entry is None or (entry[0] is not None and monotonic() >= entry[0]):
                # La vigencia empieza al completar DNS, no al iniciarlo.
                entry = [None, self.executor.submit(self.lookup, host)]
                self.futures[host] = entry
            future = entry[1]
        try:
            return future.result(timeout=self.timeout)
        except TimeoutError as exc:
            # No se cancela ni duplica un getaddrinfo que sigue trabajando.
            raise OSError("La resolución DNS superó el tiempo límite.") from exc

    def close(self):
        with self.lock:
            if self.executor:
                self.executor.shutdown(wait=False, cancel_futures=True)
            self.executor = None
            self.futures.clear()


class ClusterHTTP:
    def __init__(self, resolver, timeout=0.25):
        self.resolver = resolver
        self.timeout = timeout

    def get(self, url, json_body=False):
        target = urlsplit(url)
        if target.scheme != "http" or not target.hostname:
            raise ValueError("Los endpoints del clúster local deben usar HTTP.")
        address = self.resolver.resolve(target.hostname)
        # Conectar a la IP evita otra resolución DNS dentro de HTTPConnection.
        connection = HTTPConnection(address, target.port or 80, timeout=self.timeout)
        try:
            path = (target.path or "/") + ("?" + target.query if target.query else "")
            connection.request("GET", path, headers={"Host": target.netloc})
            response = connection.getresponse()
            if json_body:
                if response.status != 200:
                    raise OSError("No se pudieron consultar los metadatos HTTP.")
                return json.loads(response.read())
            return response.status
        finally:
            connection.close()
