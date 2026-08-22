"""racing domain — teams, sessions/laps, setups, checklists, part life, fan layer

Phase 3, step 1. Builds the motorsports domain ON TOP of the existing PLM
foundation rather than beside it:

- A **company** is an existing `organizations` row. No new company table.
- **Team membership/roles** are existing `org_members` + `roles`. No parallel
  permission system; racing tables inherit tenant isolation unchanged.
- **Parts & CAD** stay `parts` / `part_revisions` / `documents`. `part_usages`
  only adds the racing context a PLM part lacks: which car runs it and its
  service life (hours/cycles against a limit).

Isolation follows 0002 exactly. Every tenant table gets `org_id`, RLS ENABLE +
FORCE, and a `<table>_tenant_isolation` policy on the bare
`current_setting('app.current_org')` form so an unscoped query ERRORS rather than
leaking rows (fail closed loud, §12.4).

Global (no RLS), for the same reason `users`/`org_members` are global — they are
resolved with no active org, by anonymous or cross-org callers:
  tracks        shared circuit reference data (seeded below)
  badges        fan achievement reference data (seeded below)
  fan_profiles / follows / predictions / point_ledger / user_badges /
  garage_passes   these belong to a USER across orgs, not to one tenant.

Public vs private is a COLUMN, not a second store: `run_sessions.visibility`,
`laps.visibility` and `setups.visibility` are 'public' | 'team'. Setups default to
'team' (the crown-jewel IP); sessions/laps default to 'public'. Serving a public
team page still scopes to exactly one tenant — the app resolves team -> org_id,
sets the GUC to THAT org, and additionally filters visibility='public' for
anonymous viewers. RLS keeps cross-tenant isolation; the visibility column keeps
the fan wall. (Flagged for review: the fan wall is app+column level by design,
RLS was never the mechanism for it.)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-22
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Self-contained (migrations must not import evolving app code).
RACING_TENANT_TABLES = (
    "teams", "cars", "drivers", "events", "run_sessions", "laps",
    "setups", "setup_values", "checklists", "checklist_items", "part_usages",
)

# Circuits used by IMSA WeatherTech and the NTT IndyCar Series.
TRACKS = [
    ("daytona", "Daytona International Speedway", "Daytona Beach, FL", 5.730, 12, "road", "IMSA"),
    ("sebring", "Sebring International Raceway", "Sebring, FL", 6.019, 17, "road", "IMSA"),
    ("road-atlanta", "Michelin Raceway Road Atlanta", "Braselton, GA", 4.088, 12, "road", "IMSA"),
    ("laguna-seca", "WeatherTech Raceway Laguna Seca", "Monterey, CA", 3.602, 11, "road", "IMSA+IndyCar"),
    ("watkins-glen", "Watkins Glen International", "Watkins Glen, NY", 5.472, 11, "road", "IMSA"),
    ("ctmp", "Canadian Tire Motorsport Park", "Bowmanville, ON", 3.957, 10, "road", "IMSA"),
    ("lime-rock", "Lime Rock Park", "Lakeville, CT", 2.462, 7, "road", "IMSA"),
    ("road-america", "Road America", "Elkhart Lake, WI", 6.515, 14, "road", "IMSA+IndyCar"),
    ("vir", "VIRginia International Raceway", "Alton, VA", 5.262, 17, "road", "IMSA"),
    ("indianapolis", "Indianapolis Motor Speedway", "Speedway, IN", 3.925, 14, "road", "IMSA+IndyCar"),
    ("cota", "Circuit of the Americas", "Austin, TX", 5.513, 20, "road", "IMSA+IndyCar"),
    ("long-beach", "Streets of Long Beach", "Long Beach, CA", 3.167, 11, "street", "IMSA+IndyCar"),
    ("st-petersburg", "Streets of St Petersburg", "St. Petersburg, FL", 2.897, 14, "street", "IndyCar"),
    ("thermal", "The Thermal Club", "Thermal, CA", 4.936, 17, "road", "IndyCar"),
    ("barber", "Barber Motorsports Park", "Birmingham, AL", 3.830, 17, "road", "IndyCar"),
    ("detroit", "Streets of Detroit", "Detroit, MI", 2.647, 10, "street", "IndyCar"),
    ("mid-ohio", "Mid-Ohio Sports Car Course", "Lexington, OH", 3.634, 13, "road", "IndyCar"),
    ("iowa", "Iowa Speedway", "Newton, IA", 1.408, 4, "oval", "IndyCar"),
    ("portland", "Portland International Raceway", "Portland, OR", 3.166, 12, "road", "IndyCar"),
    ("milwaukee", "Milwaukee Mile", "West Allis, WI", 1.633, 4, "oval", "IndyCar"),
    ("nashville", "Nashville Superspeedway", "Lebanon, TN", 2.140, 4, "oval", "IndyCar"),
    ("wwt-gateway", "World Wide Technology Raceway", "Madison, IL", 2.012, 4, "oval", "IndyCar"),
    ("toronto", "Streets of Toronto - Exhibition Place", "Toronto, ON", 2.874, 11, "street", "IndyCar"),
]

BADGES = [
    ("first_follow", "First Follow", "Followed your first team"),
    ("ten_correct", "10 Correct Calls", "Ten correct predictions"),
    ("streak_10", "10-Session Streak", "Ten sessions in a row"),
    ("endurance", "Full Endurance", "Followed a full endurance race"),
    ("fan_of_race", "Fan of the Race", "Top fan for a race weekend"),
    ("called_record", "Called a Record", "Predicted a new track record"),
]


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    # ══ Global reference data ═════════════════════════════════════════════════
    # Circuits are shared by every tenant, and are read on public pages with no
    # org context — so global, no RLS (same rationale as users/org_members).
    op.execute("""
        CREATE TABLE tracks (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            track_id   TEXT NOT NULL,
            name       TEXT NOT NULL,
            location   TEXT NOT NULL DEFAULT '',
            country    TEXT NOT NULL DEFAULT 'USA',
            length_km  DOUBLE PRECISION,
            turns      INTEGER,
            layout     TEXT NOT NULL DEFAULT 'road',
            series_tag TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_tracks PRIMARY KEY (id),
            CONSTRAINT uq_tracks_track_id UNIQUE (track_id),
            CONSTRAINT ck_tracks_layout CHECK (layout IN ('road','street','oval'))
        )
    """)

    op.execute("""
        CREATE TABLE badges (
            id          BIGINT GENERATED BY DEFAULT AS IDENTITY,
            code        TEXT NOT NULL,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            CONSTRAINT pk_badges PRIMARY KEY (id),
            CONSTRAINT uq_badges_code UNIQUE (code)
        )
    """)

    # ══ Tenant tables ═════════════════════════════════════════════════════════
    # A team is one car/program entry inside a company (organizations row).
    op.execute("""
        CREATE TABLE teams (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id     BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            team_key   TEXT NOT NULL,
            name       TEXT NOT NULL,
            series     TEXT NOT NULL DEFAULT '',
            class      TEXT NOT NULL DEFAULT '',
            car_number TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_teams PRIMARY KEY (id),
            CONSTRAINT uq_teams_org_team_key UNIQUE (org_id, team_key)
        )
    """)

    op.execute("""
        CREATE TABLE cars (
            id           BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id       BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            team_id      BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            chassis      TEXT NOT NULL,
            model        TEXT NOT NULL DEFAULT '',
            homologation TEXT NOT NULL DEFAULT '',
            created_at   TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_cars PRIMARY KEY (id)
        )
    """)

    # A driver may or may not have a login; user_id is optional.
    op.execute("""
        CREATE TABLE drivers (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id     BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            team_id    BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
            name       TEXT NOT NULL,
            country    TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_drivers PRIMARY KEY (id)
        )
    """)

    op.execute("""
        CREATE TABLE events (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id     BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            team_id    BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            track_id   BIGINT NOT NULL REFERENCES tracks(id),
            name       TEXT NOT NULL,
            round      INTEGER,
            starts_on  DATE,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_events PRIMARY KEY (id)
        )
    """)

    # Named run_sessions: `sessions` is reserved for auth elsewhere in the stack.
    op.execute("""
        CREATE TABLE run_sessions (
            id           BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id       BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            event_id     BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            car_id       BIGINT REFERENCES cars(id) ON DELETE SET NULL,
            session_type TEXT NOT NULL,
            session_date TIMESTAMPTZ,
            visibility   TEXT NOT NULL DEFAULT 'public',
            created_by   BIGINT REFERENCES users(id) ON DELETE SET NULL,
            created_at   TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_run_sessions PRIMARY KEY (id),
            CONSTRAINT ck_run_sessions_type CHECK (session_type IN
                ('test','fp1','fp2','fp3','qual','warmup','race')),
            CONSTRAINT ck_run_sessions_visibility CHECK (visibility IN ('public','team'))
        )
    """)

    # Lap times are stored in milliseconds; formatting is a presentation concern.
    op.execute("""
        CREATE TABLE laps (
            id             BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id         BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            run_session_id BIGINT NOT NULL REFERENCES run_sessions(id) ON DELETE CASCADE,
            lap_no         INTEGER NOT NULL,
            lap_time_ms    INTEGER NOT NULL,
            driver_id      BIGINT REFERENCES drivers(id) ON DELETE SET NULL,
            tire_compound  TEXT NOT NULL DEFAULT '',
            is_pb          INTEGER NOT NULL DEFAULT 0,
            is_fastest     INTEGER NOT NULL DEFAULT 0,
            visibility     TEXT NOT NULL DEFAULT 'public',
            created_at     TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_laps PRIMARY KEY (id),
            CONSTRAINT ck_laps_visibility CHECK (visibility IN ('public','team')),
            CONSTRAINT ck_laps_time_positive CHECK (lap_time_ms > 0)
        )
    """)

    # Setup sheets: team-only by default. This is the IP the fan wall protects.
    op.execute("""
        CREATE TABLE setups (
            id             BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id         BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            setup_key      TEXT NOT NULL,
            team_id        BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            track_id       BIGINT NOT NULL REFERENCES tracks(id),
            run_session_id BIGINT REFERENCES run_sessions(id) ON DELETE SET NULL,
            baseline       TEXT NOT NULL DEFAULT '',
            revision_label TEXT NOT NULL DEFAULT 'A',
            visibility     TEXT NOT NULL DEFAULT 'team',
            engineer_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
            notes          TEXT NOT NULL DEFAULT '',
            created_at     TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_setups PRIMARY KEY (id),
            CONSTRAINT uq_setups_org_setup_key UNIQUE (org_id, setup_key),
            CONSTRAINT ck_setups_visibility CHECK (visibility IN ('public','team'))
        )
    """)

    # Key/value like part_attributes, so new setup fields need no migration.
    op.execute("""
        CREATE TABLE setup_values (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id     BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            setup_id   BIGINT NOT NULL REFERENCES setups(id) ON DELETE CASCADE,
            group_name TEXT NOT NULL DEFAULT '',
            attr_key   TEXT NOT NULL,
            attr_value TEXT NOT NULL DEFAULT '',
            unit       TEXT NOT NULL DEFAULT '',
            attr_order INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT pk_setup_values PRIMARY KEY (id),
            CONSTRAINT uq_setup_values_setup_id_attr_key UNIQUE (setup_id, attr_key)
        )
    """)

    op.execute("""
        CREATE TABLE checklists (
            id          BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id      BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            team_id     BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            is_template INTEGER NOT NULL DEFAULT 0,
            event_id    BIGINT REFERENCES events(id) ON DELETE SET NULL,
            created_at  TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_checklists PRIMARY KEY (id)
        )
    """)

    op.execute("""
        CREATE TABLE checklist_items (
            id            BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id        BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            checklist_id  BIGINT NOT NULL REFERENCES checklists(id) ON DELETE CASCADE,
            label         TEXT NOT NULL,
            assigned_role TEXT NOT NULL DEFAULT '',
            is_done       INTEGER NOT NULL DEFAULT 0,
            signed_by     BIGINT REFERENCES users(id) ON DELETE SET NULL,
            signed_at     TIMESTAMPTZ,
            item_order    INTEGER NOT NULL DEFAULT 0,
            CONSTRAINT pk_checklist_items PRIMARY KEY (id)
        )
    """)

    # Racing context for an existing PLM part: which car runs it, and its life.
    op.execute("""
        CREATE TABLE part_usages (
            id           BIGINT GENERATED BY DEFAULT AS IDENTITY,
            org_id       BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            part_id      BIGINT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
            team_id      BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            car_id       BIGINT REFERENCES cars(id) ON DELETE SET NULL,
            hours_used   DOUBLE PRECISION NOT NULL DEFAULT 0,
            hours_limit  DOUBLE PRECISION,
            cycles_used  INTEGER NOT NULL DEFAULT 0,
            cycles_limit INTEGER,
            status       TEXT NOT NULL DEFAULT 'ok',
            updated_at   TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_part_usages PRIMARY KEY (id),
            CONSTRAINT uq_part_usages_part_id_team_id UNIQUE (part_id, team_id),
            CONSTRAINT ck_part_usages_status CHECK (status IN ('ok','service_soon','over'))
        )
    """)

    # ══ Fan layer — global (a fan is a user, not a tenant) ════════════════════
    op.execute("""
        CREATE TABLE fan_profiles (
            id           BIGINT GENERATED BY DEFAULT AS IDENTITY,
            user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL DEFAULT '',
            fan_type     TEXT NOT NULL DEFAULT 'spectator',
            points       INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_fan_profiles PRIMARY KEY (id),
            CONSTRAINT uq_fan_profiles_user_id UNIQUE (user_id),
            CONSTRAINT ck_fan_profiles_type CHECK (fan_type IN ('spectator','member'))
        )
    """)

    op.execute("""
        CREATE TABLE follows (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            team_id    BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_follows PRIMARY KEY (id),
            CONSTRAINT uq_follows_user_id_team_id UNIQUE (user_id, team_id)
        )
    """)

    op.execute("""
        CREATE TABLE predictions (
            id             BIGINT GENERATED BY DEFAULT AS IDENTITY,
            user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            run_session_id BIGINT REFERENCES run_sessions(id) ON DELETE CASCADE,
            question       TEXT NOT NULL,
            answer         TEXT NOT NULL,
            is_correct     INTEGER,
            points_awarded INTEGER NOT NULL DEFAULT 0,
            created_at     TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_predictions PRIMARY KEY (id)
        )
    """)

    op.execute("""
        CREATE TABLE point_ledger (
            id         BIGINT GENERATED BY DEFAULT AS IDENTITY,
            user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            delta      INTEGER NOT NULL,
            reason     TEXT NOT NULL DEFAULT '',
            ref_type   TEXT NOT NULL DEFAULT '',
            ref_id     BIGINT,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_point_ledger PRIMARY KEY (id)
        )
    """)

    op.execute("""
        CREATE TABLE user_badges (
            id        BIGINT GENERATED BY DEFAULT AS IDENTITY,
            user_id   BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            badge_id  BIGINT NOT NULL REFERENCES badges(id) ON DELETE CASCADE,
            earned_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_user_badges PRIMARY KEY (id),
            CONSTRAINT uq_user_badges_user_id_badge_id UNIQUE (user_id, badge_id)
        )
    """)

    # Paid fan tier; revenue share is reporting on top of this table.
    op.execute("""
        CREATE TABLE garage_passes (
            id          BIGINT GENERATED BY DEFAULT AS IDENTITY,
            user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            team_id     BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            tier        TEXT NOT NULL DEFAULT 'garage_pass',
            price_cents INTEGER NOT NULL DEFAULT 600,
            status      TEXT NOT NULL DEFAULT 'active',
            started_at  TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT pk_garage_passes PRIMARY KEY (id),
            CONSTRAINT uq_garage_passes_user_id_team_id UNIQUE (user_id, team_id),
            CONSTRAINT ck_garage_passes_status CHECK (status IN ('active','canceled','past_due'))
        )
    """)

    # ══ Indexes ═══════════════════════════════════════════════════════════════
    # Tenant lookups are always org-scoped first, matching 0002's composite style.
    op.execute("CREATE INDEX ix_teams_org_car_number ON teams (org_id, car_number)")
    op.execute("CREATE INDEX ix_cars_team_id ON cars (team_id)")
    op.execute("CREATE INDEX ix_drivers_team_id ON drivers (team_id)")
    op.execute("CREATE INDEX ix_events_org_team ON events (org_id, team_id)")
    op.execute("CREATE INDEX ix_events_track_id ON events (track_id)")
    op.execute("CREATE INDEX ix_run_sessions_event_id ON run_sessions (event_id)")
    op.execute("CREATE INDEX ix_laps_run_session_id ON laps (run_session_id)")
    # Public fan reads hit only published laps — keep that path cheap.
    op.execute("CREATE INDEX ix_laps_public ON laps (run_session_id, lap_time_ms) "
               "WHERE visibility = 'public'")
    op.execute("CREATE INDEX ix_setups_org_team_track ON setups (org_id, team_id, track_id)")
    op.execute("CREATE INDEX ix_setup_values_setup_id ON setup_values (setup_id)")
    op.execute("CREATE INDEX ix_checklists_org_team ON checklists (org_id, team_id)")
    op.execute("CREATE INDEX ix_checklist_items_checklist_id ON checklist_items (checklist_id)")
    op.execute("CREATE INDEX ix_part_usages_org_team ON part_usages (org_id, team_id)")
    op.execute("CREATE INDEX ix_part_usages_part_id ON part_usages (part_id)")
    op.execute("CREATE INDEX ix_follows_team_id ON follows (team_id)")
    op.execute("CREATE INDEX ix_predictions_user_id ON predictions (user_id)")
    op.execute("CREATE INDEX ix_point_ledger_user_id ON point_ledger (user_id)")
    op.execute("CREATE INDEX ix_user_badges_user_id ON user_badges (user_id)")
    op.execute("CREATE INDEX ix_garage_passes_team_id ON garage_passes (team_id)")

    # ══ Row-level security on the racing tenant tables (same pattern as 0002) ══
    for t in RACING_TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {t}_tenant_isolation ON {t}
              USING      (org_id = current_setting('app.current_org')::bigint)
              WITH CHECK (org_id = current_setting('app.current_org')::bigint)
        """)

    # Grants: ALTER DEFAULT PRIVILEGES from 0002 covers tables created by the
    # owner role, so hyperplm_app already has DML on everything above. Re-issuing
    # explicitly is harmless and keeps this migration self-describing.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hyperplm_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hyperplm_app")
    op.execute("REVOKE ALL ON alembic_version FROM hyperplm_app")

    # ══ Seed reference data ═══════════════════════════════════════════════════
    rows = ", ".join(
        "({}, {}, {}, {}, {}, {}, {})".format(
            _sql_str(t[0]), _sql_str(t[1]), _sql_str(t[2]),
            "NULL" if t[3] is None else repr(t[3]),
            "NULL" if t[4] is None else str(t[4]),
            _sql_str(t[5]), _sql_str(t[6]),
        )
        for t in TRACKS
    )
    op.execute(
        "INSERT INTO tracks (track_id, name, location, length_km, turns, layout, series_tag) "
        f"VALUES {rows} ON CONFLICT (track_id) DO NOTHING"
    )

    brows = ", ".join(
        "({}, {}, {})".format(_sql_str(b[0]), _sql_str(b[1]), _sql_str(b[2]))
        for b in BADGES
    )
    op.execute(
        f"INSERT INTO badges (code, name, description) VALUES {brows} "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    for t in RACING_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")

    # Children before parents (FK order); CASCADE-free explicit drops.
    for t in (
        "garage_passes", "user_badges", "point_ledger", "predictions", "follows",
        "fan_profiles", "part_usages", "checklist_items", "checklists",
        "setup_values", "setups", "laps", "run_sessions", "events", "drivers",
        "cars", "teams", "badges", "tracks",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t}")
