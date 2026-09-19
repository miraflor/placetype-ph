from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd

# The cache version changes whenever suggestion semantics change in a way that would make
# an older automatic crosswalk unsafe to reuse. V9.2c caps mixed source categories at their
# epistemic floor and backs immediate PSIC sibling disagreements off exactly one level.
EXPRESS_CACHE_VERSION = "joint-v9.2c-sibling-backoff"

# Files at or below this size are keyed by content. Larger inputs keep the cheap
# size/mtime identity, because hashing a full canonical_pois.parquet on every run costs
# more than the occasional unnecessary recomputation.
HASH_BYTE_LIMIT = 8 * 1024 * 1024

# Every tuning value express passes to the underlying commands. They are named here, and
# folded into the cache key, so that changing one invalidates previous work on its own
# rather than requiring EXPRESS_CACHE_VERSION to be bumped by hand.
SUGGEST_PARAMS: dict[str, object] = {
    "top_n": 5,
    "min_score": 0.45,
    "min_margin": 0.12,
    "recheck": True,
    "batch_size": 256,
}
CLASSIFY_PARAMS: dict[str, object] = {
    "llm": "none",
    "passes": 3,
    "fail_on_ambiguous_crosswalk": False,
}

# Recheck outcomes, from joint_crosswalk.recheck_joint_worklist. The distinction that
# matters for automatic promotion is not "did it say SHIFT" but "did it reach a verdict
# at all". NO_PEERS, NO_CONTROL_HIT, NO_RECHECK_HIT and MISSING_TAXONOMY all mean the
# comparison never ran, which is not evidence that the suggestion is sound. COARSENED is a
# cleared PCPC result only when promotion also receives its hierarchy-derived resolution code.
RECHECK_CLEARED = frozenset({"STABLE", "COARSENED"})
RECHECK_CONTRADICTED = frozenset({"SHIFT"})
# The comparison ran and its result does not support the suggestion, but it is not
# decisive: SHIFT_WEAK moved by less than min_margin. Candidate-only statuses describe
# rows with no accepted first-pass code, so they stay outside the promotion-policy
# verdict and fall through to NO_VERDICT.
RECHECK_INCONCLUSIVE = frozenset({"SHIFT_WEAK"})

# Prefer already-downloaded PSA sources before making another network request.
# The second name in each tuple is the name used by `taxonomy fetch`; the first is the
# source file currently retained in this repository.
LOCAL_TAXONOMY_SOURCES: dict[tuple[str, str], tuple[str, ...]] = {
    ("psic", "rev5"): (
        "PSIC_Revision_5_Detailed_Structure_30July2026.xlsx",
        "psic_rev5.xlsx",
    ),
    ("pcpc", "2002"): (
        "2002_PCPC.xlsx",
        "pcpc_2002.xlsx",
    ),
    ("pscc", "2022"): (
        "2022_PSCC.xlsx",
        "pscc_2022.xlsx",
    ),
}

