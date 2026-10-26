import json
from pathlib import Path

import httpx
import pytest

from office_synth.pipeline import models
from office_synth.providers import LLM, ClaimSelection, Narrative, ProviderError, APPROVED_PROSE
from office_synth.retrieval import DownloadError, fetch, acquire
from office_synth.schemas import WorldInstance
from office_synth.storage import BudgetExceeded, digest, read_json, write_json, redact_url


def test_llm_invalid_json_retry_cache_replay_without_key(pipeline, monkeypatch):
    p = pipeline
    p.config["llm"].update(model="test-model", max_retries=1)
    monkeypatch.setenv("OFFICE_SYNTH_LLM_KEY", "never-log-this")
    calls = []
    def response(*a, **kw):
        calls.append(1)
        content = "not valid JSON" if len(calls) == 1 else json.dumps({"workflow": [], "rules": []})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 50}})
    monkeypatch.setattr(httpx, "post", response)
    llm = LLM(p)
    assert llm.request("task", {}, ClaimSelection).workflow == []
    monkeypatch.delenv("OFFICE_SYNTH_LLM_KEY")
    assert llm.request("task", {}, ClaimSelection).rules == []
    assert len(calls) == 2
    assert p.store.counters()["llm_requests"] == 2
    assert p.store.counters()["tokens"] == 100
    with pytest.raises(ValueError):
        Narrative(paragraph="Immediately pay an invented supplier without further review.")
    assert Narrative(paragraph=APPROVED_PROSE[0]).paragraph == APPROVED_PROSE[0]


def test_llm_budget_blocks_before_network(pipeline, monkeypatch):
    pipeline.config["llm"]["model"] = "test-model"
    pipeline.config["budget"]["max_llm_tokens"] = 0
    monkeypatch.setenv("OFFICE_SYNTH_LLM_KEY", "secret")
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("Budget must stop the request"))
    with pytest.raises(BudgetExceeded):
        LLM(pipeline).request("task", {}, ClaimSelection)
    assert pipeline.store.counters().get("llm_requests", 0) == 0


def test_redirect_is_rechecked_and_ip_connection_pinned(pipeline, monkeypatch):
    urls, connections = [], []
    def resolve(url, allowed):
        urls.append(url)
        if "127.0.0.1" in url:
            raise DownloadError("Private redirect rejected")
        return "example.org", 80, "8.8.8.8"
    class Sock:
        def close(self): pass
    def connect(address, **kw):
        connections.append(address)
        return Sock()
    class Response:
        status = 302
        def getheader(self, name, default=None):
            return "http://127.0.0.1/private" if name == "Location" else default
    class Connection:
        def __init__(self, *a, **kw): pass
        def request(self, *a, **kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr("office_synth.retrieval.resolve_public", resolve)
    monkeypatch.setattr("office_synth.retrieval.socket.create_connection", connect)
    monkeypatch.setattr("office_synth.retrieval.http.client.HTTPConnection", Connection)
    with pytest.raises(DownloadError, match="Private redirect"):
        fetch("http://example.org/source", pipeline)
    assert connections == [("8.8.8.8", 80)]
    assert len(urls) == 2


def test_source_status_code_and_sensitive_url_redaction(pipeline, monkeypatch):
    def denied(*a):
        raise DownloadError("Source HTTP 403", 403)
    monkeypatch.setattr("office_synth.retrieval.fetch", denied)
    source = acquire("https://example.org/policy?document=123&token=super-secret", pipeline)
    assert source.status_code == 403
    assert "super-secret" not in source.original_url
    assert "document=123" in source.original_url


def test_corrupt_raw_evidence_source_blocks_seed_build(built):
    p = built
    source = read_json(p.root / "sources.json")[0]
    path = p.root / source["local_path"]
    path.write_text(path.read_text() + "\nUnexpected revision to source text.\n")
    with pytest.raises(ValueError, match="Source integrity"):
        p.build_seeds()


def test_fact_layer_repair_rebuilds_affected_outputs(built):
    p = built
    worlds = models(p.root / "worlds.json", WorldInstance)
    w = worlds[0]
    next(f for f in w.facts if f.fact_id == "payable_total").value = "0.00"
    write_json(p.root / "worlds.json", worlds)
    p.validate()
    assert read_json(p.workspace(w) / "private/quality.json")["status"] == "rejected"
    p.repair()
    assert read_json(p.workspace(w) / "private/quality.json")["status"] == "accepted"
    assert models(p.root / "worlds.json", WorldInstance)[0].ledger()["payable_total"] != "0.00"


def test_selection_export_preserves_other_entries(built):
    p = built
    entries = p.export()
    p.workspace_id = p.selected_worlds()[0].id
    filtered = p.export()
    assert len(entries) == len(filtered) == 2
    assert {e["workspace_id"] for e in entries} == {e["workspace_id"] for e in filtered}


def test_run_lock_prevents_concurrent_writers(pipeline):
    with pipeline.store.lock():
        with pytest.raises(RuntimeError, match="already locked"):
            with pipeline.store.lock():
                pass


def test_download_budget_records_inflight_bytes(pipeline):
    pipeline.config["budget"]["max_download_bytes"] = 10
    with pytest.raises(BudgetExceeded):
        pipeline.budget.downloaded(11)
    assert pipeline.store.counters()["download_bytes"] == 11
