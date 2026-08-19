"""Product layer: the public report contract, its PDF form, and the service
that produces and stores it.

Layering (one direction only)::

    agents/pack_store.py   shipped deterministic evidence (no PBP cache needed)
      |
      v
    agents/pipeline.py     3 agents -> validation -> render (a plain dict)
      |
      v
    reports/contracts.py   render dict -> PublicReport (the frontend/DB contract)
      |
      +--> reports/pdf.py       PublicReport -> PDF bytes (no provider call)
      +--> persistence/         PublicReport -> saved row
      |
      v
    web/                   HTTP only

``reports/service.py`` is the one place that orchestrates the above, so both the
API and the batch CLI generate reports through exactly the same path.
"""

from .contracts import PublicReport, ReportSummary, TeamSummary

__all__ = ["PublicReport", "ReportSummary", "TeamSummary"]
