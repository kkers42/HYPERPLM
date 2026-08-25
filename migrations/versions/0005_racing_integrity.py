"""racing integrity — keep the public directory and part life correct automatically

Phase 3, step 5. Two things the app would otherwise have to remember to do by
hand on every write, which is exactly how data drifts:

1. `team_directory` was only populated by 0004's backfill, so a team created
   afterwards would never appear to fans (or would keep a stale name). A trigger
   now mirrors teams into the directory on INSERT/UPDATE. Slug is derived and
   de-duplicated; `is_public` is preserved on update so publishing is a decision
   the team keeps, not something a rename resets. New teams default to NOT public
   — publishing stays opt-in.

2. `part_usages.status` (ok | service_soon | over) was caller-supplied and could
   contradict hours_used/hours_limit. A trigger now derives it: >=100% of limit is
   'over', >=80% is 'service_soon', else 'ok'. Parts with no limit stay 'ok'.

Both are database-side so they hold no matter which code path writes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. team_directory stays in sync with teams ────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION sync_team_directory() RETURNS TRIGGER AS $$
        DECLARE base_slug TEXT; final_slug TEXT; n INT := 0;
        BEGIN
            base_slug := lower(regexp_replace(
                COALESCE(NEW.name,'team') || '-' || COALESCE(NEW.car_number,''),
                '[^a-zA-Z0-9]+', '-', 'g'));
            base_slug := trim(both '-' from base_slug);
            final_slug := base_slug;
            -- de-duplicate against other teams' slugs
            WHILE EXISTS (SELECT 1 FROM team_directory
                          WHERE slug = final_slug AND team_id <> NEW.id) LOOP
                n := n + 1;
                final_slug := base_slug || '-' || n;
            END LOOP;

            INSERT INTO team_directory (team_id, org_id, slug, name, car_number,
                                        series, class, is_public, updated_at)
            VALUES (NEW.id, NEW.org_id, final_slug, NEW.name, NEW.car_number,
                    NEW.series, NEW.class, 0, now())
            ON CONFLICT (team_id) DO UPDATE SET
                org_id     = EXCLUDED.org_id,
                slug       = EXCLUDED.slug,
                name       = EXCLUDED.name,
                car_number = EXCLUDED.car_number,
                series     = EXCLUDED.series,
                class      = EXCLUDED.class,
                -- publishing is the team's decision; a rename must not reset it
                is_public  = team_directory.is_public,
                updated_at = now();
            RETURN NEW;
        END $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_teams_sync_directory
        AFTER INSERT OR UPDATE ON teams
        FOR EACH ROW EXECUTE FUNCTION sync_team_directory();
    """)

    # ── 2. part_usages.status derived from service life ───────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION derive_part_status() RETURNS TRIGGER AS $$
        DECLARE h_ratio NUMERIC := 0; c_ratio NUMERIC := 0; worst NUMERIC;
        BEGIN
            IF NEW.hours_limit IS NOT NULL AND NEW.hours_limit > 0 THEN
                h_ratio := NEW.hours_used / NEW.hours_limit;
            END IF;
            IF NEW.cycles_limit IS NOT NULL AND NEW.cycles_limit > 0 THEN
                c_ratio := NEW.cycles_used::NUMERIC / NEW.cycles_limit;
            END IF;
            worst := GREATEST(h_ratio, c_ratio);
            NEW.status := CASE
                WHEN worst >= 1.0  THEN 'over'
                WHEN worst >= 0.8  THEN 'service_soon'
                ELSE 'ok' END;
            NEW.updated_at := now();
            RETURN NEW;
        END $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_part_usages_status
        BEFORE INSERT OR UPDATE ON part_usages
        FOR EACH ROW EXECUTE FUNCTION derive_part_status();
    """)

    # Re-derive existing rows so stored values match the rule.
    op.execute("UPDATE part_usages SET hours_used = hours_used")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_part_usages_status ON part_usages")
    op.execute("DROP FUNCTION IF EXISTS derive_part_status()")
    op.execute("DROP TRIGGER IF EXISTS trg_teams_sync_directory ON teams")
    op.execute("DROP FUNCTION IF EXISTS sync_team_directory()")
