"""Configuración local y Docker; no crea ni modifica la infraestructura."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

SEDES = ("tunja", "duitama", "sogamoso", "aguazul")


@dataclass(frozen=True)
class Node:
    name: str
    label: str
    sede: str
    role: str
    host: str
    port: int


@dataclass(frozen=True)
class Settings:
    database: str
    user: str
    password: str
    sslmode: str
    connect_timeout: int
    statement_timeout_ms: int
    transaction_retries: int
    nodes: tuple[Node, ...]
    cors_origins: tuple[str, ...]
    database_url: str = ""
    http_urls: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_env(cls):
        names = (*SEDES, "central")
        hosts = os.getenv("DB_HOSTS", ",".join(["localhost"] * 5)).split(",")
        ports = os.getenv("DB_PORTS", "26257,26258,26259,26260,26261").split(",")
        if len(hosts) == 1:
            hosts *= 5
        if len(ports) == 1:
            ports *= 5
        if len(hosts) != 5 or len(ports) != 5:
            raise ValueError("DB_HOSTS y DB_PORTS deben describir cinco nodos, o un valor compartido.")
        nodes = tuple(
            Node(
                name=f"{name}-academico" if name != "central" else "tunja-central",
                label=name.capitalize() if name != "central" else "Central · Tunja",
                sede=name if name != "central" else "tunja",
                role="academico" if name != "central" else "central",
                host=host.strip(),
                port=int(port),
            )
            for name, host, port in zip(names, hosts, ports)
        )
        if os.getenv("CLUSTER_NODES_JSON"):
            nodes = tuple(Node(**item) for item in json.loads(os.environ["CLUSTER_NODES_JSON"]))
        default_http = ",".join(f"{node.name}=http://{node.host}:{8080 + index}" for index, node in enumerate(nodes))
        http_urls = tuple(tuple(item.strip().split("=", 1)) for item in os.getenv("NODOS_HTTP", default_http).split(",") if item.strip())
        timeout = os.getenv("TIMEOUT_SQL", "")
        timeout_ms = int(float(timeout[:-2])) if timeout.endswith("ms") else int(float(timeout[:-1]) * 1000) if timeout.endswith("s") else int(timeout) if timeout else int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))
        return cls(
            database=os.getenv("DB_NAME", "uptc"),
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            sslmode=os.getenv("DB_SSLMODE", "disable"),
            connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "2")),
            statement_timeout_ms=timeout_ms,
            transaction_retries=int(os.getenv("DB_TRANSACTION_RETRIES", "3")),
            nodes=nodes,
            cors_origins=tuple(filter(None, os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(","))),
            database_url=os.getenv("DATABASE_URL", ""),
            http_urls=http_urls,
        )
