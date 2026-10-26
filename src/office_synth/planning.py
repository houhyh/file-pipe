from __future__ import annotations

from graphlib import TopologicalSorter, CycleError

from .schemas import FileDependency, FilePlan, WorldInstance


GENERATION_EDGES = {"derived_from", "summarizes"}


def build_plan(world: WorldInstance, config: dict) -> list[FilePlan]:
    ledger = world.ledger()
    base_facts = ["buyer", "supplier", "order_id", "invoice_id", "invoice_date", "invoice_total", "payable_total", "held_total"]
    all_lines = [f.fact_id for f in world.facts if f.fact_id.startswith("line_")]
    result = []

    def add(key, name, fmt, role, facts, version="v1", dependencies=None):
        rule = next(r for r in world.rules if r["version"] == version)
        result.append(FilePlan(id=key, relative_path=f"{name}.{fmt}", format=fmt, role=role,
                               author_entity_id="buyer", business_date=rule["valid_from"] if role in {"contract", "amendment", "purchase_order"} or version == "v2" else ledger["invoice_date"], version_id=version,
                               valid_from=rule["valid_from"], valid_to=rule["valid_to"], fact_ids=facts,
                               dependencies=[FileDependency(file_id=k, type=t) for k, t in (dependencies or [])],
                               structure={"sections": ["Business context", "Transaction details", "Settlement decision"],
                                          "role_perspective": world.role}, input_refs=[world.id]))

    add("contract_v1", "01_contract_v1", "docx", "contract", ["buyer", "supplier", "payment_days_v1", "due_date_v1", "invoice_id"])
    add("order", "02_purchase_order", "docx", "purchase_order", ["buyer", "supplier", "order_id", "order_date"] + all_lines,
        dependencies=[("contract_v1", "references")])
    add("data", "03_settlement_data", "xlsx", "source_data", base_facts + all_lines + ["payment_days_v1", "due_date_v1"],
        dependencies=[("order", "references"), ("contract_v1", "references")])
    summary_facts = base_facts + ["payment_days_v1", "due_date_v1"]
    add("reconciliation_v1", "04_reconciliation_v1", "docx", "reconciliation", summary_facts,
        dependencies=[("data", "derived_from"), ("contract_v1", "references")])
    add("briefing", "05_settlement_briefing", "pptx", "management_briefing", summary_facts, dependencies=[("data", "summarizes")])
    add("statement", "06_settlement_statement", "pdf", "statement", summary_facts, dependencies=[("data", "derived_from")])
    if world.events:
        add("contract_v2", "07_terms_amendment_v2", "docx", "amendment", ["buyer", "supplier", "invoice_id", "payment_days_v2", "due_date_v2"],
            version="v2", dependencies=[("contract_v1", "supersedes")])
        add("reconciliation_v2", "08_reconciliation_v2", "docx", "reconciliation", base_facts + ["payment_days_v2", "due_date_v2"],
            version="v2", dependencies=[("data", "derived_from"), ("contract_v2", "references"), ("reconciliation_v1", "supersedes")])
    for fmt in config["generation"].get("optional_formats", []):
        if fmt not in {"md", "eml"}:
            raise ValueError("Optional formats must be md or eml")
        add(f"communication_{fmt}", f"09_communication_{fmt}", fmt, "communication", summary_facts, dependencies=[("data", "derived_from")])
    errors = plan_errors(result, world, config)
    if errors:
        raise ValueError("Invalid plan: " + "; ".join(errors))
    return result


def ordered(plans: list[FilePlan]) -> list[FilePlan]:
    by_id = {p.id: p for p in plans}
    graph = {p.id: {d.file_id for d in p.dependencies if d.type in GENERATION_EDGES} for p in plans}
    try:
        keys = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError("Generation dependency cycle") from exc
    if any(k not in by_id for k in keys):
        raise ValueError("Unknown upstream file ID")
    return [by_id[k] for k in keys]


def plan_errors(plans: list[FilePlan], world: WorldInstance, config: dict) -> list[str]:
    errors = []
    ids = {p.id for p in plans}
    paths = {p.relative_path for p in plans}
    if len(ids) != len(plans) or len(paths) != len(plans):
        errors.append("duplicate file IDs or paths")
    if not set(config["generation"]["required_formats"]) <= {p.format for p in plans}:
        errors.append("required format coverage")
    lo, hi = config["generation"]["files_per_workspace"]
    if not lo <= len(plans) <= hi + len(config["generation"].get("optional_formats", [])):
        errors.append("file count outside configured range")
    facts = world.ledger()
    for p in plans:
        if any(d.file_id not in ids for d in p.dependencies):
            errors.append(f"unknown reference in {p.id}")
        if any(fid not in facts for fid in p.fact_ids):
            errors.append(f"unknown facts in {p.id}")
        for dependency in p.dependencies:
            if dependency.type == "supersedes" and dependency.file_id in ids:
                previous = next(q for q in plans if q.id == dependency.file_id)
                if p.version_id == previous.version_id or p.valid_from <= previous.valid_from or previous.valid_to != p.valid_from:
                    errors.append(f"invalid supersedes chain in {p.id}")
        rules = [r for r in world.rules if r["version"] == p.version_id]
        if len(rules) != 1 or p.valid_from != rules[0]["valid_from"] or p.valid_to != rules[0]["valid_to"]:
            errors.append(f"invalid version in {p.id}")
        if p.business_date < p.valid_from or (p.valid_to and p.business_date >= p.valid_to):
            errors.append(f"business date outside version validity in {p.id}")
    if not {"payable_total", "invoice_id", "order_id"} <= {f for p in plans for f in p.fact_ids}:
        errors.append("critical facts uncovered")
    try:
        ordered(plans)
    except ValueError as exc:
        errors.append(str(exc))
    return errors
