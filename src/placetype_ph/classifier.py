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
    FusionResult,
    FusionStatus,
    MappingKind,
    SourceEvidence,
    SourceField,
)
from .openplaces import Row, entity_match_flags, evidence_text, source_evidence
from .retrieval import TaxonomyRetriever
from .reviewed_psic import (
    accepted_administrative_role,
    packaged_name_rules,
    psic_source_evidence,
    trusted_psic_source_rule,
    unsafe_osm_compound,
)
from .taxonomy import Taxonomy
from .traversal import HierarchicalTraverser

_RESOLVED = {FusionStatus.SINGLE, FusionStatus.NESTED, FusionStatus.INTERSECT}
_UNRESOLVED = {FusionStatus.EMPTY, FusionStatus.CONFLICT, FusionStatus.UNION}
# Optional OpenPlaces input column with a user-supplied PSIC code (status MANUAL).
EXPLICIT_PSIC_COLUMN = "explicit_psic_code"
_NOT_FUSED = {"CONFLICT", "REVIEW_ENTITY_MATCH"}


def _independent_activity_nonactivity_conflict(evidence: list[SourceEvidence]) -> bool:
    """Whether independent sources disagree on activity eligibility.

    A coded activity and a high-precision NOT_ACTIVITY assertion from different
    dependency groups are substantive counter-evidence. Treating the coded source as
    automatically dominant creates false positives such as a residential subdivision
    classified as lodging.
    """
    coded = {MappingKind.EXACT, MappingKind.SUBTREE, MappingKind.UNION}
    coded_groups = {
        item.dependency_group
        for item in evidence
        if item.mapping is not None and item.mapping.mapping_kind in coded
    }
    nonactivity_groups = {
        item.dependency_group
        for item in evidence
        if item.mapping is not None and item.mapping.mapping_kind == MappingKind.NOT_ACTIVITY
    }
    return any(left != right for left in coded_groups for right in nonactivity_groups)


