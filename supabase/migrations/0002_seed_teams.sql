-- Seed the opponent allowlist.
--
-- GENERATED FILE — do not hand-edit. Regenerate with:
--     python scripts/ops/supabase_admin.py emit-seed
--
-- The source of truth is data/evidence_packs/index.json, which is itself
-- generated from the ingested season. Keeping the seed derived from the shipped
-- packs means the database allowlist and the application allowlist cannot drift.
--
-- Idempotent: re-running updates names/records in place.

insert into public.teams (team_id, team_name, season, games_n, wins, losses, active)
values
    ('segev:10', 'GALIL ELION', '2025-26', 26, 7, 19, true),
    ('segev:11', 'BEER SHEVA', '2025-26', 26, 10, 16, true),
    ('segev:12', 'KIRYAT ATA', '2025-26', 26, 9, 17, true),
    ('segev:13', 'MACCABI RAANANA', '2025-26', 26, 6, 20, true),
    ('segev:14', 'RISHON LEZION', '2025-26', 26, 12, 14, true),
    ('segev:15', 'ELIZUR NETANYA', '2025-26', 26, 7, 19, true),
    ('segev:2', 'MACCABI TEL AVIV', '2025-26', 26, 24, 2, true),
    ('segev:3', 'HAPOEL TEL AVIV', '2025-26', 26, 22, 4, true),
    ('segev:4', 'HAPOEL JERUSALEM', '2025-26', 26, 18, 8, true),
    ('segev:5', 'HAPOEL HOLON', '2025-26', 26, 15, 11, true),
    ('segev:6', 'BNEI HERZLIYA', '2025-26', 26, 18, 8, true),
    ('segev:7', 'MACCABI RAMAT GAN', '2025-26', 26, 10, 16, true),
    ('segev:8', 'HAPOEL HAEMEK', '2025-26', 26, 15, 11, true),
    ('segev:9', 'NESS ZIONA', '2025-26', 26, 9, 17, true)
on conflict (team_id) do update set
    team_name  = excluded.team_name,
    season     = excluded.season,
    games_n    = excluded.games_n,
    wins       = excluded.wins,
    losses     = excluded.losses,
    active     = excluded.active,
    updated_at = now();
