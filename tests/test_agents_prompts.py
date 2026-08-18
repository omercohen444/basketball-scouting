"""Prompt construction — provider-agnostic, buildable with no credentials.

These tests are the guard that the hard prohibitions actually reach the model.
The validator enforces them afterwards, but a prompt that never states them wastes
a provider call on output that will be rejected."""

from __future__ import annotations

from basketball_scout.agents import prompts
from basketball_scout.agents.schemas import PACK_STATE_NO_WIN_LOSS

from agents_factories import make_item, make_pack, make_tactical, make_triage

# A generous ceiling: the pack is ~25 items and the whole point of the compact
# serialization is that it stays well inside a normal context window.
MAX_PROMPT_CHARS = 60_000


def _pack(**kwargs):
    return make_pack(items=[make_item(f"EV.season.m{i}") for i in range(12)], **kwargs)


def test_prompts_build_without_any_credentials():
    """conftest strips every project env var; prompt building must not care."""
    pack = _pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    assert prompts.triage_task_prompt(pack)
    assert prompts.tactical_task_prompt(pack, triage)
    assert prompts.head_scout_task_prompt(pack, triage, tactical)


def test_every_system_prompt_states_the_no_numbers_rule():
    for builder in (
        prompts.triage_system_prompt,
        prompts.tactical_system_prompt,
        prompts.head_scout_system_prompt,
    ):
        assert "NEVER state a number" in builder()


def test_every_system_prompt_forbids_player_scheme_and_video_claims():
    for builder in (
        prompts.triage_system_prompt,
        prompts.tactical_system_prompt,
        prompts.head_scout_system_prompt,
    ):
        text = builder().lower()
        assert "individual player" in text
        assert "scheme" in text
        assert "video" in text or "film" in text


def test_neutral_direction_warning_is_present():
    assert "neutral" in prompts.triage_system_prompt().lower()


def test_triage_prompt_offers_only_the_candidate_pool():
    pack = _pack()
    pack.screening.candidate_ids = ["EV.season.m0", "EV.season.m1"]
    text = prompts.triage_task_prompt(pack)
    assert "EV.season.m0" in text
    assert "EV.season.m1" in text
    assert "EV.season.m5" not in text


def test_triage_prompt_lists_unavailable_evidence():
    """Naming the gaps is what stops the model filling them from world knowledge."""
    text = prompts.triage_task_prompt(_pack())
    assert "UNAVAILABLE" in text
    assert "NA.video" in text


def test_no_win_loss_state_injects_the_explicit_prohibition():
    text = prompts.triage_task_prompt(_pack(pack_states=[PACK_STATE_NO_WIN_LOSS], wins=24, losses=2))
    assert "NO USABLE WIN/LOSS EVIDENCE" in text


def test_win_loss_prohibition_absent_for_a_normal_team():
    assert "NO USABLE WIN/LOSS EVIDENCE" not in prompts.triage_task_prompt(_pack())


def test_masked_win_loss_numbers_never_appear_in_a_prompt():
    """If a masked effect leaked into the prompt the model could quote it, which
    is the exact failure the masking exists to prevent.

    Asserts on the numbers themselves, not the substring "win_loss" — the flag
    name ``win_loss_signal`` legitimately appears and carries no figure."""
    pack = _pack(pack_states=[PACK_STATE_NO_WIN_LOSS], wins=24, losses=2)
    for item in pack.evidence:
        item.win_loss.agent_rankable = False
        item.win_loss.effect_status = "masked_no_wl_evidence"
        item.win_loss.effect_size = None
        item.win_loss.win_average_display = None
        item.win_loss.loss_average_display = None
        item.flags.win_loss_signal = None

    text = prompts.triage_task_prompt(pack)
    assert '"in_wins"' not in text
    assert '"in_losses"' not in text
    assert '"effect_size"' not in text


def test_win_loss_numbers_are_offered_when_they_are_rankable():
    """Complement to the masking test: a team that does have W/L evidence must
    actually receive it, or the report loses its main section."""
    text = prompts.triage_task_prompt(_pack())
    assert '"in_wins"' in text
    assert '"effect_size"' in text


def test_head_scout_prompt_exposes_implications_not_evidence_ids():
    """The Head Scout cites implications only — that is what makes 'introduces no
    new evidence' structural rather than policed."""
    pack = _pack()
    triage = make_triage(pack, n=8)
    tactical = make_tactical(triage)
    text = prompts.head_scout_task_prompt(pack, triage, tactical)
    assert "T1" in text
    assert "implication ids" in prompts.head_scout_system_prompt().lower()


def test_head_scout_is_told_the_summary_must_have_no_digits():
    assert "no digits" in prompts.head_scout_system_prompt().lower()


def test_tactical_prompt_carries_the_claim_strength_ladder():
    text = prompts.tactical_system_prompt()
    for level in ("established", "indicated", "speculative"):
        assert level in text
    assert "at least TWO" in text


def test_prompts_stay_within_a_sane_size_budget():
    pack = _pack()
    triage = make_triage(pack, n=12)
    tactical = make_tactical(triage)
    for text in (
        prompts.triage_task_prompt(pack),
        prompts.tactical_task_prompt(pack, triage),
        prompts.head_scout_task_prompt(pack, triage, tactical),
    ):
        assert len(text) < MAX_PROMPT_CHARS, f"prompt grew to {len(text)} chars"
