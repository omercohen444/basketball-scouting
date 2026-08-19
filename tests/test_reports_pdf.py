"""PDF rendering.

The load-bearing property is that a PDF is a pure function of a *stored* report:
no provider call, no evidence pack, no database. Everything else here is
robustness — a report with missing optional pieces must still render.
"""

from __future__ import annotations

from test_reports_contracts import build

from basketball_scout.reports.contracts import PublicReport
from basketball_scout.reports.pdf import build_report_pdf, pdf_filename


def test_output_is_a_pdf():
    data = build_report_pdf(build())
    assert data[:5] == b"%PDF-"
    assert data.rstrip().endswith(b"%%EOF")
    assert len(data) > 3000


def test_pdf_generation_touches_nothing_external(monkeypatch):
    """Fail loudly if a future edit reaches for the network or the provider."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("PDF rendering must not open a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert build_report_pdf(build())[:5] == b"%PDF-"


def test_pdf_is_reproducible_for_the_same_report():
    """ReportLab stamps a creation date, so bytes differ; page structure must
    not. Compare the object count as a cheap structural proxy."""
    report = build()
    first, second = build_report_pdf(report), build_report_pdf(report)
    assert first.count(b" obj") == second.count(b" obj")
    assert abs(len(first) - len(second)) < 64


def test_report_with_no_recommendations_still_renders():
    report = build().model_copy(update={"recommendations": []})
    assert build_report_pdf(report)[:5] == b"%PDF-"


def test_report_with_empty_optional_blocks_still_renders():
    report = build().model_copy(
        update={"caveats": [], "unavailable_evidence": [], "key_evidence": [], "scope_note": ""}
    )
    assert build_report_pdf(report)[:5] == b"%PDF-"


def test_non_latin_text_does_not_break_the_base_fonts():
    """Standard Type-1 fonts are WinAnsi; unencodable characters must be
    replaced rather than raise or print as boxes."""
    report = build().model_copy(
        update={"team_name": "מכבי תל אביב", "executive_summary": "Sanity — “quotes” … 中文"}
    )
    assert build_report_pdf(report)[:5] == b"%PDF-"


def test_markup_in_model_prose_is_escaped_not_interpreted():
    """Claim text comes from a model; platypus parses mini-HTML."""
    report = build().model_copy(
        update={"executive_summary": "<b>unclosed <font color='red'> & ampersand"}
    )
    assert build_report_pdf(report)[:5] == b"%PDF-"


def test_filename_is_safe_and_descriptive():
    name = pdf_filename(build())
    assert name == "scouting-report-hapoel-jerusalem-2025-26.pdf"
    assert "/" not in name and "\\" not in name and ":" not in name


def test_filename_survives_an_awkward_team_name():
    report: PublicReport = build().model_copy(update={"team_name": "A/B:C  D"})
    name = pdf_filename(report)
    assert "/" not in name and ":" not in name
    assert name.endswith(".pdf")


def test_pdf_no_longer_builds_audit_or_engineering_sections():
    """The PDF is FlateDecode-compressed — verified empirically that neither a
    known-present string ("Executive Summary") nor a known-absent one survives
    a naive byte search either way, so byte-level checks on the output are
    unreliable in both directions. Assert on the generator's own source
    instead: it must not build story elements for validation detail, pack
    hashes, or model/backend identifiers — the coach-facing report shouldn't
    have them, full stop, regardless of what any one report contains."""
    import inspect

    from basketball_scout.reports import pdf as pdf_module

    source = inspect.getsource(pdf_module.build_report_pdf)
    for removed in (
        "Automated Validation", "Methodology",
        "pack_hash", "model_name", "report.report_id", "report.report_version",
        "Game-Plan Priorities",
    ):
        assert removed not in source, f"{removed!r} still appears in build_report_pdf"

    assert "Keys to Win" in source
    assert "Why it matters" in source
    assert "Tactical option" in source


# ---- the mislabelled legacy metric ------------------------------------------


def test_the_legacy_trailing_label_is_corrected_in_the_pdf():
    """One shipped evidence id's bin starts at five points behind, not six.
    Correcting the bin would move the value and invalidate every stored report,
    so the data stays and the label is fixed at every render point."""
    from basketball_scout.reports.contracts import EvidenceCard
    from basketball_scout.reports.pdf import _label

    card = EvidenceCard.model_construct(
        evidence_id="EV.behind_6_plus.efg_pct",
        metric="Effective FG% When Trailing 6+",
    )
    assert _label(card) == "Effective FG% When Trailing 5+"


def test_no_pdf_render_point_bypasses_the_label_correction():
    """The PDF is compressed, so a byte search on the output proves nothing in
    either direction. Assert on the generator instead: every place a metric
    name reaches the page must go through the helper."""
    import inspect

    from basketball_scout.reports import pdf

    source = inspect.getsource(pdf)
    assert "card.metric" not in source.replace("display_label(card.evidence_id, card.metric)", ""),         "a metric name is rendered without the label correction"
    assert source.count("_label(card)") >= 2
