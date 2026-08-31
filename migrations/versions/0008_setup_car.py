"""a setup sheet records which car it was run on

Phase 3, step 8. From the testing notes: "are we trying to connect the multiple
cars a team might have to the set-ups and data/components?" — yes, and the model
almost did. Sessions and part usages already name a car; setups only named a
team, so with two cars on one entry you could not answer "what was on car 74
when we set that time?"

One nullable column. Existing sheets keep working with no car attributed.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-31
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE setups ADD COLUMN car_id BIGINT "
               "REFERENCES cars(id) ON DELETE SET NULL")
    op.execute("CREATE INDEX ix_setups_car ON setups (car_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_setups_car")
    op.execute("ALTER TABLE setups DROP COLUMN IF EXISTS car_id")
