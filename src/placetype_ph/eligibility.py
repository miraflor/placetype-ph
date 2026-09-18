from __future__ import annotations

from .models import Eligibility, MappingKind, SourceEvidence


def decide_eligibility(evidence: list[SourceEvidence]) -> Eligibility:
    """Decide whether PSIC activity classification is semantically appropriate.

    `UNCODEABLE` is absence of usable evidence, not counter-evidence. It must therefore
    not cancel a substantive `NOT_ACTIVITY` judgment from another field/source. Coded
    activity evidence still wins the eligibility question; downstream fusion and entity
    matching decide how to handle disagreements about *which* activity.
    """
    mapped = [e.mapping for e in evidence if e.mapping is not None]
    informative = [m for m in mapped if m.mapping_kind != MappingKind.UNCODEABLE]
    if not informative:
        return Eligibility.UNCERTAIN

    coded = {MappingKind.EXACT, MappingKind.SUBTREE, MappingKind.UNION}
    if any(m.mapping_kind in coded for m in informative):
        return Eligibility.ELIGIBLE
    if all(m.mapping_kind == MappingKind.NOT_ACTIVITY for m in informative):
        return Eligibility.NOT_ACTIVITY
    return Eligibility.UNCERTAIN
