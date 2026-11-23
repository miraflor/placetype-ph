"""Additive classification-evidence pipeline with strict non-promotion by default."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache

from .activity import activity_proposals, load_rules
from .fusion import AncestorLookup, fuse_activity
from .institutions import administrative, ownership
from .model import Assessment
from .nonactivity import eligibility
from .registry import RegistryRecord, match_registry
from .source_evidence import text

POLICY_ID = "place-evidence-v2-source-fusion"


@lru_cache(maxsize=1)
def _rules():
    return load_rules()


def fingerprint() -> str:
    from importlib import resources

    rule_bytes = (
        resources.files("placetype_ph.specialized").joinpath("activity_rules.csv").read_bytes()
    )
    return hashlib.sha256(POLICY_ID.encode() + b"\0" + rule_bytes).hexdigest()


def assess(
    row: Mapping[str, object],
    *,
    psic_code: str = "",
    psic_method: str = "",
    registry: tuple[RegistryRecord, ...] = (),
    ancestors: AncestorLookup | None = None,
) -> Assessment:
    """Audit source evidence without modifying active PSIC/PCPC/PSCC decisions.

    A caller with the official PSIC taxonomy may supply its ancestors(code)
    method. Without that, numeric code-prefix nesting is provisional and
    mixed alphabetic/numeric ancestry is never assumed.
    """
    r = dict(row)
    original_code = text(psic_code)
    method = text(psic_method)
    proposals, source_flags = activity_proposals(r, _rules())
    fusion = fuse_activity(
        proposals, original_code=original_code, original_method=method, ancestors=ancestors
    )
    candidate_codes, status, reason = (fusion.candidate_codes, fusion.status, fusion.reason)
    flags = set(source_flags) | set(fusion.flags)
    if any(flag.startswith("COMPOUND_CONFLICT") for flag in source_flags):
        status = "REVIEW"
        reason = "incompatible_compound_source_tags"
    nonactivity, nonactivity_reason = eligibility(r)
    if nonactivity:
        if proposals:
            flags.add("ACTIVITY_NONACTIVITY_CONFLICT")
            status = "REVIEW"
            reason = "positive_activity_and_negative_source_conflict"
        elif status == "NO_EVIDENCE":
            status = nonactivity
            reason = nonactivity_reason
        else:
            flags.add("NONACTIVITY_EVIDENCE_REVIEW")
            status = "REVIEW"
            reason = nonactivity_reason
    admin = administrative(r)
    if admin.status == "PROPOSED":
        # Proposals are additive. Administrative evidence must not clear a
        # conflict detected earlier in the same entity.
        candidate_codes = tuple(sorted(set(candidate_codes) | {admin.code}))
        if status == "REVIEW":
            flags.add("ADMINISTRATIVE_WITH_UNRESOLVED_EVIDENCE")
        elif status in {"NOT_PSIC_ACTIVITY", "UNCODEABLE"}:
            status = "REVIEW"
            flags.add("ADMINISTRATIVE_NONACTIVITY_CONFLICT")
            reason = "administrative_function_and_nonactivity_disagreement"
        elif (
            original_code
            and original_code != admin.code
            and method.casefold() in {"manual", "reviewed", "reviewed_crosswalk", "user_supplied"}
        ):
            status = "REVIEW"
            reason = "reviewed_classification_requires_review"
        else:
            status = "ADMINISTRATIVE_CANDIDATE"
            reason = "specific_government_administrative_function"
    elif admin.status == "REVIEW":
        flags.add("ADMINISTRATIVE_FUNCTION_REVIEW")
    flag_tuple = tuple(sorted(flags))
    role_codes = tuple(sorted(set(candidate_codes) | ({original_code} if original_code else set())))
    owner = ownership(r, role_codes)
    domain = (
        "education"
        if any(c.startswith("85") for c in role_codes if c.isdigit())
        else "health"
        if any(c.startswith("86") for c in role_codes if c.isdigit())
        else ""
    )
    reg = match_registry(r, registry, domain=domain) if domain else match_registry(r, (), domain="")
    ownership_side = owner.side
    ownership_status = owner.status
    ownership_reason = owner.reason
    if reg.status == "EXTERNAL_ID_SITE_MATCH":
        if owner.side and owner.side != reg.side:
            ownership_status = "REVIEW"
            ownership_reason = "registry_vs_heuristic_conflict"
            ownership_side = ""
        else:
            ownership_status = "EXTERNAL_ID_SITE_EVIDENCE"
            ownership_reason = reg.basis
            ownership_side = reg.side
    elif reg.status.endswith("CONFLICT") or reg.status in {
        "IDENTITY_AMBIGUOUS",
        "EXTERNAL_ID_LOCALITY_REVIEW",
    }:
        ownership_status = "REVIEW"
        ownership_side = ""
        ownership_reason = "registry_identity_unresolved"
    audit = {
        "policy": POLICY_ID,
        "original_psic_code": original_code,
        "original_psic_method": method,
        "candidates": list(candidate_codes),
        "evidence": [item.as_record() for item in proposals],
        "source_flags": list(source_flags),
        "compound_flags": list(flag_tuple),
        "eligibility_status": nonactivity,
        "fusion": {
            "status": fusion.status,
            "reason": fusion.reason,
            "groups": list(fusion.groups),
            "hierarchy_basis": fusion.hierarchy_basis,
            "flags": list(fusion.flags),
            "automatic_promotion": False,
        },
        "administration": admin.__dict__,
        "ownership_heuristic": owner.__dict__,
        "registry": reg.__dict__,
    }
    return Assessment(
        candidate_codes=candidate_codes,
        status=status,
        reason=reason,
        evidence=proposals,
        flags=flag_tuple,
        administrative_status=admin.status,
        administrative_code=admin.code,
        administrative_reason=admin.reason,
        ownership_candidate=ownership_side,
        ownership_status=ownership_status,
        ownership_reason=ownership_reason,
        registry_status=reg.status,
        registry_reference=reg.reference,
        registry_basis=reg.basis,
        audit=audit,
    )


def as_columns(assessment: Assessment) -> dict[str, object]:
    return {
        "specialized_status": assessment.status,
        "specialized_candidate_codes": json.dumps(
            list(assessment.candidate_codes), separators=(",", ":")
        ),
        "specialized_reason": assessment.reason,
        "specialized_flags": json.dumps(list(assessment.flags), separators=(",", ":")),
        "administrative_status": assessment.administrative_status,
        "administrative_candidate_code": assessment.administrative_code,
        "administrative_reason": assessment.administrative_reason,
        "ownership_candidate": assessment.ownership_candidate,
        "ownership_status": assessment.ownership_status,
        "ownership_reason": assessment.ownership_reason,
        "registry_status": assessment.registry_status,
        "registry_reference": assessment.registry_reference,
        "registry_basis": assessment.registry_basis,
        "specialized_audit": json.dumps(
            assessment.audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    }
