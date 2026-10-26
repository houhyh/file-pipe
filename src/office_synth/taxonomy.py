from __future__ import annotations

import random
from collections import Counter

from .schemas import CandidateSeed
from .storage import identifier


DIMENSIONS = ("domain", "role", "scenario", "work_need", "business_object", "execution_pattern")


def matches(combo: dict, condition: dict) -> bool:
    return all(combo.get(key) in (values if isinstance(values, list) else [values]) for key, values in condition.items())


def compatible(combo: dict, rules: list[dict]) -> bool:
    for rule in rules:
        if matches(combo, rule.get("when", {})):
            if rule["effect"] == "deny" and matches(combo, rule.get("require", {})):
                return False
            if rule["effect"] == "allow" and not matches(combo, rule.get("require", {})):
                return False
    return True


def sample(tax: dict, config: dict) -> tuple[list[CandidateSeed], dict]:
    dimensions = tax["dimensions"]
    for name in DIMENSIONS:
        values = dimensions[name]
        if not values or len({v["id"] for v in values}) != len(values):
            raise ValueError(f"Empty dimension or duplicate IDs: {name}")
        for value in values:
            if not {"id", "name", "definition", "synonyms"} <= value.keys():
                raise ValueError(f"Incomplete taxonomy entry in {name}")
    rng = random.Random(config["run"]["random_seed"])
    limit = config["sampling"]["candidate_limit"]
    seen, pool = set(), []
    # Bounded random pool, never enumerate the Cartesian product.
    for _ in range(limit * 40):
        combo = {key: rng.choice(dimensions[key])["id"] for key in DIMENSIONS}
        key = tuple(combo.values())
        if key not in seen and compatible(combo, tax.get("compatibility", [])):
            seen.add(key)
            pool.append(combo)
    coverage = Counter()
    selected = []
    while pool and len(selected) < limit:
        combo = min(pool, key=lambda c: sum(coverage[(k, v)] for k, v in c.items()))
        pool.remove(combo)
        selected.append(combo)
        coverage.update(combo.items())
    candidates = [CandidateSeed(id=identifier("candidate", c), domain_id=c["domain"], role_id=c["role"],
                                scenario_id=c["scenario"], work_need_id=c["work_need"],
                                business_objects=[c["business_object"]], execution_pattern=c["execution_pattern"])
                  for c in selected]
    report = {"candidate_count": len(candidates), "candidate_limit": limit,
              "marginal": {k: {v["id"]: coverage[(k, v["id"])] for v in dimensions[k]} for k in DIMENSIONS},
              "unique_combinations": len(selected), "bounded_draws": limit * 40}
    return candidates, report
