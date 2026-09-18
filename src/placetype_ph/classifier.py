from __future__ import annotations

from dataclasses import replace

from .cache import DecisionCache
from .crosswalk import Crosswalk
from .eligibility import decide_eligibility
from .fusion import entry_roots, fuse, intersect_subtrees, normalize_roots
from .models import (
    ClassificationResult,
    CrosswalkEntry,
    Eligibility,
    FusionStatus,
    MappingKind,
    SourceEvidence,
    SourceField,
)
from .openplaces import Row, entity_match_flags, evidence_text, source_evidence
from .retrieval import TaxonomyRetriever
from .taxonomy import Taxonomy
from .traversal import HierarchicalTraverser

_RESOLVED = {FusionStatus.SINGLE, FusionStatus.NESTED, FusionStatus.INTERSECT}
_UNRESOLVED = {FusionStatus.EMPTY, FusionStatus.CONFLICT, FusionStatus.UNION}


class EntityClassifier:
    def __init__(
        self,
        taxonomy: Taxonomy,
        crosswalk: Crosswalk | None = None,
        traverser: HierarchicalTraverser | None = None,
        cache: DecisionCache | None = None,
    ):
        self.taxonomy = taxonomy
        self.crosswalk = crosswalk
        self.traverser = traverser
        self.cache = cache
        self.retriever = TaxonomyRetriever(taxonomy) if traverser is not None else None
        self.applicable_crosswalk_entries = 0
        # Ambiguous crosswalk matches are counted, not fatal. The run continues and the
        # affected rows are flagged for review; the counts reach run.json.
        self.ambiguities: dict[str, int] = {}
        self.ambiguity_rows = 0
        if crosswalk is not None:
            self.applicable_crosswalk_entries = crosswalk.count_for(
                taxonomy.scheme, taxonomy.version
            )
            self._validate_crosswalk(crosswalk)

    def reset_run_diagnostics(self) -> None:
        """Clear per-run counters when a classifier object is reused."""
        self.ambiguities.clear()
        self.ambiguity_rows = 0

    def _validate_crosswalk(self, crosswalk: Crosswalk) -> None:
        """Report every bad row at once, not just the first one a reviewer hits."""
        problems: list[str] = []
        for entry in crosswalk.entries:
            if entry.scheme != self.taxonomy.scheme or entry.version != self.taxonomy.version:
                continue
            label = f"{entry.source}:{entry.source_value!r}"
            if len(set(entry.codes)) != len(entry.codes):
                problems.append(f"{label} repeats one or more taxonomy codes")
                continue
            if entry.mapping_kind in {MappingKind.EXACT, MappingKind.SUBTREE}:
                if len(entry.codes) != 1:
                    problems.append(
                        f"{label} has mapping_kind {entry.mapping_kind} but "
                        f"{len(entry.codes)} codes; expected exactly one"
                    )
                    continue
            elif entry.mapping_kind == MappingKind.UNION:
                if len(entry.codes) < 2:
                    problems.append(
                        f"{label} has mapping_kind UNION but {len(entry.codes)} codes; "
                        "expected at least two distinct codes"
                    )
                    continue
            elif entry.codes:
                problems.append(
                    f"{label} has mapping_kind {entry.mapping_kind} but also codes "
                    f"{list(entry.codes)}"
                )
                continue
            else:
                continue
            unknown = [c for c in entry.codes if c not in self.taxonomy.nodes]
            if unknown:
                problems.append(f"{label} references unknown codes {unknown}")
                continue
            if entry.mapping_kind == MappingKind.UNION:
                roots = normalize_roots(self.taxonomy, entry.codes)
                if len(roots) < 2:
                    problems.append(
                        f"{label} has a UNION whose codes collapse to one subtree; "
                        "use SUBTREE/EXACT for the surviving root instead"
                    )
        if problems:
            raise ValueError(
                f"{len(problems)} invalid crosswalk row(s) for "
                f"{self.taxonomy.scheme} {self.taxonomy.version}:\n  " + "\n  ".join(problems)
            )

    def _name_rule_precedence(
        self, entries: list[CrosswalkEntry], flags: list[str]
    ) -> list[CrosswalkEntry]:
        """A reviewed name rule outranks a broad category rule it contradicts.

        Refinement is unchanged: when the two are compatible both stay in the source's
        dependency group and fusion intersects them. When their asserted subtrees are
        disjoint, the category rule is the bulk mapping and the name rule is the specific
        hand-written judgment, so the name rule wins instead of both being widened away.
        """
        if len(entries) < 2:
            return entries
        category = [e for e in entries if e.source_field == SourceField.CATEGORY]
        name = [e for e in entries if e.source_field == SourceField.NAME]
        if not category or not name:
            return entries
        category_entry = category[0]
        name_entry = name[0]
        coded = {MappingKind.EXACT, MappingKind.SUBTREE, MappingKind.UNION}

        # NOT_ACTIVITY is a substantive classification decision, not merely missing text.
        # A specific reviewed name rule must therefore be able to correct a broad category
        # rule in either direction (e.g. a place category that says restaurant but an exact
        # landmark name reviewed as non-economic). UNCODEABLE is different: it says the
        # name itself carries no usable signal and should not erase a useful category.
        if (
            name_entry.mapping_kind == MappingKind.NOT_ACTIVITY
            and category_entry.mapping_kind in coded
        ) or (
            name_entry.mapping_kind in coded
            and category_entry.mapping_kind == MappingKind.NOT_ACTIVITY
        ):
            flags.append("CROSSWALK_NAME_RULE_OVERRODE_CATEGORY")
            return name

        category_roots = entry_roots(self.taxonomy, category_entry)
        name_roots = entry_roots(self.taxonomy, name_entry)
        if not category_roots or not name_roots:
            return entries
        if intersect_subtrees(self.taxonomy, category_roots, name_roots):
            return entries
        flags.append("CROSSWALK_NAME_RULE_OVERRODE_CATEGORY")
        return name

    def _mapped_evidence(self, row: Row) -> tuple[list[SourceEvidence], list[str]]:
        out: list[SourceEvidence] = []
        flags: list[str] = []
        row_had_ambiguity = False
        for e in source_evidence(row):
            entries: list[CrosswalkEntry] = []
            if self.crosswalk is not None:
                found = self.crosswalk.matches(
                    e.source,
                    self.taxonomy.scheme,
                    self.taxonomy.version,
                    category=e.category,
                    name=e.name,
                )
                for detail in found.ambiguities:
                    self.ambiguities[detail] = self.ambiguities.get(detail, 0) + 1
                    row_had_ambiguity = True
                    flags.append(f"CROSSWALK_AMBIGUOUS:{e.source}")
                entries = self._name_rule_precedence(list(found.entries), flags)
            if entries:
                out.extend(replace(e, mapping=mapping) for mapping in entries)
            else:
                out.append(replace(e, mapping=None))
        if row_had_ambiguity:
            self.ambiguity_rows += 1
        return out, sorted(set(flags))

    def _status_for(self, base: str, code: str | None) -> str:
        """PCPC results are potential product families whichever component decided them.

        The label used to depend on the deciding component, so a crosswalk-resolved PCPC
        row came out as SINGLE and quietly escaped the documented caveat.
        """
        if self.taxonomy.scheme != "pcpc" or code is None:
            return base
        suffix = "PARTIAL" if self.taxonomy.has_children(code) else "FULL"
        return f"POTENTIAL_PRODUCT_FAMILY_{suffix}"

    def _result(
        self,
        canonical_id: str,
        code: str | None,
        status: str,
        component: str,
        **kwargs: object,
    ) -> ClassificationResult:
        result = ClassificationResult(
            canonical_id,
            self.taxonomy.scheme,
            self.taxonomy.version,
            code,
            self.taxonomy.level_of(code),
            status,
            component,
            **kwargs,  # type: ignore[arg-type]
        )
        if code is not None:
            result.classification_depth = self.taxonomy.depth(code)
            result.branch_max_depth = self.taxonomy.branch_max_depth(code)
        result.max_depth = self.taxonomy.max_depth
        return result

    def classify_row(self, row: Row, product_text: str | None = None) -> ClassificationResult:
        canonical_id = str(row["canonical_id"])
        mapped, evidence_flags = self._mapped_evidence(row)
        fusion = fuse(self.taxonomy, mapped)
        eligibility = decide_eligibility(mapped)

        if self.taxonomy.scheme == "psic" and eligibility == Eligibility.NOT_ACTIVITY:
            return self._result(
                canonical_id,
                None,
                "NOT_PSIC_ACTIVITY",
                "CROSSWALK",
                evidence_sources=[e.source for e in mapped if e.mapping],
                flags=evidence_flags,
            )

        conflict_flags = entity_match_flags(row, fusion.status == FusionStatus.CONFLICT)
        if conflict_flags:
            return self._result(
                canonical_id,
                None,
                "REVIEW_ENTITY_MATCH",
                "FUSION",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=fusion.evidence_sources,
                flags=evidence_flags + fusion.flags + conflict_flags,
            )

        if fusion.code is not None and fusion.status in _RESOLVED:
            return self._result(
                canonical_id,
                fusion.code,
                self._status_for(fusion.status.value, fusion.code),
                "FUSION",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=fusion.evidence_sources,
                flags=evidence_flags + fusion.flags,
            )

        # PSCC is commodity-level. Do not infer a traded commodity from a place name alone.
        # It is allowed if a crosswalk already supplied commodity evidence, or explicit
        # product text was given.
        product_text = product_text.strip() if product_text else None
        has_product_text = bool(product_text)
        if (
            self.taxonomy.scheme == "pscc"
            and not has_product_text
            and fusion.status == FusionStatus.EMPTY
        ):
            return self._result(
                canonical_id,
                None,
                "NO_PRODUCT_EVIDENCE",
                "POLICY",
                flags=evidence_flags + ["PSCC_REQUIRES_PRODUCT_EVIDENCE"],
            )

        if self.traverser is None:
            extra = ["LLM_NOT_CONFIGURED"] if fusion.status in _UNRESOLVED else []
            return self._result(
                canonical_id,
                fusion.code,
                self._status_for(fusion.status.value, fusion.code),
                "FUSION",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=fusion.evidence_sources,
                flags=evidence_flags + fusion.flags + extra,
            )

        text = product_text if has_product_text else evidence_text(row)
        restriction = (
            fusion.candidate_codes
            if fusion.status in {FusionStatus.CONFLICT, FusionStatus.UNION}
            and fusion.candidate_codes
            else None
        )
        retrieved: list[dict[str, object]] = []
        if restriction is None and self.retriever is not None:
            hits = self.retriever.search(text, top_n=20)
            if hits:
                restriction = [hit.code for hit in hits]
                retrieved = [{"code": hit.code, "score": round(hit.score, 6)} for hit in hits]

        cache_key = DecisionCache.key(
            self.taxonomy.scheme,
            self.taxonomy.version,
            self.taxonomy.fingerprint,
            self.traverser.backend.cache_identity,
            self.traverser.prompt_fingerprint,
            text,
            restriction or [],
            self.traverser.passes,
        )
        cached = self.cache.get(cache_key) if self.cache else None
        if cached is not None:
            code = cached.get("code")
            agreement = cached.get("agreement")
            audit = cached.get("audit", {})
            traversal_flags = list(cached.get("flags", []))
        else:
            traversal = self.traverser.classify(text, restriction)
            code = traversal.code
            agreement = traversal.agreement
            traversal_flags = list(traversal.flags)
            audit = {
                "path": traversal.path,
                "reason": traversal.reason,
                "decisions": traversal.decisions,
                "restriction": restriction or [],
                "retrieval": retrieved,
            }
            if self.cache:
                self.cache.put(
                    cache_key,
                    {
                        "code": code,
                        "agreement": agreement,
                        "audit": audit,
                        "flags": traversal_flags,
                    },
                )

        # A cache row written by an older build may not carry an agreement value.
        agreement_value = float(agreement) if agreement is not None else 0.0

        if code is None:
            # Fall back to whatever the deterministic layer already established. Turning
            # the model on must never lose a code that crosswalk fusion could defend.
            if fusion.code is not None:
                return self._result(
                    canonical_id,
                    fusion.code,
                    self._status_for("FUSION_BACKOFF", fusion.code),
                    "FUSION",
                    candidate_codes=fusion.candidate_codes,
                    evidence_sources=fusion.evidence_sources,
                    flags=(
                        evidence_flags
                        + fusion.flags
                        + traversal_flags
                        + ["LLM_FELL_BACK_TO_FUSION"]
                    ),
                    traversal_agreement=agreement_value,
                    model=self.traverser.backend.model_name,
                    audit=audit,
                )
            return self._result(
                canonical_id,
                None,
                "REVIEW",
                "LLM",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=fusion.evidence_sources,
                flags=evidence_flags + fusion.flags + traversal_flags,
                traversal_agreement=agreement_value,
                model=self.traverser.backend.model_name,
                audit=audit,
            )

        base = "LLM_PARTIAL" if self.taxonomy.has_children(code) else "LLM_FULL"
        return self._result(
            canonical_id,
            code,
            self._status_for(base, code),
            "LLM",
            candidate_codes=fusion.candidate_codes,
            evidence_sources=fusion.evidence_sources,
            flags=evidence_flags + fusion.flags + traversal_flags,
            traversal_agreement=agreement_value,
            model=self.traverser.backend.model_name,
            audit=audit,
        )
