"""Portable, versioned production EvidencePack artifacts.

Why this module exists
----------------------
``evidence_pack.load_league_data()`` rebuilds every team's pack by walking 182
processed team-game records *and* 297 cached raw play-by-play files. That is the
right thing locally, and impossible in production: ``data/raw`` and
``data/processed`` are git-ignored, so a deployed instance has neither.

So the deterministic layer is **serialized once, offline, and shipped**. The
production path is::

    data/evidence_packs/pack_<team>.json  ->  agents  ->  validation  ->  storage

No PBP cache, no stats walk, no network. Rebuilding the artifacts is a local
developer step (``scripts/scouting_report/build_production_packs.py``), never a
request-time one.

Integrity
---------
Every artifact carries ``pack_hash`` — sha256 over a *canonical* serialization
of the pack (sorted keys, no whitespace). :func:`load_artifact` recomputes it and
refuses a mismatch, so a hand-edited or truncated pack fails loudly at load
instead of quietly changing what an agent is told.

The envelope also carries provenance the ``EvidencePack`` schema deliberately
does not: which source games produced it, and a fingerprint over those ids. The
pack schema is ``extra="forbid"`` and is the agent contract — provenance belongs
around it, not inside it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .schemas import EvidencePack

# Bump when the *envelope* shape changes. The pack's own contract version is
# EvidencePack.definition_version and moves independently.
PACK_ARTIFACT_VERSION = "packs-v1"

DEFAULT_PACKS_DIRNAME = "evidence_packs"
INDEX_FILENAME = "index.json"

_HASH_PREFIX = "sha256:"


class PackArtifactError(RuntimeError):
    """An artifact is missing, malformed, or fails its integrity check."""


# ---- canonicalization / hashing --------------------------------------------


def canonical_pack_json(pack: EvidencePack) -> str:
    """The one byte-form the hash is defined over.

    Sorted keys and no whitespace, so an unrelated formatting choice can never
    change a hash and two runs over identical data always agree.
    """
    return json.dumps(
        pack.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_pack_hash(pack: EvidencePack) -> str:
    return _HASH_PREFIX + hashlib.sha256(canonical_pack_json(pack).encode("utf-8")).hexdigest()


def fingerprint(values: list[str]) -> str:
    """Order-independent digest of a set of ids (used for source provenance)."""
    joined = "\n".join(sorted(values))
    return _HASH_PREFIX + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def team_slug(team_id: str) -> str:
    """``segev:4`` -> ``segev_4``. Filesystem- and URL-safe, still readable."""
    return team_id.replace(":", "_")


# ---- envelope ---------------------------------------------------------------


class PackProvenance(BaseModel):
    """Where this pack came from. Everything here is deterministic."""

    model_config = ConfigDict(extra="forbid")

    source: str
    season: str
    team_id: str
    team_name: str
    games_n: int
    wins: int
    losses: int
    date_range: str
    definition_version: str
    source_game_ids: list[str] = Field(default_factory=list)
    source_fingerprint: str
    evidence_items_n: int
    candidate_ids_n: int
    pack_states: list[str] = Field(default_factory=list)


class PackArtifact(BaseModel):
    """One team's shippable deterministic evidence, plus its provenance."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: str = PACK_ARTIFACT_VERSION
    pack_hash: str
    provenance: PackProvenance
    pack: EvidencePack

    @property
    def team_id(self) -> str:
        return self.pack.team_id

    def verify(self) -> None:
        """Raise if the stored hash does not match the stored pack."""
        actual = compute_pack_hash(self.pack)
        if actual != self.pack_hash:
            raise PackArtifactError(
                f"pack hash mismatch for {self.pack.team_id!r}: "
                f"stored {self.pack_hash}, computed {actual}. The artifact was "
                f"edited or truncated; rebuild it rather than trusting it."
            )


class PackIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    season: str
    games_n: int
    wins: int
    losses: int
    file: str
    pack_hash: str
    pack_states: list[str] = Field(default_factory=list)