class EntityClassifier:
    def __init__(
        self,
        taxonomy: Taxonomy,
        crosswalk: Crosswalk | None = None,
        traverser: HierarchicalTraverser | None = None,
        cache: DecisionCache | None = None,
        reviewed_psic_policy: bool = True,
    ):
        self.taxonomy = taxonomy
        self.crosswalk = crosswalk
        self.traverser = traverser
        self.cache = cache
        # Enables the accepted Rev. 5 semantic policy, independently from user crosswalks.
        self.operational_reviewed_psic = (
            reviewed_psic_policy and taxonomy.scheme == "psic" and taxonomy.version == "rev5"
        )
        self.retriever = TaxonomyRetriever(taxonomy) if traverser is not None else None
        # Packaged keyword name rules yield to a disjoint category; user name rules do not.
        self._packaged_name_rules = (
            packaged_name_rules() if self.operational_reviewed_psic else frozenset()
        )
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
        if name_entry in self._packaged_name_rules:
            # A packaged rule is a keyword pattern ("college", "hotel"), not a judgment
            # about this establishment; the reviewed category is the more direct statement.
            # Example: "University of Santo Tomas Hospital" tagged amenity=hospital is 861.
            flags.append("CROSSWALK_CATEGORY_RULE_OVERRULED_NAME")
            return category
        flags.append("CROSSWALK_NAME_RULE_OVERRODE_CATEGORY")
        return name

    def _mapped_evidence(self, row: Row) -> tuple[list[SourceEvidence], list[str]]:
        out: list[SourceEvidence] = []
        flags: list[str] = []
        row_had_ambiguity = False
        if self.operational_reviewed_psic:
            evidence_items = psic_source_evidence(row, keep_whole=self._keep_whole)
        elif self.taxonomy.scheme == "psic":
            # Legacy comparison: only source_name/source_category, not structured
            # Overture labels introduced by the reviewed Rev. 5 integration.
            evidence_items = source_evidence(row, include_structured_overture=False)
        else:
            evidence_items = source_evidence(row)
        for e in evidence_items:
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
                floor_rule = None
                if (
                    self.operational_reviewed_psic
                    and e.category
                    and all(rule.source_field == SourceField.NAME for rule in entries)
                ):
                    # A reviewed name-only mapping need not suppress a curated,
                    # potentially contradictory category floor. Compatible floors
                    # refine; incompatible evidence requests review, except that a
                    # packaged keyword name rule yields to the floor it contradicts.
                    candidate_floor = trusted_psic_source_rule(
                        self.taxonomy, e.source, e.category
                    )
                    if candidate_floor is not None and candidate_floor.codes:
                        floor_rule = candidate_floor
                        name_roots = [
                            root for rule in entries for root in entry_roots(self.taxonomy, rule)
                        ]
                        if (
                            name_roots
                            and all(rule in self._packaged_name_rules for rule in entries)
                            and not intersect_subtrees(
                                self.taxonomy,
                                entry_roots(self.taxonomy, candidate_floor),
                                name_roots,
                            )
                        ):
                            entries = []
                            flags.append("NAME_RULE_OVERRULED_BY_CATEGORY")
                out.extend(replace(e, mapping=mapping) for mapping in entries)
                if self.operational_reviewed_psic:
                    flags.append("ACCEPTED_CROSSWALK_RULE")
                    if floor_rule is not None:
                        out.append(replace(e, mapping=floor_rule))
                        flags.append("TRUSTED_CATEGORY_FLOOR")
            else:
                source_rule = (
                    trusted_psic_source_rule(self.taxonomy, e.source, e.category)
                    if self.operational_reviewed_psic
                    else None
                )
                if source_rule is None:
                    out.append(replace(e, mapping=None))
                else:
                    out.append(replace(e, mapping=source_rule))
                    flags.append(
                        "TRUSTED_SOURCE_FLOOR" if source_rule.codes else "SOURCE_TERMINAL_POLICY"
                    )
        if row_had_ambiguity:
            self.ambiguity_rows += 1
        return out, sorted(set(flags))

    def _keep_whole(self, source: str, raw: str) -> bool:
        """A multi-tag OSM string with its own reviewed row or terminal source policy."""
        if self.crosswalk is not None and self.crosswalk.has_exact_category(
            source, self.taxonomy.scheme, self.taxonomy.version, raw
        ):
            return True
        rule = trusted_psic_source_rule(self.taxonomy, source, raw)
        return rule is not None and not rule.codes

    def _explicit_code(self, row: Row) -> str | None:
        """User-supplied PSIC code. An unknown code stops the run instead of being ignored."""
        if self.taxonomy.scheme != "psic":
            return None
        value = row.get(EXPLICIT_PSIC_COLUMN)
        from .io_resolution import scalar_text

        code = scalar_text(value)
        if not code:
            return None
        if code not in self.taxonomy.nodes:
            raise ValueError(
                f"{EXPLICIT_PSIC_COLUMN} {code!r} for canonical_id {row.get('canonical_id')!r} "
                f"is not a {self.taxonomy.scheme} {self.taxonomy.version} code"
            )
        return code

    def _explicit_result(
        self,
        canonical_id: str,
        code: str,
        fusion: FusionResult,
        evidence_flags: list[str],
    ) -> ClassificationResult:
        """Supplied PSIC is authoritative; its agreement with source evidence is recorded."""
        flags = ["USER_SUPPLIED_PSIC"]
        if fusion.code is not None and fusion.code in self.taxonomy.nodes:
            roots = fusion.admissible_roots or [fusion.code]
            related = any(
                root == code
                or root in self.taxonomy.ancestors(code)
                or code in self.taxonomy.ancestors(root)
                for root in roots
            )
            flags.append(
                "EXPLICIT_PSIC_AGREES_WITH_SOURCES"
                if related
                else "EXPLICIT_PSIC_DIFFERS_FROM_SOURCES"
            )
        elif fusion.status == FusionStatus.EMPTY:
            flags.append("EXPLICIT_PSIC_NO_SOURCE_EVIDENCE")
        else:
            flags.append("EXPLICIT_PSIC_SOURCES_UNRESOLVED")
        return self._result(
            canonical_id,
            code,
            "MANUAL",
            "EXPLICIT_INPUT",
            candidate_codes=list(dict.fromkeys([code, *fusion.candidate_codes])),
            evidence_sources=fusion.evidence_sources,
            flags=evidence_flags + flags,
            audit={
                "source_fusion_status": fusion.status.value,
                "source_fusion_code": fusion.code,
                "source_admissible_roots": sorted(set(fusion.admissible_roots)),
            },
        )

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
        explicit = self._explicit_code(row)
        mapped, evidence_flags = self._mapped_evidence(row)
        fusion = fuse(self.taxonomy, mapped)
        if explicit is not None:
            eligibility = decide_eligibility(mapped)
            manual_flags = [*evidence_flags, *fusion.flags]
            if eligibility == Eligibility.NOT_ACTIVITY:
                manual_flags.append("MANUAL_OVERRIDES_NON_ACTIVITY")
            if self.operational_reviewed_psic and unsafe_osm_compound(row, mapped):
                manual_flags.append("MANUAL_OVERRIDES_COMPOUND_REVIEW")
            manual_flags.extend(entity_match_flags(row, fusion.status == FusionStatus.CONFLICT))
            return self._explicit_result(canonical_id, explicit, fusion, manual_flags)
        result = self._classify_fused(
            row, product_text, canonical_id, mapped, evidence_flags, fusion
        )
        if (
            len(fusion.admissible_roots) > 1
            and result.deciding_component == "FUSION"
            and result.code == fusion.code
            and result.status not in _NOT_FUSED
        ):
            # The code is the common parent of several admissible subtrees. The I-O step
            # maps the subtrees, not the parent, so the parent's other activities stay out.
            result.admissible_roots = sorted(set(fusion.admissible_roots))
            result.audit = {**result.audit, "admissible_roots": result.admissible_roots}
        return result

    def _classify_fused(
        self,
        row: Row,
        product_text: str | None,
        canonical_id: str,
        mapped: list[SourceEvidence],
        evidence_flags: list[str],
        fusion: FusionResult,
    ) -> ClassificationResult:
        eligibility = decide_eligibility(mapped)

        if self.taxonomy.scheme == "psic" and _independent_activity_nonactivity_conflict(mapped):
            match_flags = entity_match_flags(row, True)
            status = "REVIEW_ENTITY_MATCH" if match_flags else "CONFLICT"
            return self._result(
                canonical_id,
                None,
                status,
                "FUSION",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=[item.source for item in mapped if item.mapping is not None],
                flags=(
                    evidence_flags + fusion.flags + match_flags + ["ACTIVITY_NON_ACTIVITY_CONFLICT"]
                ),
            )

        # Multiple substantive OSM tags are not one activity unless every
        # primary assertion has the same accepted mapping.
        if self.operational_reviewed_psic and unsafe_osm_compound(row, mapped):
            match_flags = entity_match_flags(row, True)
            return self._result(
                canonical_id,
                None,
                "REVIEW_ENTITY_MATCH" if match_flags else "CONFLICT",
                "SOURCE_COMPOUND",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=fusion.evidence_sources,
                flags=evidence_flags + fusion.flags + match_flags + ["COMPOUND_OSM_MULTI_PRIMARY"],
            )

        # Dependent source assertions describing disjoint activities are not a
        # licence to select their LCA as one invented principal activity.
        if self.operational_reviewed_psic and any(
            flag.startswith("DEPENDENT_CONFLICT:") for flag in fusion.flags
        ):
            match_flags = entity_match_flags(row, True)
            return self._result(
                canonical_id,
                None,
                "REVIEW_ENTITY_MATCH" if match_flags else "CONFLICT",
                "FUSION",
                candidate_codes=fusion.candidate_codes,
                evidence_sources=fusion.evidence_sources,
                flags=evidence_flags + fusion.flags + match_flags,
            )

        if self.operational_reviewed_psic:
            # Preserve negative eligibility and reviewed exact/name decisions.
            admin_code, admin_reason = accepted_administrative_role(self.taxonomy, row, mapped)
            if admin_code is None and admin_reason in {
                "unrelated_activity_evidence",
                "exact_classification_preserved",
            }:
                match_flags = entity_match_flags(row, True)
                return self._result(
                    canonical_id,
                    None,
                    "REVIEW_ENTITY_MATCH" if match_flags else "CONFLICT",
                    "ADMINISTRATIVE_ROLE",
                    candidate_codes=list(dict.fromkeys(fusion.candidate_codes + ["84120"])),
                    evidence_sources=fusion.evidence_sources,
                    flags=evidence_flags
                    + fusion.flags
                    + match_flags
                    + ["ADMINISTRATIVE_ROLE_CONFLICT"],
                )
            if admin_code is not None and eligibility != Eligibility.NOT_ACTIVITY:
                return self._result(
                    canonical_id,
                    admin_code,
                    "SINGLE",
                    "ADMINISTRATIVE_ROLE",
                    candidate_codes=list(dict.fromkeys(fusion.candidate_codes + [admin_code])),
                    evidence_sources=fusion.evidence_sources,
                    flags=evidence_flags + fusion.flags + ["ADMINISTRATIVE_ROLE_ACCEPTED"],
                    audit={
                        "administrative_reason": admin_reason,
                        "original_fusion_status": fusion.status.value,
                        "original_fusion_code": fusion.code,
                    },
                )

            # UNCODEABLE means the source category is too broad to assign a
            # principal activity; it is distinct from NOT_PSIC_ACTIVITY.
            coded_kinds = {MappingKind.EXACT, MappingKind.SUBTREE, MappingKind.UNION}
            if (
                any(
                    e.mapping is not None and e.mapping.mapping_kind == MappingKind.UNCODEABLE
                    for e in mapped
                )
                and not any(
                    e.mapping is not None and e.mapping.mapping_kind in coded_kinds for e in mapped
                )
                and eligibility != Eligibility.NOT_ACTIVITY
            ):
                return self._result(
                    canonical_id,
                    None,
                    "UNCODEABLE",
                    "SOURCE_POLICY",
                    flags=evidence_flags + fusion.flags,
                )

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
