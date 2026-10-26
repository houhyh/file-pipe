from __future__ import annotations

import shutil
from collections import Counter
from importlib.resources import files
from pathlib import Path

from .config import fingerprint
from .generation import generate
from .parsing import parse, MIMES
from .planning import build_plan, ordered, GENERATION_EDGES
from .schemas import CandidateSeed, FilePlan, SeedBlueprint, SourceRecord, WorldInstance, ValidationResult
from .seeds import extract_evidence, check_evidence, build_seed
from .storage import Store, Budget, atomic_bytes, clean_error, digest, identifier, read_json, safe_path, write_json
from .taxonomy import sample
from .validation import validate, assessment
from .world import instantiate, world_errors


STAGES = ["sample", "collect", "build-seeds", "instantiate", "plan", "generate", "validate", "repair", "export"]


def models(path: Path, model):
    if not path.is_file():
        raise RuntimeError(f"Missing prerequisite: {path.name}; run the preceding stage first")
    return [model.model_validate(v) for v in read_json(path)]


class Pipeline:
    def __init__(self, config: dict, *, resume=True, force=False, limit=None, seed_id=None, workspace_id=None, urls=None):
        from .schemas import CONFIG_FINGERPRINT
        CONFIG_FINGERPRINT.set(fingerprint(config))
        self.config = config
        self.root = Path(config["_root"])
        self.store = Store(self.root)
        self.budget = Budget(self.store, config["budget"])
        self.resume, self.force, self.limit = resume, force, limit
        self.seed_id, self.workspace_id, self.urls = seed_id, workspace_id, urls
        self.reused = 0

    def execute(self, stage, obj, inputs, function, force=None):
        self.budget.check_time()
        def run():
            paths = function()
            return [str(p.relative_to(self.root)) for p in paths]
        metadata = {}
        if stage == "collect" and not self.config["run"]["fixture"]:
            metadata["provider"] = "user_urls" if self.urls or self.config["retrieval"]["urls"] else self.config["retrieval"]["provider"]
        elif stage in {"build-seeds", "generate"} and self.config["llm"]["enabled"] and not self.fixture_mode():
            metadata = {"provider": "configured_json_llm", "model": self.config["llm"]["model"]}
        refs, reused = self.store.execute(stage, obj, inputs, run, resume=self.resume, force=self.force if force is None else force, metadata=metadata)
        self.reused += reused
        return refs

    def selected_worlds(self):
        worlds = models(self.root / "worlds.json", WorldInstance)
        if self.seed_id:
            worlds = [w for w in worlds if w.seed_id == self.seed_id]
        if self.workspace_id:
            worlds = [w for w in worlds if w.id == self.workspace_id]
        if (self.workspace_id or self.seed_id) and not worlds:
            raise ValueError("No workspaces match the requested seed/workspace ID")
        return worlds[:self.limit] if self.limit else worlds

    def workspace(self, world):
        return safe_path(self.root / "workspaces", world.id)

    def fixture_mode(self):
        path = self.root / "sources.json"
        if path.exists():
            sources = read_json(path)
            if sources:
                return all(s["source_type"] == "fixture" for s in sources)
        return self.config["run"]["fixture"]

    def sample(self):
        def action():
            candidates, coverage = sample(self.config["_taxonomy"], self.config)
            if self.limit:
                candidates = candidates[:self.limit]
            write_json(self.root / "candidates.json", candidates)
            write_json(self.root / "coverage.json", coverage)
            return [self.root / "candidates.json", self.root / "coverage.json"]
        self.execute("sample", "run", [self.config["_taxonomy"], self.config["sampling"], self.config["run"]["random_seed"], self.limit], action)

    def collect(self):
        candidates = models(self.root / "candidates.json", CandidateSeed)
        def action():
            if not self.config["run"]["fixture"]:
                from .retrieval import collect
                records = collect(self, candidates)
            else:
                records = []
                for name in ["workflow.md", "payment_policy.md"]:
                    data = files("office_synth").joinpath("assets", "fixtures", name).read_bytes()
                    raw_hash = digest(data)
                    path = self.root / "cache" / "blobs" / f"{raw_hash}.md"
                    atomic_bytes(path, data)
                    parsed = parse(path, "md")
                    parsed_path = self.root / "sources" / f"{raw_hash}.json"
                    write_json(parsed_path, parsed)
                    record = SourceRecord(id=identifier("source", ["fixture", name]), original_url=f"fixture://{name}", final_url=f"fixture://{name}",
                                          institution="Project maintainers (synthetic fixture)", title=name, source_type="fixture",
                                          status_code=None, declared_mime="text/markdown", detected_mime=MIMES["md"], parsed_format="md", byte_count=len(data),
                                          raw_hash=raw_hash, content_hash=parsed["content_hash"], local_path=str(path.relative_to(self.root)),
                                          parsed_path=str(parsed_path.relative_to(self.root)), license_status="CC0-1.0 (authored fixture)",
                                          download_status="succeeded", parse_status="succeeded")
                    records.append(record)
            write_json(self.root / "sources.json", records)
            if not any(s.parse_status == "succeeded" for s in records):
                from .retrieval import DownloadError
                raise DownloadError("No sources parsed successfully; inspect sources.json for download/parse errors")
            return [self.root / "sources.json"] + [self.root / s.local_path for s in records if s.local_path] + [self.root / s.parsed_path for s in records if s.parsed_path]
        self.execute("collect", "run", [self.config["run"]["fixture"], self.config["retrieval"], self.urls,
                                        [c.model_dump(exclude={"created_at"}) for c in candidates]], action)

    def build_seeds(self):
        candidates = models(self.root / "candidates.json", CandidateSeed)
        sources = models(self.root / "sources.json", SourceRecord)
        def action():
            parsed = {}
            for source in sources:
                if source.parse_status != "succeeded":
                    continue
                raw = self.root / source.local_path
                fmt = source.parsed_format
                if not fmt:
                    from .parsing import detect
                    fmt = detect(raw.read_bytes(), source.declared_mime, source.original_url)
                result = parse(raw, fmt)
                if result["raw_hash"] != source.raw_hash or result["content_hash"] != source.content_hash:
                    raise ValueError(f"Source integrity failure: {source.id}; recollect before building seeds")
                parsed[source.id] = result
            evidence = [e for s in sources if s.id in parsed for e in extract_evidence(s, parsed[s.id])]
            errors = check_evidence(evidence, sources, parsed)
            if errors:
                raise ValueError("Invalid evidence: " + "; ".join(errors))
            llm = None
            if not self.fixture_mode() and self.config["llm"]["enabled"]:
                from .providers import LLM
                llm = LLM(self)
            seeds = []
            for candidate in candidates:
                candidate_sources = sources
                # Online search hits retain candidate association; explicit URLs can support any candidate.
                if not self.fixture_mode():
                    candidate_sources = [s for s in sources if not s.input_refs or candidate.id in s.input_refs]
                seed_config = {**self.config, "run": {**self.config["run"], "fixture": self.fixture_mode()}}
                seed = build_seed(candidate, candidate_sources, evidence, seed_config, llm)
                seeds.append(seed)
                if sum(s.review_status == "accepted" for s in seeds) >= self.config["sampling"]["target_seeds"]:
                    break
            write_json(self.root / "evidence.json", evidence)
            write_json(self.root / "seeds.json", seeds)
            return [self.root / "evidence.json", self.root / "seeds.json"]
        inputs = [digest((self.root / name).read_bytes()) for name in ["candidates.json", "sources.json"]]
        inputs += [digest((self.root / s.local_path).read_bytes()) for s in sources if s.parse_status == "succeeded"]
        self.execute("build-seeds", "run", [inputs, self.config["seeds"], self.config["llm"], self.config["sampling"]], action)

    def instantiate(self):
        seeds = models(self.root / "seeds.json", SeedBlueprint)
        accepted = [s for s in seeds if s.review_status == "accepted"]
        if not accepted:
            raise RuntimeError("No accepted seeds. Inspect seeds.json evidence_gaps and source failures")
        def action():
            worlds = [instantiate(s, i, self.config) for s in accepted for i in range(self.config["sampling"]["instances_per_seed"])]
            for world in worlds:
                errors = world_errors(world)
                if errors:
                    raise ValueError(str(errors))
                write_json(self.workspace(world) / "private" / "world.json", world)
                write_json(self.workspace(world) / "private" / "seed.json", next(s for s in seeds if s.id == world.seed_id))
                write_json(self.workspace(world) / "private" / "evidence.json", read_json(self.root / "evidence.json"))
            write_json(self.root / "worlds.json", worlds)
            return [self.root / "worlds.json"] + [self.workspace(w) / "private" / name for w in worlds for name in ["world.json", "seed.json", "evidence.json"]]
        self.execute("instantiate", "run", [digest((self.root / "seeds.json").read_bytes()), self.config["sampling"]["instances_per_seed"],
                                             self.config["run"]["random_seed"], self.config["generation"]["enable_event"]], action)

    def plan(self):
        for world in self.selected_worlds():
            def action(world=world):
                path = self.workspace(world) / "private" / "plan.json"
                write_json(path, build_plan(world, self.config))
                return [path]
            self.execute("plan", world.id, [world, self.config["generation"]], action)

    def generate(self, repair_ids=None):
        for world in self.selected_worlds():
            workspace = self.workspace(world)
            plans = models(workspace / "private" / "plan.json", FilePlan)
            from .planning import plan_errors
            errors = plan_errors(plans, world, self.config)
            if errors:
                raise ValueError("Invalid plan: " + "; ".join(errors))
            llm = None
            if self.config["llm"]["enabled"] and not self.fixture_mode():
                from .providers import LLM
                llm = LLM(self)
            for p in ordered(plans):
                upstream = [[d.file_id, digest((workspace / "public" / next(q.relative_path for q in plans if q.id == d.file_id)).read_bytes())]
                            for d in p.dependencies if d.type in GENERATION_EDGES]
                file_world = {fid: world.ledger()[fid] for fid in p.fact_ids}
                inputs = [p, file_world, world.transactions, upstream, self.config["llm"]]
                def action(p=p):
                    target = workspace / "public" / p.relative_path
                    if target.is_file():
                        archive = workspace / "private" / "history" / p.id / (digest(target.read_bytes()) + "." + p.format)
                        atomic_bytes(archive, target.read_bytes())
                    narrative = llm.narrative(world.role) if llm and p.format != "xlsx" else None
                    return generate(p, world, plans, workspace, narrative)
                force = self.force or (repair_ids is not None and (world.id, p.id) in repair_ids)
                self.execute("generate", f"{world.id}/{p.id}", inputs, action, force=force)

    def validate(self):
        for world in self.selected_worlds():
            workspace = self.workspace(world)
            plans = models(workspace / "private" / "plan.json", FilePlan)
            def action(world=world, plans=plans, workspace=workspace):
                results = validate(world, plans, workspace, self.config)
                path = workspace / "private" / "validation.json"
                quality = workspace / "private" / "quality.json"
                write_json(path, results)
                write_json(quality, assessment(results, self.config))
                return [path, quality]
            hashes = [(p.id, digest((workspace / "public" / p.relative_path).read_bytes()) if (workspace / "public" / p.relative_path).is_file() else None) for p in plans]
            self.execute("validate", world.id, [world, plans, hashes, self.config["validation"]], action)

    def repair(self):
        worlds = self.selected_worlds()
        paths = [self.workspace(w) / "private" / "validation.json" for w in worlds]
        if any(not p.exists() for p in paths):
            raise RuntimeError("Missing prerequisite: run validate before repair")
        def action():
            self._repair()
            return [self.workspace(w) / "private" / "quality.json" for w in worlds]
        scope = identifier("selection", [w.id for w in worlds])
        self.execute("repair", scope, [[digest(p.read_bytes()) for p in paths], self.config["generation"]["max_repair_attempts"]], action)

    def _repair(self):
        for attempt in range(self.config["generation"]["max_repair_attempts"]):
            repair_ids = set()
            for world in self.selected_worlds():
                workspace = self.workspace(world)
                results = models(workspace / "private" / "validation.json", ValidationResult)
                failures = [r for r in results if r.status == "fail" and r.severity == "blocking"]
                if not failures:
                    continue
                diagnostics = workspace / "private" / "repair" / f"attempt-{attempt+1}-{identifier('diag', [r.model_dump() for r in failures])}.json"
                write_json(diagnostics, failures)
                if any(r.scope == world.id for r in failures):
                    # Deterministic world/plan rebuild from the accepted seed; never silently mutate source evidence.
                    seed = SeedBlueprint.model_validate(read_json(workspace / "private" / "seed.json"))
                    rebuilt = next(instantiate(seed, i, self.config) for i in range(self.config["sampling"]["instances_per_seed"]) if instantiate(seed, i, self.config).id == world.id)
                    worlds = models(self.root / "worlds.json", WorldInstance)
                    write_json(self.root / "worlds.json", [rebuilt if w.id == world.id else w for w in worlds])
                    write_json(workspace / "private" / "world.json", rebuilt)
                    old_plan = workspace / "private" / "plan.json"
                    atomic_bytes(workspace / "private" / "history" / f"plan-{digest(old_plan.read_bytes())}.json", old_plan.read_bytes())
                    write_json(old_plan, build_plan(rebuilt, self.config))
                for failure in failures:
                    if failure.scope != world.id:
                        repair_ids.add((world.id, failure.scope))
            if not repair_ids and not any(read_json(self.workspace(w) / "private" / "quality.json")["status"] == "rejected" for w in self.selected_worlds()):
                break
            # File input hashes automatically invalidate actual dependent paths.
            self.generate(repair_ids)
            self.validate()

    def export(self):
        worlds = self.selected_worlds()
        paths = [self.workspace(w) / "private" / name for w in worlds for name in ["quality.json", "plan.json"]]
        if any(not p.exists() for p in paths):
            raise RuntimeError("Missing prerequisite: run plan and validate before export")
        actual_files = []
        for world in worlds:
            plans = models(self.workspace(world) / "private" / "plan.json", FilePlan)
            for plan in plans:
                path = safe_path(self.workspace(world) / "public", plan.relative_path)
                actual_files.append([world.id, plan.id, digest(path.read_bytes()) if path.is_file() else None])
        def action():
            self._export()
            entries = read_json(self.root / "workspaces.json")
            refs = [self.root / "workspaces.json"]
            for entry in entries:
                manifest_path = safe_path(self.root, entry["manifest"])
                refs.append(manifest_path)
                refs += [safe_path(manifest_path.parent, f["path"]) for f in read_json(manifest_path)["files"]]
            return refs
        self.execute("export", identifier("selection", [w.id for w in worlds]),
                     [worlds, [digest(p.read_bytes()) for p in paths], actual_files, self.config["validation"]], action)
        return read_json(self.root / "workspaces.json")

    def _export(self):
        entries = []
        for world in self.selected_worlds():
            workspace = self.workspace(world)
            quality_path = workspace / "private" / "quality.json"
            if not quality_path.exists():
                raise RuntimeError("Missing prerequisite: run validate before export")
            quality = read_json(quality_path)
            if quality["status"] != "accepted":
                continue
            plans = models(workspace / "private" / "plan.json", FilePlan)
            # Verify validation still describes current files; stale success cannot authorize export.
            checks = validate(world, plans, workspace, {**self.config, "validation": {**self.config["validation"], "render": False, "recalculate": False}})
            critical = [r for r in checks if r.status == "fail" and r.severity == "blocking"]
            if critical:
                raise RuntimeError(f"Stale or invalid workspace {world.id}; run validate/repair before export")
            target = safe_path(self.root / "export", world.id)
            manifest = {"schema_version": "1.0", "workspace_id": world.id, "group_key": world.group_key,
                        "provenance": "fixture" if self.fixture_mode() else "online", "quality_tier": quality["quality_tier"], "files": []}
            for p in plans:
                source = workspace / "public" / p.relative_path
                dest = safe_path(target / "public", p.relative_path)
                atomic_bytes(dest, source.read_bytes())
                manifest["files"].append({"file_id": p.id, "path": "public/"+p.relative_path, "format": p.format, "sha256": digest(source.read_bytes()),
                                           "version": p.version_id, "dependencies": [d.model_dump() for d in p.dependencies]})
            write_json(target / "manifest.json", manifest)
            entries.append({"workspace_id": world.id, "manifest": f"export/{world.id}/manifest.json", "group_key": world.group_key})
        index = self.root / "workspaces.json"
        if index.exists() and (self.seed_id or self.workspace_id or self.limit):
            selected = {w.id for w in self.selected_worlds()}
            entries = [e for e in read_json(index) if e["workspace_id"] not in selected] + entries
        write_json(index, entries)
        return entries

    def run(self):
        for stage in STAGES:
            print(f"[{stage}]", flush=True)
            getattr(self, stage.replace("-", "_"))()

    def report(self):
        stages = self.store.stages()
        durations = {k.removeprefix("duration:"): float(v) for k, v in self.store.db.execute("SELECT key,value FROM meta WHERE key LIKE 'duration:%'")}
        for stage in stages:
            stage["elapsed_seconds"] = round(durations.get(stage["id"], 0), 3)
        workspaces = []
        duplicates = {}
        texts = []
        formats = Counter()
        role_counts = Counter()
        content_hashes, structure_profiles = set(), set()
        if (self.root / "worlds.json").exists():
            for world in models(self.root / "worlds.json", WorldInstance):
                workspace = self.workspace(world)
                quality_path = workspace / "private" / "quality.json"
                quality = read_json(quality_path) if quality_path.exists() else {"status": "pending"}
                checks_path = workspace / "private" / "validation.json"
                checks = read_json(checks_path) if checks_path.exists() else []
                role_counts[world.role] += 1
                plan_path = workspace / "private" / "plan.json"
                if plan_path.exists():
                    structure_profiles.add(digest([(p["format"], p["role"], p["template_ref"], p["structure"]) for p in read_json(plan_path)]))
                workspaces.append({"id": world.id, "role": world.role, "group_key": world.group_key, "event_enabled": bool(world.events),
                                   "versions": [r["version"] for r in world.rules], **quality,
                                   "skipped": [{"scope": r["scope"], "check": r["check_id"], "reason": r["observed"]} for r in checks if r["status"] == "skipped"]})
                for p in sorted((workspace / "public").glob("*")):
                    if not p.is_file():
                        continue
                    relative = str(p.relative_to(self.root))
                    duplicates.setdefault(digest(p.read_bytes()), []).append(relative)
                    formats[p.suffix.lstrip(".")] += 1
                    try:
                        text = parse(p, p.suffix.lstrip("."))["text"].casefold()
                        content_hashes.add(digest(text))
                        import re
                        tokens = re.findall(r"\w+", text)
                        shingles = {tuple(tokens[i:i+4]) for i in range(max(0, len(tokens)-3))}
                        texts.append((relative, shingles))
                    except Exception:
                        pass
        near = []
        for i, (a, sa) in enumerate(texts):
            for b, sb in texts[i+1:]:
                similarity = len(sa & sb) / max(1, len(sa | sb))
                if similarity >= 0.8:
                    near.append({"a": a, "b": b, "jaccard": round(similarity, 4), "action": "marked_only"})
        seeds = read_json(self.root / "seeds.json") if (self.root / "seeds.json").exists() else []
        sources = read_json(self.root / "sources.json") if (self.root / "sources.json").exists() else []
        report = {"schema_version": "1.0", "fixture": self.fixture_mode(),
                  "network_search_requests": self.store.counters().get("search_requests", 0),
                  "llm_requests": self.store.counters().get("llm_requests", 0), "consumption": self.store.counters(),
                  "tokens": self.store.counters().get("tokens") if self.store.counters().get("known_usage_responses", 0) == self.store.counters().get("llm_requests", 0) else None, "cost_usd": None,
                  "stage_statuses": dict(Counter(s["status"] for s in stages)), "failure_classes": dict(Counter(s["error_type"] for s in stages if s["error_type"])),
                  "stages": stages, "accepted_seeds": sum(s["review_status"] == "accepted" for s in seeds),
                  "stage_flow": {"candidates": len(read_json(self.root / "candidates.json")) if (self.root / "candidates.json").exists() else 0,
                                 "downloaded_sources": sum(s["download_status"] == "succeeded" for s in sources),
                                 "parsed_sources": sum(s["parse_status"] == "succeeded" for s in sources), "blueprints": len(seeds),
                                 "instances": len(workspaces), "accepted_workspaces": sum(w["status"] == "accepted" for w in workspaces)},
                  "seed_gaps": [{"id": s["id"], "gaps": s["evidence_gaps"]} for s in seeds if s["evidence_gaps"]],
                  "seed_shortfall": max(0, self.config["sampling"]["target_seeds"] - sum(s["review_status"] == "accepted" for s in seeds)),
                  "source_count": len(sources), "source_failures": [s for s in sources if s["parse_status"] != "succeeded"],
                  "workspaces": workspaces, "role_coverage": dict(role_counts), "format_coverage": dict(formats),
                  "binary_duplicates": [v for v in duplicates.values() if len(v) > 1], "near_duplicates": near,
                  "diversity": {"seed_blueprints": len(seeds), "instances": len(workspaces), "template_groups": 1 if workspaces else 0,
                                "business_families": len({(s["dimensions"]["scenario"], s["dimensions"]["execution_pattern"]) for s in seeds}),
                                "unique_content_hashes": len(content_hashes), "structure_profiles": len(structure_profiles),
                                "note": "Role perspectives share one settlement business family; instances are not new business types"},
                  "reused_stages_this_invocation": self.reused}
        if (self.root / "coverage.json").exists():
            report["sampling_coverage"] = read_json(self.root / "coverage.json")
        write_json(self.root / "report.json", report)
        return report
