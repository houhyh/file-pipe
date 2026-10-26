from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from .parsing import detect, parse, MIMES
from .providers import BraveSearch, ProviderError
from .schemas import SourceRecord
from .storage import BudgetExceeded, atomic_bytes, clean_error, digest, identifier, read_json, redact_url, write_json


class DownloadError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def resolve_public(url: str, allowed: list[str]) -> tuple[str, int, str]:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise DownloadError("Only HTTP(S) URLs without embedded credentials are allowed")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = sorted({r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    if not addresses:
        raise DownloadError("DNS returned no addresses")
    if host not in allowed and any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise DownloadError("Local, private, reserved and metadata addresses are forbidden")
    return host, port, addresses[0]


def fetch(url: str, pipeline) -> tuple[bytes, str, str, int]:
    cfg = pipeline.config["retrieval"]
    current = url
    site_times = getattr(pipeline, "site_times", {})
    pipeline.site_times = site_times
    for _ in range(6):
        host, port, address = resolve_public(current, cfg["allowed_private_hosts"])
        parts = urlsplit(current)
        wait = cfg["site_interval_seconds"] - (time.monotonic() - site_times.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        pipeline.budget.check_time()
        site_times[host] = time.monotonic()
        connection = http.client.HTTPConnection(host, port, timeout=cfg["timeout_seconds"])
        # Connect to the validated IP; avoid a second DNS lookup / DNS rebinding.
        sock = socket.create_connection((address, port), timeout=cfg["timeout_seconds"])
        try:
            if parts.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection.sock = sock
            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            host_header = f"[{host}]" if ":" in host else host
            if port not in {80, 443}:
                host_header += f":{port}"
            connection.request("GET", target, headers={"Host": host_header, "User-Agent": "office-synth/0.1 (public-document acquisition)", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise DownloadError("Redirect without Location")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                raise DownloadError(f"Source HTTP {response.status}", response.status)
            size = response.getheader("Content-Length")
            if size and int(size) > cfg["max_file_bytes"]:
                raise DownloadError("Content-Length exceeds file limit")
            chunks, count = [], 0
            while True:
                pipeline.budget.check_time()
                remaining = pipeline.config["budget"]["max_download_bytes"] - pipeline.store.counters().get("download_bytes", 0)
                if remaining <= 0:
                    if size is not None and count == int(size):
                        break
                    raise BudgetExceeded("download_bytes exhausted before reading another chunk")
                chunk = response.read(min(65536, cfg["max_file_bytes"] - count + 1, remaining + 1))
                if not chunk:
                    break
                pipeline.budget.downloaded(len(chunk))
                count += len(chunk)
                if count > cfg["max_file_bytes"]:
                    raise DownloadError("Source exceeds file size limit")
                chunks.append(chunk)
            return b"".join(chunks), current, response.getheader("Content-Type", ""), response.status
        finally:
            connection.close()
            sock.close()
    raise DownloadError("Too many redirects")


def acquire(url: str, pipeline, candidate_refs=None, title="") -> SourceRecord:
    cfg = pipeline.config["retrieval"]
    source_id = identifier("source", url)
    record = SourceRecord(id=source_id, original_url=redact_url(url), final_url=redact_url(url), institution=urlsplit(url).hostname or "unknown",
                          title=title, source_type="public_url", input_refs=candidate_refs or [])
    cached = pipeline.root / "cache" / "urls" / (digest(url) + ".json")
    if cached.exists() and not pipeline.force:
        previous = SourceRecord.model_validate(read_json(cached))
        raw = pipeline.root / previous.local_path
        parsed = pipeline.root / previous.parsed_path
        if previous.parse_status == "succeeded" and raw.is_file() and digest(raw.read_bytes()) == previous.raw_hash and parsed.is_file():
            fmt = previous.parsed_format or detect(raw.read_bytes(), previous.declared_mime, urlsplit(url).path)
            actual = parse(raw, fmt)
            write_json(parsed, actual)
            previous.parsed_format, previous.detected_mime = fmt, MIMES[fmt]
            previous.input_refs = sorted(set(previous.input_refs + (candidate_refs or [])))
            return previous
    try:
        for attempt in range(cfg["retries"] + 1):
            try:
                data, final, mime, code = fetch(url, pipeline)
                break
            except (OSError, TimeoutError, http.client.HTTPException):
                if attempt == cfg["retries"]:
                    raise DownloadError("Source transport failure (details redacted)") from None
                time.sleep(min(2**attempt, 4))
            except DownloadError as exc:
                if str(exc) in {"Source HTTP 429", "Source HTTP 500", "Source HTTP 502", "Source HTTP 503", "Source HTTP 504"} and attempt < cfg["retries"]:
                    time.sleep(min(2**attempt, 4))
                else:
                    raise
        record.final_url, record.declared_mime, record.status_code = redact_url(final), mime, code
        record.institution = urlsplit(final).hostname or record.institution
        record.byte_count, record.raw_hash, record.download_status = len(data), digest(data), "succeeded"
        # Preserve original bytes even if type identification or parsing fails.
        path = pipeline.root / "cache" / "blobs" / record.raw_hash
        atomic_bytes(path, data)
        record.local_path = str(path.relative_to(pipeline.root))
        fmt = detect(data, mime, urlsplit(final).path)
        record.detected_mime, record.parsed_format = MIMES[fmt], fmt
        expected_office = urlsplit(url).path.lower().endswith((".docx", ".xlsx", ".pptx", ".pdf")) or any(t in mime for t in ("officedocument", "ms-excel", "msword", "application/pdf"))
        if fmt == "html" and (expected_office or any(tag in data[:100000].lower() for tag in [b'type="password"', b"type='password'"])):
            raise DownloadError("HTML/login page returned instead of an accessible source")
        result = parse(path, fmt)
        record.title = record.title or result["units"][0]["text"][:200]
        record.content_hash = result["content_hash"]
        parsed_path = pipeline.root / "sources" / (record.raw_hash + ".json")
        write_json(parsed_path, result)
        record.parsed_path, record.parse_status = str(parsed_path.relative_to(pipeline.root)), "succeeded"
    except BudgetExceeded:
        raise
    except Exception as exc:
        record.error = clean_error(exc)
        if isinstance(exc, DownloadError) and exc.status_code:
            record.status_code = exc.status_code
        record.parse_status = "failed"
        if record.download_status != "succeeded":
            record.download_status = "failed"
    write_json(cached, record)
    return record


def query_plan(candidate, round_number=0):
    base = f"{candidate.role_id} purchase order goods receipt invoice three way match"
    suffixes = ["workflow procedure", "payment policy contract terms", "template filetype:xlsx"]
    if round_number:
        suffixes = ["received quantity payable invoice payment policy", "supplier settlement workflow procedure", "payment terms amendment effective date"]
    return [base + " " + suffix for suffix in suffixes]


def collect(pipeline, candidates):
    cfg = pipeline.config["retrieval"]
    urls = pipeline.urls if pipeline.urls is not None else cfg["urls"]
    records = []
    if urls:
        for url in urls[:cfg["max_sources_per_seed"] * len(candidates)]:
            record = acquire(url, pipeline)
            records.append(record)
            write_json(pipeline.root / "sources.json", records)
        return records
    if cfg["provider"] != "brave":
        raise ProviderError(f"Unsupported search provider: {cfg['provider']}")
    provider = BraveSearch(pipeline)
    known = {}
    all_hits = []
    from .seeds import build_seed, extract_evidence
    for candidate in candidates:
        candidate_records = []
        for round_number in range(cfg["max_gap_search_rounds"] + 1):
            hits = []
            for query in query_plan(candidate, round_number)[:cfg["max_queries_per_candidate"]]:
                for hit in provider.search(query, {}, cfg["max_results_per_query"]):
                    all_hits.append({**hit.model_dump(), "candidate_id": candidate.id, "round": round_number})
                    hits.append(hit)
            write_json(pipeline.root / "search_hits.json", all_hits)
            # Prefer native documents and unique URLs. Search snippets are never used as evidence.
            hits.sort(key=lambda h: (not urlsplit(h.url).path.lower().endswith((".pdf", ".docx", ".xlsx", ".pptx")), h.url))
            for hit in hits:
                if len(candidate_records) >= cfg["max_sources_per_seed"]:
                    break
                if hit.url in known:
                    record = known[hit.url]
                    record.input_refs = sorted(set(record.input_refs + [candidate.id]))
                else:
                    record = acquire(hit.url, pipeline, [candidate.id], hit.title)
                    known[hit.url] = record
                    records.append(record)
                if record.id not in {s.id for s in candidate_records}:
                    candidate_records.append(record)
                write_json(pipeline.root / "sources.json", records)
            evidence = [e for s in candidate_records if s.parse_status == "succeeded" for e in extract_evidence(s, read_json(pipeline.root / s.parsed_path))]
            draft = build_seed(candidate, candidate_records, evidence, pipeline.config)
            if draft.review_status == "accepted" or len(candidate_records) >= cfg["max_sources_per_seed"]:
                break
    return records
