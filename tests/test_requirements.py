"""Dependency manifests must not drift apart.

CI installs ``requirements-ci.txt``; developers and the deployment install
``requirements.txt``. If a new runtime dependency lands in one and not the
other, CI either stops covering it or starts failing to import it — and both
failures are confusing at exactly the wrong moment. This test makes the drift
loud and immediate.
"""

from __future__ import annotations

from basketball_scout.config import REPO_ROOT

#: Documented, intentional differences. Anything else is drift.
CI_EXCLUSIONS = {"crewai"}


def _requirements(name: str) -> dict[str, str]:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    pinned: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        for separator in (">=", "==", "~=", "<", ">"):
            if separator in line:
                name_part = line.split(separator)[0]
                break
        else:
            name_part = line
        pinned[name_part.strip().split("[")[0].lower()] = line
    return pinned


def test_ci_manifest_is_the_full_one_minus_the_documented_exclusions():
    full = _requirements("requirements.txt")
    ci = _requirements("requirements-ci.txt")

    assert set(ci) == set(full) - CI_EXCLUSIONS, (
        "requirements.txt and requirements-ci.txt disagree beyond the documented "
        f"exclusions {sorted(CI_EXCLUSIONS)}"
    )


def test_shared_dependencies_carry_identical_constraints():
    full = _requirements("requirements.txt")
    ci = _requirements("requirements-ci.txt")
    mismatched = {name: (full[name], ci[name]) for name in ci if full[name] != ci[name]}
    assert not mismatched, f"version constraints differ: {mismatched}"


def test_the_product_stage_dependencies_are_declared():
    full = _requirements("requirements.txt")
    for package in ("fastapi", "uvicorn", "jinja2", "httpx", "reportlab", "pydantic"):
        assert package in full, f"{package} is used by the product layer but not declared"


def test_crewai_is_excluded_from_ci_on_purpose():
    """Guards the claim in requirements-ci.txt's header: the offline suite must
    not need the agent provider tree."""
    assert "crewai" not in _requirements("requirements-ci.txt")
    assert "crewai" in _requirements("requirements.txt")
