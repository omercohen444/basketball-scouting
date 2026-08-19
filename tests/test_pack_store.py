"""Production EvidencePack artifacts: integrity, portability, and the real 14.

The shipped artifacts are what a deployment runs on — there is no PBP cache in
production — so "all 14 load and hash-check" is a genuine deployment gate, not a
formality.
"""

from __future__ import annotations

import json

import pytest
from agents_factories import make_pack
from pack_factories import PRODUCTION_PACKS_DIR, write_synthetic_packs

from basketball_scout.agents.pack_store import (
    PACK_ARTIFACT_VERSION,
    PackArtifactError,
    PackStore,
    artifact_path,
    build_artifact,
    build_index,
    canonical_pack_json,
    compute_pack_hash,
    fingerprint,
    load_artifact,
    team_slug,
    write_artifact,
    write_index,
)

EXPECTED_TEAMS = 14


# ---- canonicalization -------------------------------------------------------


def test_canonical_json_is_stable_across_key_order():
    pack = make_pack()
    reparsed = type(pack).model_validate(json.loads(pack.model_dump_json()))
    assert canonical_pack_json(pack) == canonical_pack_json(reparsed)
    assert compute_pack_hash(pack) == compute_pack_hash(reparsed)


def test_hash_changes_when_any_evidence_changes():
    pack = make_pack()
    before = compute_pack_hash(pack)
    pack.evidence[0].display_value = "99.9%"
    assert compute_pack_hash(pack) != before


def test_fingerprint_ignores_ordering():
    assert fingerprint(["b", "a", "c"]) == fingerprint(["c", "b", "a"])
    assert fingerprint(["a"]) != fingerprint(["b"])


def test_team_slug_is_filesystem_safe():
    assert team_slug("segev:4") == "segev_4"


# ---- round trip -------------------------------------------------------------


def test_artifact_round_trips_through_disk(tmp_path):
    pack = make_pack()
    artifact = build_artifact(pack, ["101", "102"])
    path = write_artifact(artifact, tmp_path)

    loaded = load_artifact(path)
    assert loaded.pack.team_id == pack.team_id
    assert loaded.pack_hash == artifact.pack_hash
    assert loaded.provenance.source_game_ids == ["101", "102"]
    assert loaded.provenance.evidence_items_n == len(pack.evidence)


def test_tampered_artifact_is_rejected(tmp_path):
    artifact = build_artifact(make_pack(), ["1"])
    path = write_artifact(artifact, tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pack"]["evidence"][0]["display_value"] = "100.0%"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PackArtifactError, match="hash mismatch"):
        load_artifact(path)


def test_wrong_artifact_version_is_rejected(tmp_path):
    artifact = build_artifact(make_pack(), ["1"])
    path = write_artifact(artifact, tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["artifact_version"] = "packs-v0"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PackArtifactError, match="artifact_version"):
        load_artifact(path)


def test_malformed_json_is_a_typed_error(tmp_path):
    path = tmp_path / "pack_broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PackArtifactError, match="not valid JSON"):
        load_artifact(path)


def test_missing_file_is_a_typed_error(tmp_path):
    with pytest.raises(PackArtifactError, match="not found"):
        load_artifact(tmp_path / "pack_absent.json")


# ---- store ------------------------------------------------------------------


def test_store_lists_and_loads(tmp_path):
    write_synthetic_packs(tmp_path)
    store = PackStore(tmp_path)
    assert store.available
    assert store.team_ids() == ["segev:11", "segev:4"]
    assert store.get_pack("segev:4").team_id == "segev:4"


def test_store_rejects_a_team_outside_the_index(tmp_path):
    write_synthetic_packs(tmp_path)
    with pytest.raises(PackArtifactError, match="not in the shipped pack index"):
        PackStore(tmp_path).get("segev:999")


def test_store_reports_unavailable_without_raising(tmp_path):
    store = PackStore(tmp_path / "nothing-here")
    assert store.available is False


def test_store_caches_loaded_artifacts(tmp_path):
    write_synthetic_packs(tmp_path)
    store = PackStore(tmp_path)
    first = store.get("segev:4")
    artifact_path(tmp_path, "segev:4").unlink()  # a cached read must not touch disk
    assert store.get("segev:4") is first


def test_index_records_every_artifact(tmp_path):
    artifacts = [build_artifact(make_pack(team_id=t), ["1"]) for t in ("segev:4", "segev:2")]
    for artifact in artifacts:
        write_artifact(artifact, tmp_path)
    write_index(build_index(artifacts, generated_at="2026-08-19T00:00:00Z"), tmp_path)

    index = PackStore(tmp_path).index
    assert index.teams_n == 2
    assert {e.team_id for e in index.teams} == {"segev:2", "segev:4"}
    assert all(e.file.startswith("pack_") for e in index.teams)


# ---- the real, committed artifacts -----------------------------------------


@pytest.mark.skipif(
    not (PRODUCTION_PACKS_DIR / "index.json").is_file(),
    reason="production evidence packs are not present in this checkout",
)
class TestShippedProductionPacks:
    def test_exactly_fourteen_teams(self):
        assert len(PackStore(PRODUCTION_PACKS_DIR).team_ids()) == EXPECTED_TEAMS

    def test_all_fourteen_load_and_hash_check(self):
        artifacts = PackStore(PRODUCTION_PACKS_DIR).load_all()
        assert len(artifacts) == EXPECTED_TEAMS
        for team_id, artifact in artifacts.items():
            artifact.verify()  # raises on drift
            assert artifact.pack.team_id == team_id
            assert artifact.pack.evidence, f"{team_id} has no evidence items"
            assert artifact.pack.screening.candidate_ids, f"{team_id} has no candidates"

    def test_version_metadata_is_present_and_consistent(self):
        store = PackStore(PRODUCTION_PACKS_DIR)
        index = store.index
        assert index.artifact_version == PACK_ARTIFACT_VERSION
        for entry in index.teams:
            artifact = store.get(entry.team_id)
            assert artifact.artifact_version == PACK_ARTIFACT_VERSION
            assert artifact.pack_hash == entry.pack_hash
            assert artifact.provenance.definition_version == artifact.pack.definition_version
            assert artifact.provenance.season == index.season
            assert artifact.provenance.source_fingerprint.startswith("sha256:")
            assert artifact.provenance.source_game_ids

    def test_masking_invariant_survived_serialization(self):
        """An effect size must never travel with agent_rankable false."""
        for artifact in PackStore(PRODUCTION_PACKS_DIR).load_all().values():
            leaks = [
                item.evidence_id
                for item in artifact.pack.evidence
                if not item.win_loss.agent_rankable and item.win_loss.effect_size is not None
            ]
            assert not leaks, f"{artifact.pack.team_id} leaked {leaks}"

    def test_the_degenerate_team_is_still_flagged(self):
        """Maccabi Tel Aviv (24-2) has no rankable W/L evidence; the shipped
        artifact must still carry that state or the report would silently start
        making outcome claims it cannot support."""
        pack = PackStore(PRODUCTION_PACKS_DIR).get_pack("segev:2")
        assert "no_win_loss_evidence" in pack.pack_states
        assert all(not i.win_loss.agent_rankable for i in pack.evidence)
