"""Auto-triage: reports become a patch proposal, never an auto-applied edit.

The proposal is a normal Item written through ``commit()``; a human must
``approve`` it, then it dispatches. Measurement compares the next runs on
``measurement_min_runs`` and the doctor reports a regression so the fix can
be rolled back via ``ops rollback``.
"""

from __future__ import annotations

from typing import Any

from context_rail.kernel.measurement import ReportSample

__all__ = ["PatchProposal", "propose_patch"]


class PatchProposal:
    """One proposed skill/profile patch with its evidence and measurement plan."""

    def __init__(
        self,
        *,
        target_class: str,
        target: str,
        summary: str,
        evidence: list[str],
        measurement_runs: int = 10,
    ) -> None:
        self.target_class = target_class
        self.target = target
        self.summary = summary
        self.evidence = evidence
        self.measurement_runs = measurement_runs


def propose_patch(
    reports: list[ReportSample | dict[str, Any]],
    *,
    target_class: str = "skill",
    target: str = "",
    measurement_min_runs: int = 10,
) -> PatchProposal:
    """Cluster reports into one proposal; never invents a fix.

    ``reports`` may be samples or plain dicts with ``report_id``/``fingerprint``.
    The proposal names its evidence (report ids) and is written as an Item;
    it is never applied without human approval.
    """
    evidence: list[str] = []
    for report in reports:
        if isinstance(report, dict):
            evidence.append(str(report.get("report_id") or report.get("fingerprint") or "unknown"))
        else:
            evidence.append(str(getattr(report, "report_id", "unknown")))
    if not target:
        target = ",".join(sorted({str(e) for e in evidence[:3]})) or "unknown"
    summary = f"triage proposal for {target_class}:{target} from {len(evidence)} report(s)"
    return PatchProposal(
        target_class=target_class,
        target=target,
        summary=summary,
        evidence=evidence,
        measurement_runs=measurement_min_runs,
    )