class PackIndex(BaseModel):
    """Manifest of the shipped packs — also the production team allowlist."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: str = PACK_ARTIFACT_VERSION
    source: str
    season: str
    generated_at: str
    teams_n: int
    definition_version: str
    teams: list[PackIndexEntry] = Field(default_factory=list)


# ---- build ------------------------------------------------------------------


def build_artifact(pack: EvidencePack, source_game_ids: list[str]) -> PackArtifact:
    return PackArtifact(
        artifact_version=PACK_ARTIFACT_VERSION,
        pack_hash=compute_pack_hash(pack),
        provenance=PackProvenance(
            source=pack.source,
            season=pack.season,
            team_id=pack.team_id,
            team_name=pack.team_name,
            games_n=pack.games_n,
            wins=pack.wins,
            losses=pack.losses,
            date_range=pack.date_range,
            definition_version=pack.definition_version,
            source_game_ids=sorted(source_game_ids),
            source_fingerprint=fingerprint(source_game_ids),
            evidence_items_n=len(pack.evidence),
            candidate_ids_n=len(pack.screening.candidate_ids),
            pack_states=list(pack.pack_states),
        ),
        pack=pack,
    )


def artifact_filename(team_id: str) -> str:
    return f"pack_{team_slug(team_id)}.json"


def artifact_path(packs_dir: Path, team_id: str) -> Path:
    return Path(packs_dir) / artifact_filename(team_id)


def write_artifact(artifact: PackArtifact, packs_dir: Path) -> Path:
    packs_dir = Path(packs_dir)
    packs_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_path(packs_dir, artifact.pack.team_id)
    # indent=1 keeps the file reviewable in a diff without doubling its size.
    path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def build_index(artifacts: list[PackArtifact], *, generated_at: str) -> PackIndex:
    ordered = sorted(artifacts, key=lambda a: a.pack.team_id)
    seasons = {a.pack.season for a in ordered}
    sources = {a.pack.source for a in ordered}
    return PackIndex(
        artifact_version=PACK_ARTIFACT_VERSION,
        source=sorted(sources)[0] if sources else "unknown",
        season=sorted(seasons)[-1] if seasons else "unknown",
        generated_at=generated_at,
        teams_n=len(ordered),
        definition_version=ordered[0].pack.definition_version if ordered else "unknown",
        teams=[
            PackIndexEntry(
                team_id=a.pack.team_id,
                team_name=a.pack.team_name,
                season=a.pack.season,
                games_n=a.pack.games_n,
                wins=a.pack.wins,
                losses=a.pack.losses,
                file=artifact_filename(a.pack.team_id),
                pack_hash=a.pack_hash,
                pack_states=list(a.pack.pack_states),
            )
            for a in ordered
        ],
    )


def write_index(index: PackIndex, packs_dir: Path) -> Path:
    packs_dir = Path(packs_dir)
    packs_dir.mkdir(parents=True, exist_ok=True)
    path = packs_dir / INDEX_FILENAME
    path.write_text(
        json.dumps(index.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ---- load -------------------------------------------------------------------


def load_artifact(path: Path, *, verify: bool = True) -> PackArtifact:
    """Read and (by default) integrity-check one artifact."""
    path = Path(path)
    if not path.is_file():
        raise PackArtifactError(f"evidence pack artifact not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackArtifactError(f"{path.name} is not valid JSON: {exc}") from exc

    try:
        artifact = PackArtifact.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError -> one typed error
        raise PackArtifactError(
            f"{path.name} does not match the pack artifact contract: {exc}"
        ) from exc

    if artifact.artifact_version != PACK_ARTIFACT_VERSION:
        raise PackArtifactError(
            f"{path.name} is artifact_version {artifact.artifact_version!r}, "
            f"this build expects {PACK_ARTIFACT_VERSION!r}. Rebuild the packs."
        )
    if verify:
        artifact.verify()
    return artifact


def load_index(packs_dir: Path) -> PackIndex:
    path = Path(packs_dir) / INDEX_FILENAME
    if not path.is_file():
        raise PackArtifactError(
            f"pack index not found: {path}. Run "
            f"scripts/scouting_report/build_production_packs.py to generate it."
        )
    try:
        return PackIndex.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise PackArtifactError(f"{path} is not a valid pack index: {exc}") from exc


class PackStore:
    """Read-only access to the shipped production packs.

    Artifacts are loaded lazily and cached, so a process that only ever serves
    saved reports never pays to parse 14 packs.
    """

    def __init__(self, packs_dir: Path, *, verify: bool = True):
        self.packs_dir = Path(packs_dir)
        self.verify = verify
        self._index: PackIndex | None = None
        self._cache: dict[str, PackArtifact] = {}

    # -- index ---------------------------------------------------------------

    @property
    def index(self) -> PackIndex:
        if self._index is None:
            self._index = load_index(self.packs_dir)
        return self._index

    @property
    def available(self) -> bool:
        """True when a usable index is present — checked without raising."""
        try:
            self.index
        except PackArtifactError:
            return False
        return True

    def team_ids(self) -> list[str]:
        return [entry.team_id for entry in self.index.teams]

    def entries(self) -> list[PackIndexEntry]:
        return list(self.index.teams)

    def has_team(self, team_id: str) -> bool:
        return any(entry.team_id == team_id for entry in self.index.teams)

    # -- packs ---------------------------------------------------------------

    def get(self, team_id: str) -> PackArtifact:
        if team_id in self._cache:
            return self._cache[team_id]
        if not self.has_team(team_id):
            raise PackArtifactError(
                f"team {team_id!r} is not in the shipped pack index "
                f"({', '.join(self.team_ids())})"
            )
        artifact = load_artifact(artifact_path(self.packs_dir, team_id), verify=self.verify)
        if artifact.pack.team_id != team_id:
            raise PackArtifactError(
                f"artifact file for {team_id!r} contains a pack for {artifact.pack.team_id!r}"
            )
        self._cache[team_id] = artifact
        return artifact

    def get_pack(self, team_id: str) -> EvidencePack:
        return self.get(team_id).pack

    def load_all(self) -> dict[str, PackArtifact]:
        """Load and verify every shipped pack. Used by the integrity test/CLI."""
        return {team_id: self.get(team_id) for team_id in self.team_ids()}


def default_packs_dir(data_dir: Path) -> Path:
    return Path(data_dir) / DEFAULT_PACKS_DIRNAME
