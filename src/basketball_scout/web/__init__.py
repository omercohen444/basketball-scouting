"""HTTP layer.

Everything in this package is about transport: routing, status codes, auth
headers, templates. No basketball logic, no analytics, no provider calls — those
live in ``stats/``, ``agents/`` and ``reports/`` and are reachable from here only
through :class:`~basketball_scout.reports.service.ReportService`.

The split that matters for this product:

* **public** routes read saved reports and never trigger a paid generation;
* **admin** routes (exactly one) generate, and require ``X-Admin-Token``.
"""

from .app import create_app

__all__ = ["create_app"]
