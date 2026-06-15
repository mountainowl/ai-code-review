"""Governance / provenance configuration (governance Recs ②/③).

Parses the ``[governance]`` block of ``config/env.toml`` into a typed,
immutable view. Everything is **off by default** — governance capture is
opt-in and, in this first phase, changes no review behavior: it only records
per-change provenance signals for audit.

Kept in its own module (mirroring :mod:`bubo.telemetry.config`) because the
governance surface grows across phases (rigor modulation, policy gates,
reporting). Imports only :mod:`bubo.config_values` to stay free of cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bubo.config_values import bool_value, lower_string_list, one_of, section, string_list
from bubo.governance_policy import POLICY_OFF, POLICY_REPORT_ONLY, POLICY_SOFT
from bubo.provenance import BAND_COLLABORATIVE, BAND_LIKELY_AI

# Policy modes accepted by ``[governance].policy_mode`` (validated on parse).
POLICY_MODES = (POLICY_OFF, POLICY_REPORT_ONLY, POLICY_SOFT)

# Default bands that escalate (trigger rigor modulation / a policy flag). The
# two AI-implicated bands; ``unknown`` deliberately never escalates.
DEFAULT_ESCALATE_BANDS = (BAND_LIKELY_AI, BAND_COLLABORATIVE)

# Default commit-trailer patterns that DECLARE AI assistance. Case-insensitive
# regexes matched against individual commit-message lines (see
# :func:`bubo.provenance.parse_ai_trailers`). ``Co-authored-by`` counts only
# when the value names a known agent — humans pair-program and co-author too —
# while explicit ``Generated-by`` / ``AI-assisted`` style trailers count on
# their own. Operators override the whole list via ``ai_trailer_patterns``.
DEFAULT_AI_TRAILER_PATTERNS: tuple[str, ...] = (
    r"^\s*co-authored-by\s*:.*\b("
    r"claude|copilot|chatgpt|gpt|openai|codex|gemini|cursor|devin|anthropic|llm"
    r")\b",
    r"^\s*(?:assisted|generated|written)-by\s*:.*\b("
    r"claude|copilot|chatgpt|gpt|openai|codex|gemini|cursor|devin|anthropic|llm|ai"
    r")\b",
    # Standalone marker line — anchored to end-of-line (optionally ``: value``)
    # so a prose body like "AI-generated code was reviewed" is NOT a declaration.
    r"^\s*(?:ai-assisted|ai-generated|generated-by-ai)\b(?:\s*[:=].*)?$",
)


@dataclass(frozen=True, slots=True)
class GovernanceConfig:
    """Immutable view of ``[governance]``. All-off defaults.

    Attributes
    ----------
    capture_provenance:
        Master switch for Phase 1. When ``False`` (default) no commit/diff
        metadata is fetched and no provenance is recorded — zero behavior
        change and zero extra API calls.
    ai_trailer_patterns:
        Regexes (case-insensitive) matched against commit-message lines to
        detect *declared* AI assistance. Defaults to
        :data:`DEFAULT_AI_TRAILER_PATTERNS`.
    sensitive_path_globs:
        ``fnmatch`` globs (case-preserving) flagged on a change's changed
        paths — e.g. ``payments/**``, ``*.pem``. Empty (default) = off; each
        org sets its own. Recorded for audit and used as the sensitive-path
        half of the escalation predicate (Phase 2).
    rigor_modulation:
        Phase 2 capability 1. When ``True``, an escalated change (see
        ``escalate_bands`` / ``rigor_require_sensitive``) injects a
        heightened-scrutiny directive into the per-change review prompt.
        **Off by default.** Advisory only — it adds prompt context, never a
        verdict, and never blocks a merge.
    escalate_bands:
        Provenance bands that escalate (shared by rigor modulation and the
        policy gate). Defaults to ``likely_ai`` + ``collaborative``;
        ``unknown`` never escalates.
    rigor_require_sensitive:
        When ``True`` (default) a change escalates only if it ALSO touches a
        ``sensitive_path_globs`` path; when ``False`` an escalating band alone
        suffices.
    policy_mode:
        Phase 2 capability 2: ``off`` (default) / ``report-only`` / ``soft``.
        When not ``off``, an auditable governance decision is recorded per
        change. All modes are advisory — bubo cannot block a merge — so
        ``soft`` differs from ``report-only`` only by also allowing rigor
        injection to be reflected in the decision. (No ``enforce`` mode
        exists: bubo does not own CI/branch protection.)
    """

    capture_provenance: bool = False
    ai_trailer_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_AI_TRAILER_PATTERNS)
    )
    sensitive_path_globs: list[str] = field(default_factory=list)
    rigor_modulation: bool = False
    escalate_bands: list[str] = field(default_factory=lambda: list(DEFAULT_ESCALATE_BANDS))
    rigor_require_sensitive: bool = True
    policy_mode: str = POLICY_OFF

    @property
    def enabled(self) -> bool:
        """True if any governance capability needs the per-change commit fetch.

        The poller gates the commit/diff fetch on this, so enabling rigor
        modulation or a policy mode auto-implies provenance computation even
        when ``capture_provenance`` is left ``False`` (a reasonable config, not
        a mistake — the fetch is what all three capabilities depend on).
        """
        return (
            self.capture_provenance
            or self.rigor_modulation
            or self.policy_mode != POLICY_OFF
        )


def governance_config_from_dict(raw: dict[str, Any]) -> GovernanceConfig:
    """Parse a loaded TOML mapping's ``[governance]`` block into a config."""
    governance = section(raw, "governance")
    patterns = governance.get("ai_trailer_patterns")
    return GovernanceConfig(
        capture_provenance=bool_value(
            governance.get("capture_provenance"),
            "capture_provenance",
            default=False,
        ),
        ai_trailer_patterns=(
            list(DEFAULT_AI_TRAILER_PATTERNS)
            if patterns is None
            else string_list(patterns, "ai_trailer_patterns")
        ),
        sensitive_path_globs=string_list(
            governance.get("sensitive_path_globs", []),
            "sensitive_path_globs",
        ),
        rigor_modulation=bool_value(
            governance.get("rigor_modulation"), "rigor_modulation", default=False
        ),
        escalate_bands=(
            list(DEFAULT_ESCALATE_BANDS)
            if governance.get("escalate_bands") is None
            else lower_string_list(governance.get("escalate_bands"), "escalate_bands")
        ),
        rigor_require_sensitive=bool_value(
            governance.get("rigor_require_sensitive"),
            "rigor_require_sensitive",
            default=True,
        ),
        policy_mode=one_of(
            governance.get("policy_mode"), "policy_mode", POLICY_MODES, default=POLICY_OFF
        ),
    )


__all__ = [
    "DEFAULT_AI_TRAILER_PATTERNS",
    "DEFAULT_ESCALATE_BANDS",
    "POLICY_MODES",
    "GovernanceConfig",
    "governance_config_from_dict",
]
