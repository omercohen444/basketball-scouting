"""PDF rendering of a **saved** scouting report.

Input is a :class:`~basketball_scout.reports.contracts.PublicReport` and nothing
else. That is the point: producing a PDF must never reach the provider, never
touch the PBP cache, and never recompute a number. Downloading a PDF is a pure
read of something already generated, validated and stored.

ReportLab (platypus) rather than HTML-to-PDF: no headless browser, no system
libraries, installs cleanly on Railway, and the layout is a few hundred lines of
ordinary Python. Visual design is deliberately restrained — the final look is a
later, interactive design step.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .contracts import EvidenceCard, PublicReport

# Restrained, print-legible palette. Ink-first: colour carries meaning
# (reliability, claim strength) and never decorates.
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#D1D5DB")
ACCENT = colors.HexColor("#1D4ED8")
BAND = colors.HexColor("#F3F4F6")

PAGE_MARGIN = 18 * mm

# One sentence of trust context for the cover, not a methodology essay — this
# is a coach-facing document. Validation detail, hashes, model/backend
# identifiers and report/evidence version ids stay in the API contract for
# audit but are deliberately absent here; see reports/contracts.py's
# "coach-facing curation" section for the full reasoning.
TRUST_NOTE = (
    "Every figure is computed from official play-by-play. Team-level only — "
    "no player, scheme, or video-derived analysis."
)


def _safe(text: str | None) -> str:
    """Escape for platypus' mini-markup and drop anything the base fonts cannot
    encode (the standard Type-1 fonts are WinAnsi; a stray non-cp1252 character
    would otherwise print as a black box)."""
    if not text:
        return ""
    encodable = text.encode("cp1252", errors="replace").decode("cp1252")
    return escape(encodable)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "BodyText2",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=INK,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    return {
        "title": ParagraphStyle(
            "TitleX", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "SubtitleX", parent=body, fontSize=10, leading=14,
            textColor=MUTED, spaceAfter=2,
        ),
        "h2": ParagraphStyle(
            "H2X", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12.5, leading=16, textColor=INK, spaceBefore=12, spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "H3X", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=10.5, leading=14, textColor=INK, spaceBefore=6, spaceAfter=3,
        ),
        "body": body,
        "claim": ParagraphStyle(
            "ClaimX", parent=body, fontSize=10, leading=14, spaceAfter=2,
        ),
        "evidence": ParagraphStyle(
            "EvidenceX", parent=body, fontSize=8.2, leading=11,
            textColor=MUTED, leftIndent=10, spaceAfter=1,
        ),
        "small": ParagraphStyle(
            "SmallX", parent=body, fontSize=8.2, leading=11, textColor=MUTED,
        ),
        "cell": ParagraphStyle(
            "CellX", parent=body, fontSize=8.2, leading=10.5, spaceAfter=0,
        ),
        "cellhead": ParagraphStyle(
            "CellHeadX", parent=body, fontName="Helvetica-Bold",
            fontSize=8.2, leading=10.5, spaceAfter=0, textColor=INK,
        ),
    }


def _evidence_line(card: EvidenceCard) -> str:
    bits = [f"<b>{_safe(card.metric)}</b> {_safe(card.value)}"]
    if card.scope and card.scope != "season":
        bits.append(f"scope {_safe(card.scope)}")
    if card.league_rank:
        bits.append(f"rank {_safe(card.league_rank)}")
    if card.league_average:
        bits.append(f"league avg {_safe(card.league_average)}")
    if card.win_loss.available and card.win_loss.in_wins:
        effect = f", d={card.win_loss.effect_size}" if card.win_loss.effect_size is not None else ""
        bits.append(
            f"W {_safe(card.win_loss.in_wins)} / L {_safe(card.win_loss.in_losses)}{effect}"
        )
    bits.append(f"n={card.sample_games}g")
    bits.append(f"{_safe(card.reliability)} reliability")
    return " &#183; ".join(bits)


def _meta_line(report: PublicReport) -> str:
    prov = report.provenance
    parts = [
        f"Season {_safe(prov.season)}",
        f"Record {_safe(prov.record)}",
        f"{prov.games_n} games",
    ]
    if prov.date_range:
        parts.append(_safe(prov.date_range_display))
    return " &#183; ".join(parts)


def _header_footer(report: PublicReport):
    label = f"{report.team_name} — scouting report · {report.report_id}"
    encodable = label.encode("cp1252", errors="replace").decode("cp1252")

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(PAGE_MARGIN, 11 * mm, encodable)
        canvas.drawRightString(A4[0] - PAGE_MARGIN, 11 * mm, f"Page {canvas.getPageNumber()}")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(PAGE_MARGIN, 14 * mm, A4[0] - PAGE_MARGIN, 14 * mm)
        canvas.restoreState()

    return draw


def _evidence_table(cards: list[EvidenceCard], st: dict[str, ParagraphStyle]) -> Table:
    header = ["Metric", "Scope", "Value", "League rank", "League avg", "Sample", "Reliability"]
    data = [[Paragraph(_safe(h), st["cellhead"]) for h in header]]
    for card in cards:
        data.append(
            [
                Paragraph(_safe(card.metric), st["cell"]),
                Paragraph(_safe(card.scope), st["cell"]),
                Paragraph(_safe(card.value), st["cell"]),
                Paragraph(_safe(card.league_rank or "—"), st["cell"]),
                Paragraph(_safe(card.league_average or "—"), st["cell"]),
                Paragraph(f"{card.sample_games}g", st["cell"]),
                Paragraph(_safe(card.reliability), st["cell"]),
            ]
        )
    available = A4[0] - 2 * PAGE_MARGIN
    widths = [w * available for w in (0.26, 0.13, 0.11, 0.14, 0.13, 0.08, 0.15)]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BAND),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def build_report_pdf(report: PublicReport) -> bytes:
    """Render a saved report to PDF bytes. Pure function of its input."""
    st = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=22 * mm,
        title=f"Scouting Report — {report.team_name}",
        author="Basketball Analytics and AI Scouting System",
        subject=f"Opponent scouting report · {report.season}",
    )

    story: list = []

    # -- cover block ---------------------------------------------------------
    story.append(Paragraph("Opponent Scouting Report", st["subtitle"]))
    story.append(Paragraph(_safe(report.team_name), st["title"]))
    story.append(Paragraph(_meta_line(report), st["subtitle"]))
    story.append(Paragraph(f"Generated {_safe(report.generated_at)}", st["small"]))
    story.append(Spacer(1, 4))
    # scope_note restates season/record/games, which _meta_line already prints
    # directly above it. Kept in the API contract, dropped from the document.
    if report.provenance.pack_states:
        story.append(Spacer(1, 3))
        story.append(
            Paragraph(
                "<b>Data state:</b> " + _safe(", ".join(report.provenance.pack_states)),
                st["small"],
            )
        )
    story.append(Spacer(1, 8))

    # -- executive summary ---------------------------------------------------
    story.append(Paragraph("Executive Summary", st["h2"]))
    story.append(Paragraph(_safe(report.executive_summary), st["body"]))

    # -- Keys to Win (most actionable content goes early) --------------------
    #
    # Every heading below is guarded by CondPageBreak rather than PageBreak.
    # The difference is the whole fix: PageBreak always burns the rest of the
    # page, which is how a 6-line page 3 with 196mm of white space happened;
    # CondPageBreak only breaks when the section genuinely cannot start here.
    if report.recommendations:
        story.append(CondPageBreak(96))
        story.append(Paragraph("Keys to Win", st["h2"]))
        story.append(
            Paragraph(
                "Each key is an evidence-supported objective. A tactical option is "
                "included only when the evidence points to that specific method — most "
                "keys have none, and that is expected, not a gap.",
                st["small"],
            )
        )
        story.append(Spacer(1, 3))
        for rec in sorted(report.recommendations, key=lambda r: r.priority):
            block: list = [
                Paragraph(
                    f"<b>{rec.priority}. {_safe(rec.objective)}</b> "
                    f'<font color="#6B7280">(confidence: {_safe(rec.confidence)})</font>',
                    st["claim"],
                ),
                Paragraph(f"<b>Why it matters:</b> {_safe(rec.why_it_matters)}", st["body"]),
            ]
            for card in rec.evidence[:4]:
                block.append(Paragraph(_evidence_line(card), st["evidence"]))
            for tactic in rec.tactics:
                block.append(
                    Paragraph(
                        f"<b>Tactical option:</b> {_safe(tactic.method)} "
                        f"&#8212; {_safe(tactic.mechanism)}",
                        st["evidence"],
                    )
                )
            block.append(Spacer(1, 6))
            story.append(KeepTogether(block))

    # -- narrative sections --------------------------------------------------
    # The heading rides inside the first claim's KeepTogether block so it can
    # never strand alone at the foot of a page. ``sections.items()`` filters
    # empty sections, so index 0 always exists.
    for _key, title, claims in report.sections.items():
        for index, claim in enumerate(claims):
            block = [Paragraph(_safe(claim.text), st["claim"])]
            for card in claim.evidence[:4]:
                block.append(Paragraph(_evidence_line(card), st["evidence"]))
            block.append(Spacer(1, 4))
            if index == 0:
                block.insert(0, Paragraph(_safe(title), st["h2"]))
            story.append(KeepTogether(block))

    # -- deterministic evidence ---------------------------------------------
    if report.key_evidence:
        story.append(CondPageBreak(120))
        story.append(Paragraph("Key Deterministic Evidence", st["h2"]))
        story.append(
            Paragraph(
                "Every value below is computed from play-by-play, not written by a model.",
                st["small"],
            )
        )
        story.append(Spacer(1, 4))
        story.append(_evidence_table(report.key_evidence, st))

    # -- data limits -----------------------------------------------------------
    # Caveats and declared-unavailable evidence collapsed into one short
    # section — a coach needs "what to watch out for", not two headings that
    # say overlapping things.
    if report.caveats or report.unavailable_evidence:
        story.append(CondPageBreak(60))
        story.append(Paragraph("Data Limits", st["h2"]))
        for caveat in report.caveats:
            story.append(Paragraph(f"&#183; {_safe(caveat)}", st["body"]))
        for item in report.unavailable_evidence:
            story.append(
                Paragraph(f"&#183; <b>{_safe(item.label)}:</b> {_safe(item.reason)}", st["body"])
            )

    # -- trust note ------------------------------------------------------------
    # One sentence, not a methodology essay or an audit log — see TRUST_NOTE.
    story.append(Paragraph(TRUST_NOTE, st["small"]))

    draw = _header_footer(report)
    doc.build(story, onFirstPage=draw, onLaterPages=draw)
    return buffer.getvalue()


def pdf_filename(report: PublicReport) -> str:
    """A stable, filesystem-safe download name."""
    slug = "".join(c if c.isalnum() else "-" for c in report.team_name.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"scouting-report-{slug or report.team_id.replace(':', '-')}-{report.season}.pdf"
