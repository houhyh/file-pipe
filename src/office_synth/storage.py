from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from pydantic import BaseModel

from . import __version__
from .schemas import StageRecord, StageStatus, now


def serialize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def canonical(value: Any) -> str:
    return json.dumps(value, default=serialize, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical(value).encode()
    return hashlib.sha256(data).hexdigest()


def identifier(prefix: str, value: Any) -> str:
    return f"{prefix}_{digest(value)[:16]}"


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path: Path, value: Any) -> None:
    atomic_bytes(path, (json.dumps(value, default=serialize, ensure_ascii=False, indent=2) + "\n").encode())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Path escapes output root")
    return path


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        sensitive = {"token", "key", "api_key", "apikey", "access_token", "signature", "sig", "password", "credential", "x-amz-signature", "x-amz-credential", "x-amz-security-token"}
        query = urlencode([(k, "[REDACTED]" if k.casefold() in sensitive else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return "[invalid URL]"


def clean_error(exc: Exception) -> str:
    text = str(exc)
    text = re.sub(r"https?://[^\s\"'<>]+", lambda m: redact_url(m.group()), text)
    # SDK errors may echo request credentials; persist type for remote failures instead.
    return text[:600]


class BudgetExceeded(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.code_fingerprint = digest([digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))])
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "state.sqlite", timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS stages (key TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        import fcntl
        with (self.root / ".run.lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Run directory is already locked by another process") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def counters(self):
        return dict(self.db.execute("SELECT name,value FROM counters"))

    def consume(self, name: str, amount: int, maximum: int):
        """Record received bytes even when the last bounded in-flight chunk exceeds the cap."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
            current = (row[0] if row else 0) + amount
            self.db.execute("INSERT OR REPLACE INTO counters VALUES (?,?)", (name, current))
        if current > maximum:
            raise BudgetExceeded(f"{name} budget exhausted after in-flight chunk: {current}>{maximum}")

    def reserve(self, name: str, amount: int, maximum: int):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
            previous = row[0] if row else 0
            if previous + amount > maximum:
                raise BudgetExceeded(f"{name} budget exhausted: {previous}+{amount}>{maximum}")
            self.db.execute("INSERT OR REPLACE INTO counters VALUES (?,?)", (name, previous + amount))

    def get_stage(self, stage: str, obj: str) -> StageRecord | None:
        row = self.db.execute("SELECT data FROM stages WHERE key=?", (f"{stage}:{obj}",)).fetchone()
        return StageRecord.model_validate_json(row[0]) if row else None

    def put_stage(self, record: StageRecord):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO stages VALUES (?,?)", (record.id, record.model_dump_json()))

    def stages(self) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM stages ORDER BY key")]

    def execute(self, stage: str, obj: str, inputs: Any, function, *, resume=True, force=False, metadata=None):
        fingerprint = digest([__version__, self.code_fingerprint, stage, inputs])
        old = self.get_stage(stage, obj)
        if resume and not force and old and old.status == StageStatus.succeeded and old.input_fingerprint == fingerprint:
            if old.output_refs and all(safe_path(self.root, ref).is_file() for ref in old.output_refs):
                # Output hashes detect edits and corruption, not just missing files.
                saved = self.db.execute("SELECT value FROM meta WHERE key=?", (f"hash:{old.id}",)).fetchone()
                actual = digest([digest(safe_path(self.root, ref).read_bytes()) for ref in old.output_refs])
                if saved and saved[0] == actual:
                    return old.output_refs, True
        record = StageRecord(id=f"{stage}:{obj}", stage=stage, object_id=obj,
                             input_fingerprint=fingerprint, status=StageStatus.running,
                             attempts=(old.attempts if old else 0) + 1, started_at=now(), **(metadata or {}))
        before = self.counters()
        started = time.monotonic()
        self.put_stage(record)
        try:
            refs = function()
            record.output_refs = refs
            record.status = StageStatus.succeeded
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (
                    f"hash:{record.id}", digest([digest(safe_path(self.root, r).read_bytes()) for r in refs])))
            return refs, False
        except Exception as exc:
            record.status = StageStatus.blocked if isinstance(exc, BudgetExceeded) else StageStatus.failed
            record.error_type = type(exc).__name__
            record.error = clean_error(exc)
            raise
        finally:
            record.ended_at = now()
            after = self.counters()
            record.request_counts = {k: after.get(k, 0) - before.get(k, 0) for k in after}
            record.tokens = record.request_counts.get("tokens")
            self.put_stage(record)
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"duration:{record.id}", str(time.monotonic() - started)))
            self.db.commit()


class Budget:
    def __init__(self, store: Store, config: dict):
        self.store, self.config = store, config
        self.started = time.monotonic()

    def check_time(self):
        elapsed = int(time.monotonic() - self.started)
        if elapsed >= self.config["max_wall_seconds"]:
            raise BudgetExceeded("wall time budget exhausted for this invocation")

    def request(self, kind: str):
        self.check_time()
        self.store.reserve(kind, 1, self.config[f"max_{kind}"])

    def downloaded(self, size: int):
        self.check_time()
        self.store.consume("download_bytes", size, self.config["max_download_bytes"])
