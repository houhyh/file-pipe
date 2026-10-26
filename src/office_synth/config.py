from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from .storage import atomic_bytes, digest


DEFAULT = {
    "schema_version": "1.0",
    "run": {"random_seed": 42, "output_dir": "../../runs/demo", "mode": "synthetic_world", "fixture": True},
    "sampling": {"candidate_limit": 12, "target_seeds": 1, "instances_per_seed": 2},
    "retrieval": {"provider": "brave", "max_queries_per_candidate": 3, "max_results_per_query": 3,
                  "max_sources_per_seed": 6, "max_gap_search_rounds": 1,
                  "timeout_seconds": 20, "max_file_bytes": 10485760, "retries": 2,
                  "site_interval_seconds": 0.2, "allowed_private_hosts": [], "urls": []},
    "seeds": {"min_sources": 2},
    "llm": {"enabled": False, "base_url": "https://api.openai.com/v1", "model": "",
            "api_key_env": "OFFICE_SYNTH_LLM_KEY", "max_output_tokens": 3000, "max_retries": 1},
    "generation": {"files_per_workspace": [5, 8], "required_formats": ["docx", "xlsx", "pptx", "pdf"],
                   "max_parallel_llm_calls": 2, "max_repair_attempts": 2, "enable_event": True,
                   "optional_formats": []},
    "validation": {"render": False, "recalculate": False,
                   "required_checks": ["structure", "facts", "calculations", "references", "dependencies", "versions", "world"]},
    "budget": {"max_llm_requests": 30, "max_llm_tokens": None, "max_search_requests": 30,
               "max_download_bytes": 104857600, "max_wall_seconds": 3600},
}


def taxonomy() -> dict:
    def entry(key, name, definition):
        return {"id": key, "name": name, "definition": definition, "synonyms": [], "sources": []}
    return {
        "schema_version": "1.0",
        "dimensions": {
            "domain": [entry("commerce", "Commerce", "Fictional goods and services business")],
            "role": [entry("procurement", "Procurement", "Purchase and supplier settlement"),
                     entry("finance", "Finance", "Payables reconciliation and payment approval"),
                     entry("operations", "Operations", "Receipt and supplier exception coordination")],
            "scenario": [entry("settlement", "Supplier settlement", "Order, receipt and invoice reconciliation")],
            "work_need": [entry("reconcile", "Reconcile payables", "Match source records and compute payable amount")],
            "business_object": [entry("purchase", "Purchase", "Goods received against a purchase order")],
            "execution_pattern": [entry("three_way_match", "Three way match", "Compare order, receipt and invoice")],
        },
        "compatibility": [{"effect": "allow", "when": {"scenario": ["settlement"]},
                           "require": {"work_need": ["reconcile"], "execution_pattern": ["three_way_match"]}}],
    }


def merge(base: dict, update: dict) -> dict:
    result = deepcopy(base)
    for key, value in update.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def load(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a YAML mapping")
    config = merge(DEFAULT, raw)
    if config["schema_version"] != "1.0" or config["run"]["mode"] != "synthetic_world":
        raise ValueError("Only schema 1.0 and synthetic_world mode are supported")
    config["_base"] = str(path.resolve().parent)
    config["_root"] = str((path.parent / config["run"]["output_dir"]).resolve())
    tax_path = path.parent / raw.get("taxonomy", "taxonomy.yaml")
    if "taxonomy" in raw and not tax_path.is_file():
        raise FileNotFoundError(f"Taxonomy file not found: {tax_path}")
    config["_taxonomy"] = yaml.safe_load(tax_path.read_text()) if tax_path.exists() else taxonomy()
    for group, names in {
        "sampling": ["candidate_limit", "target_seeds", "instances_per_seed"],
        "retrieval": ["max_queries_per_candidate", "max_results_per_query", "max_sources_per_seed", "max_file_bytes"],
        "generation": ["max_parallel_llm_calls"],
        "budget": ["max_llm_requests", "max_search_requests", "max_download_bytes", "max_wall_seconds"],
    }.items():
        for key in names:
            value = config[group][key]
            if not isinstance(value, int) or value < (1 if group != "budget" else 0):
                raise ValueError(f"{group}.{key} must be a positive integer (budgets may be zero)")
    if config["seeds"]["min_sources"] < 2:
        raise ValueError("This release requires at least two independent source materials")
    for group, keys in {"retrieval": ["retries", "max_gap_search_rounds"], "generation": ["max_repair_attempts"], "llm": ["max_retries"]}.items():
        for key in keys:
            if not isinstance(config[group][key], int) or config[group][key] < 0:
                raise ValueError(f"{group}.{key} must be a nonnegative integer")
    if config["budget"]["max_llm_tokens"] is not None and (not isinstance(config["budget"]["max_llm_tokens"], int) or config["budget"]["max_llm_tokens"] < 0):
        raise ValueError("budget.max_llm_tokens must be null or a nonnegative integer")
    bounds = config["generation"]["files_per_workspace"]
    if not isinstance(bounds, list) or len(bounds) != 2 or not all(isinstance(v, int) for v in bounds) or not 1 <= bounds[0] <= bounds[1]:
        raise ValueError("generation.files_per_workspace must be [positive minimum, maximum]")
    return config


def fingerprint(config: dict, sections: list[str] | None = None) -> str:
    if sections:
        return digest({key: config[key] for key in sections})
    return digest({k: v for k, v in config.items() if not k.startswith("_")})


def initialize(output: Path):
    demo = deepcopy(DEFAULT)
    demo["taxonomy"] = "taxonomy.yaml"
    online = deepcopy(demo)
    online["run"].update(fixture=False, output_dir="../../runs/online")
    online["sampling"]["target_seeds"] = 3
    online["llm"]["enabled"] = True
    providers = {
        "search": {"provider": "brave", "api_key_env": "BRAVE_SEARCH_API_KEY"},
        "llm": DEFAULT["llm"],
        "note": "Copy llm settings into pipeline.yaml; secrets only in environment. Set an available model explicitly.",
    }
    files = {"pipeline.yaml": yaml.safe_dump(demo, sort_keys=False),
             "online.yaml": yaml.safe_dump(online, sort_keys=False),
             "taxonomy.yaml": yaml.safe_dump(taxonomy(), sort_keys=False),
             "providers.example.yaml": yaml.safe_dump(providers, sort_keys=False),
             "source_urls.jsonl": ""}
    for name, content in files.items():
        path = output / name
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
    for name, content in files.items():
        atomic_bytes(output / name, content.encode())
