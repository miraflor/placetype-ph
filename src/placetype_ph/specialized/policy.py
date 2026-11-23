"""Additive classification-evidence pipeline with strict non-promotion by default."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from .activity import activity_proposals, load_rules, reconcile_activity
from .institutions import administrative, ownership
from .model import Assessment
from .nonactivity import eligibility
from .registry import RegistryRecord, match_registry
from .source_evidence import text

POLICY_ID = "place-evidence-v1"


@lru_cache(maxsize=1)
def _rules():
    return load_rules()


def fingerprint() -> str:
    from importlib import resources
    rule_bytes = resources.files("placetype_ph.specialized").joinpath("activity_rules.csv").read_bytes()
    return hashlib.sha256(POLICY_ID.encode() + b"\0" + rule_bytes).hexdigest()


def assess(row: Mapping[str, object], *, psic_code: str = "", psic_method: str = "",
           registry: tuple[RegistryRecord, ...] = ()) -> Assessment:
    """Evaluate evidence without changing any reviewed or other existing classification."""
    r = dict(row)
    original_code = text(psic_code)
    proposals, flags = activity_proposals(r, _rules())
    candidate_codes, status, reason = reconcile_activity(proposals, flags, original_code, text(psic_method))
    nonactivity, nonactivity_reason = eligibility(r)
    if nonactivity and status == "NO_EVIDENCE":
        status = nonactivity
        reason = nonactivity_reason
    elif nonactivity and status not in {"NO_EVIDENCE", "BROAD_CANDIDATE"}:
        flags = tuple(sorted(set(flags) | {"ACTIVITY_NONACTIVITY_CONFLICT"}))
        status = "REVIEW"
        reason = "positive_activity_and_negative_source_conflict"
    admin = administrative(r)
    if admin.status == "PROPOSED":
        # Administrative function and frontline service have been separated first.
        candidate_codes = tuple(sorted(set(candidate_codes) | {admin.code}))
        if original_code and original_code != admin.code:
            status = "REVIEW" if text(psic_method).casefold() in {
                "manual", "reviewed", "reviewed_crosswalk", "user_supplied"} else "ADMINISTRATIVE_CANDIDATE"
        else:
            status = "ADMINISTRATIVE_CANDIDATE"
        reason = "specific_government_administrative_function"
    elif admin.status == "REVIEW":
        flags = tuple(sorted(set(flags) | {"ADMINISTRATIVE_FUNCTION_REVIEW"}))
    role_codes = tuple(sorted(set(candidate_codes) | ({original_code} if original_code else set())))
    owner = ownership(r, role_codes)
    domain = "education" if any(c.startswith("85") for c in role_codes if c.isdigit()) else (
        "health" if any(c.startswith("86") for c in role_codes if c.isdigit()) else "")
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
    elif reg.status.endswith("CONFLICT") or reg.status in {"IDENTITY_AMBIGUOUS", "EXTERNAL_ID_LOCALITY_REVIEW"}:
        ownership_status = "REVIEW"
        ownership_side = ""
        ownership_reason = "registry_identity_unresolved"
    audit = {
        "policy": POLICY_ID,
        "original_psic_code": original_code,
        "original_psic_method": text(psic_method),
        "candidates": list(candidate_codes),
        "evidence": [item.as_record() for item in proposals],
        "compound_flags": list(flags),
        "eligibility_status": nonactivity,
        "administration": admin.__dict__,
        "ownership_heuristic": owner.__dict__,
        "registry": reg.__dict__,
    }
    return Assessment(candidate_codes=candidate_codes, status=status, reason=reason,
                      evidence=proposals, flags=flags,
                      administrative_status=admin.status, administrative_code=admin.code,
                      administrative_reason=admin.reason, ownership_candidate=ownership_side,
                      ownership_status=ownership_status, ownership_reason=ownership_reason,
                      registry_status=reg.status, registry_reference=reg.reference,
                      registry_basis=reg.basis, audit=audit)


def as_columns(assessment: Assessment) -> dict[str, object]:
    return {
        "specialized_status": assessment.status,
        "specialized_candidate_codes": json.dumps(list(assessment.candidate_codes), separators=(",", ":")),
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
        "specialized_audit": json.dumps(assessment.audit, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }
