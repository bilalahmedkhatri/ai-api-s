# BondsGame games API

This module keeps BondsGame game records in a separately configured PostgreSQL
database. Set `GAMES_BONDSGAME_DATABASE_URL` in the backend `.env` to the
Supabase PostgreSQL connection string; it is independent of
`BLOG_BONDSGAME_DATABASE_URL`. Keep credentials server-side and require TLS.
The engine uses `NullPool` plus unique prepared-statement names for compatibility
with transaction poolers.

## Tables and types

- `games`: stable text ID and unique slug; editorial/catalogue fields; `DATE`
  for exact release dates; `TIMESTAMPTZ` for created, updated, and published
  timestamps; constrained status and score values; JSONB for verdict, specs,
  and purchase links; GIN genre index.
- `game_comments`: text ID, FK to `games.id`, author/content, non-negative vote
  counters, moderation status, and `TIMESTAMPTZ created_at`. Pending is the
  default. A game deletion cascades, but this API deliberately exposes no delete.

Both tables enable RLS. Supabase direct reads can see only published games and
approved comments attached to published games. There are no anonymous insert or
update policies. Writes go through the authenticated backend.

## Explicit migration

The migration is operator-run and never executes at startup or on an API call.
It refuses to adopt or overwrite pre-existing `games` / `game_comments` tables;
inspect and plan a data-preserving migration first if either already exists.

```powershell
uv run python -m app.games.migrate --sql
uv run python -m app.games.migrate --action upgrade
uv run python -m app.games.migrate --action verify
```

`--sql` prints the matching PostgreSQL DDL for Supabase SQL Editor. The connected
upgrade prints only a status/error type, never the database URL. Upgrade is
transactional and creates `games_schema_version` at revision `games_0001`.

## Endpoints

Application routes require an authorized BondsGame server API key and must be
called through server-side code. The BondsGame authorized app must receive exact
method/path grants for each route it will call (for example `GET /api/v1/games`
and `POST /api/v1/games/{slug}/comments`); app permissions are never granted by
this migration:

- `GET /api/v1/games?limit=24&offset=0&genre=Action` — published catalogue.
- `GET /api/v1/games/{slug}` — published detail.
- `GET /api/v1/games/{slug}/similar?limit=4` — published titles sharing a genre.
- `GET /api/v1/games/{slug}/comments?limit=20&before=<comment-id>` — approved
  comments, newest first.
- `POST /api/v1/games/{slug}/comments` — submits a comment as pending moderation.

Dashboard routes use an approved admin dashboard session; mutations also check
the configured dashboard Origin:

- `GET/POST /api/v1/dashboard/games`
- `PATCH /api/v1/dashboard/games/{game_id}`
- `GET /api/v1/dashboard/games/{game_id}/comments`
- `PATCH /api/v1/dashboard/games/comments/{comment_id}`

The public API does not expose drafts. No delete or public vote endpoint is
included. Import existing BondsGame seed records separately, preserving their
IDs and slugs; map legacy `score`/`rating` deliberately and send release dates as
ISO date values. No BondsGame frontend files are modified by this module.