EXPRESS_SUGGESTION_COLUMNS = (
    "mapping_kind",
    "codes",
    "suggested_kind",
    "suggested_codes",
    "recheck_status",
)
EXPRESS_AUTOMATIC_COLUMNS = (*EXPRESS_SUGGESTION_COLUMNS, "express_status")


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` through a temporary file in the same directory.

    An express run is resumable, so a half-written file left behind by an interrupt would
    be indistinguishable from a finished one on the next run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_frame(frame: pd.DataFrame, path: Path) -> None:
    """Write a work CSV atomically."""
    atomic_write_text(path, frame.to_csv(index=False))


def file_state(path: Path) -> str:
    """Cache identity for a local input or reference file.

    Small files are keyed by content, so copying the repository, cloning it, or checking
    it out again does not invalidate an otherwise valid run, and so an edit that happens
    to preserve the file size cannot be missed. Large files keep the cheap identity but
    are named by their own directory and file name rather than by an absolute path, for
    the same reason.
    """
    if not path.exists():
        return f"missing:{path.name}"
    stat = path.stat()
    if stat.st_size <= HASH_BYTE_LIMIT:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        return f"content:{path.name}:{digest}"
    return f"stat:{path.parent.name}/{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


def pipeline_state() -> str:
    """Stable description of every tuning value express passes downstream."""
    return "pipeline:" + json.dumps(
        {"suggest": SUGGEST_PARAMS, "classify": CLASSIFY_PARAMS}, sort_keys=True
    )


def input_cache_key(input_path: Path, states: Iterable[str]) -> str:
    """Key an express run to the input and every semantic dependency."""
    payload = "|".join([EXPRESS_CACHE_VERSION, file_state(input_path), *states])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def default_output_path(input_path: Path) -> Path:
    """Choose an output in PlaceType, never beside the upstream OpenPlaces file."""
    scope = (
        input_path.parent.name
        if input_path.name == "canonical_pois.parquet"
        else input_path.stem
    )
    return Path("output") / f"{scope}_placetype.parquet"


def find_local_taxonomy_source(
    reference_dir: Path, scheme: str, version: str
) -> Path | None:
    """Return an already-downloaded source workbook for a missing taxonomy."""
    for name in LOCAL_TAXONOMY_SOURCES.get((scheme, version), ()):
        path = reference_dir / "downloads" / name
        if path.is_file():
            return path
    return None


def csv_ready(path: Path, required: Iterable[str]) -> bool:
    """A work CSV is reusable only when it parses and carries the columns it must have.

    Existence alone is not enough: an interrupted write leaves a file that exists. A full
    parse rejects a corrupt file, but not a truncated one, because pandas pads a short
    final row rather than raising. The missing terminator catches that case.
    """
    if not path.is_file():
        return False
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    if not raw.endswith(b"\n"):
        return False
    try:
        frame = pd.read_csv(path, dtype=str)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return False
    return set(required) <= set(frame.columns)


def crosswalk_has_mappings(path: Path) -> bool:
    """Whether an automatic crosswalk carries at least one operative mapping.

    Checked on the reuse path too: a crosswalk that promoted nothing is a valid file, and
    classifying against it would quietly produce an empty layer.
    """
    try:
        frame = pd.read_csv(path, dtype=str, usecols=["mapping_kind"]).fillna("")
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
    ):
        return False
    return bool(frame["mapping_kind"].astype(str).str.strip().ne("").any())


def reference_is_joint_format(reference_crosswalk: Path) -> bool:
    """Whether a reviewed crosswalk has the minimum columns of the joint worklist."""
    if not reference_crosswalk.is_file():
        return False
    try:
        header = pd.read_csv(reference_crosswalk, nrows=0)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError):
        return False
    required = {"source", "source_value", "scheme", "version"}
    return required <= set(header.columns)


def seed_joint_worklist(
    reference_crosswalk: Path,
    worklist: Path,
    *,
    schemes: set[str] | None = None,
) -> bool:
    """Seed temporary express work from a reviewed joint crosswalk once.

    When Express is run on a subset of schemes, filter the seed so reviewed rows for an
    unrequested taxonomy cannot make that taxonomy reappear during suggestion loading.

    Returns False without writing when there is nothing usable to seed: the worklist
    already exists, the reference is absent, the reference predates the joint format, or
    the filter leaves no rows. In each of those cases the caller falls back to the legacy
    adoption path inside ``crosswalk-init``, which is the behaviour those cases want.
    """
    if worklist.exists() or not reference_is_joint_format(reference_crosswalk):
        return False
    if schemes is None:
        worklist.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(worklist, reference_crosswalk.read_text(encoding="utf-8"))
        return True

    frame = pd.read_csv(reference_crosswalk, dtype=str).fillna("")
    if "scheme" not in frame.columns:
        return False
    wanted = {scheme.casefold() for scheme in schemes}
    frame = frame[frame["scheme"].astype(str).str.casefold().isin(wanted)]
    if frame.empty:
        return False
    write_frame(frame, worklist)
    return True


def recheck_verdict(status: object) -> str:
    """Classify one recheck outcome as CLEARED, CONTRADICTED, INCONCLUSIVE, or NO_VERDICT.

    STABLE and hierarchy-safe COARSENED outcomes clear; SHIFT contradicts. SHIFT_WEAK is
    INCONCLUSIVE: the
    comparison ran and pointed away, but by less than min_margin. Candidate-only statuses
    describe rows without accepted first-pass codes, so they return NO_VERDICT for the
    promotion policy. Unknown statuses also return NO_VERDICT. Keeping absent and weak
    evidence apart matters because only weak evidence points away from the suggestion;
    candidate diagnostics do not participate in the automatic-promotion verdict.
    """
    value = str(status or "").strip().upper()
    if value in RECHECK_CLEARED:
        return "CLEARED"
    if value in RECHECK_CONTRADICTED:
        return "CONTRADICTED"
    if value in RECHECK_INCONCLUSIVE:
        return "INCONCLUSIVE"
    return "NO_VERDICT"


def suggestion_is_clearable(suggested_codes: object) -> bool:
    """Whether the peer recheck could ever clear this suggestion.

    The comparison runs over first-pass codes. A suggestion that assigns no code, such as
    NOT_ACTIVITY, therefore never reaches STABLE, however many peers exist.
    """
    return bool(str(suggested_codes or "").strip())


def promote_auto_suggestions(
    input_path: Path,
    output_path: Path,
    *,
    promote_unchecked: bool = True,
    hold_inconclusive: bool = False,
) -> dict[str, int]:
    """Turn defensible suggestions into operative mappings for express mode only.

    Human-reviewed mappings are preserved verbatim. An unreviewed row is promoted only
    when the suggestion engine emitted an actual mapping kind and the peer recheck did
    not contradict it. Candidate-only rows remain unresolved. The sole replacement
    allowed by recheck is a conservative PCPC sibling backoff to their common parent.

    A recheck that could not run is not a recheck that approved. Those rows are reported
    separately and, when ``promote_unchecked`` is False, held rather than promoted.
    """
    frame = pd.read_csv(input_path, dtype=str).fillna("")
    missing = set(EXPRESS_SUGGESTION_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"suggestion file is missing columns {sorted(missing)}")

    for column in ("express_status", "express_recheck", "confidence", "notes"):
        if column not in frame.columns:
            frame[column] = ""

    counts = {
        "reviewed": 0,
        "promoted_cleared": 0,
        "promoted_inconclusive": 0,
        "promoted_unchecked": 0,
        "held_recheck": 0,
        "held_inconclusive": 0,
        "held_no_verdict": 0,
        "unresolved": 0,
        "without_code": 0,
        "held_unclearable": 0,
    }

    for idx, row in frame.iterrows():
        if str(row.get("mapping_kind", "")).strip():
            frame.at[idx, "express_status"] = "REVIEWED"
            counts["reviewed"] += 1
            continue

        kind = str(row.get("suggested_kind", "")).strip()
        if not kind:
            frame.at[idx, "express_status"] = "UNRESOLVED"
            counts["unresolved"] += 1
            continue

        recheck_status = str(row.get("recheck_status", "")).strip().upper()
        verdict = recheck_verdict(recheck_status)
        codes = str(row.get("suggested_codes", "")).strip()
        resolution = ""
        invalid_coarsened = False
        if recheck_status == "COARSENED":
            resolution = str(row.get("recheck_resolution_code", "")).strip()
            if resolution:
                codes = resolution
                kind = "SUBTREE"
            else:
                invalid_coarsened = True
                verdict = "NO_VERDICT"
        frame.at[idx, "express_recheck"] = verdict

        held: str | None = None
        if invalid_coarsened:
            held = "HELD_NO_VERDICT"
        elif verdict == "CONTRADICTED":
            held = "HELD_RECHECK"
        elif verdict == "INCONCLUSIVE" and (hold_inconclusive or not promote_unchecked):
            held = "HELD_INCONCLUSIVE"
        elif verdict == "NO_VERDICT" and not promote_unchecked:
            held = "HELD_NO_VERDICT"

        if held is not None:
            frame.at[idx, "express_status"] = held
            counts[
                {
                    "HELD_RECHECK": "held_recheck",
                    "HELD_INCONCLUSIVE": "held_inconclusive",
                    "HELD_NO_VERDICT": "held_no_verdict",
                }[held]
            ] += 1
            if not suggestion_is_clearable(codes):
                counts["held_unclearable"] += 1
            continue

        status = {
            "CLEARED": "AUTO_ACCEPTED",
            "INCONCLUSIVE": "AUTO_ACCEPTED_INCONCLUSIVE",
            "NO_VERDICT": "AUTO_ACCEPTED_UNCHECKED",
        }[verdict]
        frame.at[idx, "mapping_kind"] = kind
        frame.at[idx, "codes"] = codes
        frame.at[idx, "express_status"] = status
        counts[
            {
                "CLEARED": "promoted_cleared",
                "INCONCLUSIVE": "promoted_inconclusive",
                "NO_VERDICT": "promoted_unchecked",
            }[verdict]
        ] += 1
        if not codes:
            counts["without_code"] += 1

        if not str(row.get("confidence", "")).strip():
            score = str(row.get("retrieval_score", "")).strip()
            if score:
                frame.at[idx, "confidence"] = score

        provenance = str(row.get("suggestion_source", "")).strip()
        if recheck_status == "COARSENED" and resolution:
            provenance = (
                f"{provenance}; recheck:coarsened_to={resolution}"
                if provenance
                else f"recheck:coarsened_to={resolution}"
            )
        note = f"{frame.at[idx, 'express_status']} by placetype express"
        if provenance:
            note += f"; {provenance}"
        existing = str(row.get("notes", "")).strip()
        frame.at[idx, "notes"] = f"{existing}; {note}" if existing else note

    write_frame(frame, output_path)
    return counts


def summarize_automatic_crosswalk(path: Path) -> dict[str, int]:
    """Reconstruct the Express evidence summary from a fresh or reused auto-crosswalk."""
    frame = pd.read_csv(path, dtype=str).fillna("")
    if "express_status" not in frame.columns:
        raise ValueError(f"automatic crosswalk {path} is missing 'express_status'")
    status = frame["express_status"].astype(str).str.strip()
    promoted = status.isin(
        {"AUTO_ACCEPTED", "AUTO_ACCEPTED_INCONCLUSIVE", "AUTO_ACCEPTED_UNCHECKED"}
    )
    held = status.isin({"HELD_RECHECK", "HELD_INCONCLUSIVE", "HELD_NO_VERDICT"})

    def _column(name: str) -> pd.Series:
        if name in frame.columns:
            return frame[name].astype(str).str.strip()
        return pd.Series("", index=frame.index, dtype=str)

    codes = _column("codes")
    suggested_codes = _column("suggested_codes")
    return {
        "reviewed": int(status.eq("REVIEWED").sum()),
        "promoted_cleared": int(status.eq("AUTO_ACCEPTED").sum()),
        "promoted_inconclusive": int(status.eq("AUTO_ACCEPTED_INCONCLUSIVE").sum()),
        "promoted_unchecked": int(status.eq("AUTO_ACCEPTED_UNCHECKED").sum()),
        "held_recheck": int(status.eq("HELD_RECHECK").sum()),
        "held_inconclusive": int(status.eq("HELD_INCONCLUSIVE").sum()),
        "held_no_verdict": int(status.eq("HELD_NO_VERDICT").sum()),
        "unresolved": int(status.eq("UNRESOLVED").sum()),
        "without_code": int((promoted & codes.eq("")).sum()),
        "held_unclearable": int((held & suggested_codes.eq("")).sum()),
    }


def promoted_total(counts: Mapping[str, int]) -> int:
    """Rows this run turned into operative mappings."""
    return sum(
        int(counts.get(name, 0))
        for name in ("promoted_cleared", "promoted_inconclusive", "promoted_unchecked")
    )


def classification_run_ready(run_dir: Path) -> bool:
    """A resumable run is ready only when its manifest-named outputs exist."""
    manifest_path = run_dir / "run.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict):
        return False
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        return False
    names = (
        outputs.get("entity_classifications"),
        outputs.get("classified_pois"),
    )
    return all(isinstance(name, str) and (run_dir / name).is_file() for name in names)
