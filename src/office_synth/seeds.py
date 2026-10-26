from __future__ import annotations

import re

from .schemas import CandidateSeed, EvidenceRecord, GroundedClaim, SeedBlueprint, SourceRecord
from .storage import digest, identifier


def independent_source_groups(sources, evidence):
    """Cluster mirrors and near-identical evidence text before counting source materials."""
    groups, representatives = {}, []
    for source in sources:
        text = " ".join(e.excerpt.casefold() for e in evidence if e.source_id == source.id)
        tokens = re.findall(r"\w+", text)
        shingles = {tuple(tokens[i:i+4]) for i in range(max(0, len(tokens)-3))}
        match = None
        for previous, prior_shingles in representatives:
            if source.content_hash == previous.content_hash or (shingles and len(shingles & prior_shingles) / max(1, len(shingles | prior_shingles)) >= 0.85):
                match = groups[previous.id]
                break
        groups[source.id] = match or source.id
        if match is None:
            representatives.append((source, shingles))
    return groups


def extract_evidence(source: SourceRecord, parsed: dict) -> list[EvidenceRecord]:
    records = []
    for unit in parsed["units"]:
        # Extract complete bounded sentences, not a model-generated paraphrase.
        for sentence in re.split(r"(?<=[.!?。])\s+", unit["text"]):
            if len(sentence) < 35 or len(sentence) > 1200:
                continue
            text = sentence.casefold()
            workflow = any(k in text for k in ("compare", "reconcil", "receipt", "invoice", "采购", "收货", "发票", "对账"))
            policy = any(k in text for k in ("must", "shall", "payment", "payable", "effective", "approval", "付款", "应当", "生效", "审批"))
            if not (workflow or policy):
                continue
            purpose = "workflow_reference" if workflow and any(k in text for k in ("match", "compare", "reconcil", "checks receipt", "对账", "匹配")) else "policy_reference" if policy else "workflow_reference"
            records.append(EvidenceRecord(id=identifier("evidence", [source.raw_hash, unit["location"], sentence]),
                                          source_id=source.id, excerpt=sentence, location=unit["location"], claim=sentence,
                                          purpose=purpose, extraction_method="extractive_exact_sentence", source_hash=source.raw_hash,
                                          input_refs=[source.id]))
    return records


def check_evidence(evidence: list[EvidenceRecord], sources: list[SourceRecord], parsed_by_source: dict) -> list[str]:
    errors = []
    by_source = {s.id: s for s in sources}
    for e in evidence:
        source = by_source.get(e.source_id)
        units = parsed_by_source.get(e.source_id, {}).get("units", [])
        if not source or source.raw_hash != e.source_hash:
            errors.append(f"{e.id}: missing source or wrong hash")
        elif not any(unit["location"] == e.location and e.excerpt in unit["text"] for unit in units):
            errors.append(f"{e.id}: excerpt cannot be located")
    return errors


def build_seed(candidate: CandidateSeed, sources: list[SourceRecord], evidence: list[EvidenceRecord], config: dict, llm=None) -> SeedBlueprint:
    source_by_id = {s.id: s for s in sources if s.parse_status == "succeeded"}
    evidence = [e for e in evidence if e.source_id in source_by_id]
    groups = independent_source_groups(list(source_by_id.values()), evidence)
    unique_content = {groups[e.source_id] for e in evidence}
    gaps = []
    workflows = [e for e in evidence if e.purpose == "workflow_reference"]
    policies = [e for e in evidence if e.purpose == "policy_reference"]
    def diverse(items):
        first, rest, seen = [], [], set()
        for e in items:
            if e.source_id not in seen:
                first.append(e)
                seen.add(e.source_id)
            else:
                rest.append(e)
        return (first + rest)[:4]
    workflow_claims = [GroundedClaim(text=e.claim, evidence_ids=[e.id], basis="evidence_supported") for e in diverse(workflows)]
    policy_claims = [GroundedClaim(text=e.claim, evidence_ids=[e.id], basis="evidence_supported") for e in diverse(policies)]
    if llm is not None:
        # The model may select exact quotes; claims must remain verbatim to avoid false entailment.
        selection = llm.select_claims(candidate, evidence)
        by_id = {e.id: e for e in evidence}
        for category in ("workflow", "rules"):
            for key in selection[category]:
                if key not in by_id:
                    raise ValueError("LLM selected unknown evidence ID")
        workflow_claims = [GroundedClaim(text=by_id[key].excerpt, evidence_ids=[key], basis="evidence_supported") for key in selection["workflow"]]
        policy_claims = [GroundedClaim(text=by_id[key].excerpt, evidence_ids=[key], basis="evidence_supported") for key in selection["rules"]]
    if len(unique_content) < config["seeds"]["min_sources"]:
        gaps.append(f"Need {config['seeds']['min_sources']} independent content sources; have {len(unique_content)}")
    if not workflow_claims:
        gaps.append("Missing workflow evidence")
    if not policy_claims:
        gaps.append("Missing policy evidence")
    if candidate.execution_pattern != "three_way_match" or candidate.scenario_id != "settlement":
        gaps.append("This release only instantiates the supplier settlement / three_way_match business family")
    # This implementation's business generator supports a concrete three-way-match family.
    text = " ".join(c.text.casefold() for c in workflow_claims + policy_claims)
    required_keywords = [("purchase", "order", "采购", "订单"), ("receipt", "received", "收货", "入库"), ("invoice", "发票"), ("payment", "payable", "付款", "应付")]
    if not all(any(word in text for word in group) for group in required_keywords):
        gaps.append("Evidence does not establish the implemented purchase/receipt/invoice/payment workflow")
    used_ids = sorted({eid for c in workflow_claims+policy_claims for eid in c.evidence_ids})
    used_sources = sorted({e.source_id for e in evidence if e.id in used_ids})
    if len({groups[sid] for sid in used_sources}) < config["seeds"]["min_sources"]:
        gaps.append("Selected core claims do not cover the required independent source count")
    dimensions = {"domain": candidate.domain_id, "role": candidate.role_id, "scenario": candidate.scenario_id,
                  "work_need": candidate.work_need_id, "business_object": candidate.business_objects[0], "execution_pattern": candidate.execution_pattern}
    family = identifier("family", [sorted(s.content_hash for s in sources), "builtin/settlement-v1"])
    return SeedBlueprint(id=identifier("seed", [candidate.id, used_ids]), candidate_id=candidate.id,
                         dimensions=dimensions, business_goal=f"{candidate.role_id}: reconcile a fictional supplier settlement",
                         workflow=workflow_claims, rules=policy_claims,
                         input_materials=["purchase order", "receipt record", "invoice", "payment agreement"],
                         decision_points=["release matched amount", "hold unreceived quantities"],
                         file_roles=["source_data", "contract", "reconciliation", "management_briefing", "statement"],
                         typical_outputs=["settlement workbook", "reconciliation", "settlement briefing"],
                         variable_parameters=["quantity", "unit price", "payment days", "invoice date"],
                         invariants=["Fictional calculations are synthetic choices, not imported institutional policy", "Payable plus held equals invoice total"],
                         evidence_ids=used_ids, source_ids=used_sources, evidence_gaps=gaps,
                         review_status="needs_evidence" if gaps else "accepted",
                         provenance="fixture" if config["run"]["fixture"] else "online", family_key=family,
                         input_refs=[candidate.id]+used_sources)
