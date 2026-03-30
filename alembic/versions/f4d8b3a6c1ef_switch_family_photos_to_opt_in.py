"""switch family photos inclusion to opt-in

Revision ID: f4d8b3a6c1ef
Revises: e83a4f9b1d21
Create Date: 2026-03-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4d8b3a6c1ef"
down_revision: Union[str, Sequence[str], None] = "e83a4f9b1d21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "posts",
            "include_in_family_photos",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text("0"),
        )
    op.execute(
        sa.text(
            "UPDATE posts SET include_in_family_photos = 0 "
            "WHERE include_in_family_photos = 1 OR include_in_family_photos IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "posts",
            "include_in_family_photos",
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.text("1"),
        )
