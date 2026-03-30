"""add include_in_family_photos flag to posts

Revision ID: e83a4f9b1d21
Revises: c2f1d6e4a9ab
Create Date: 2026-03-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e83a4f9b1d21"
down_revision: Union[str, Sequence[str], None] = "c2f1d6e4a9ab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column(
            "include_in_family_photos",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE posts SET include_in_family_photos = 1 "
            "WHERE include_in_family_photos IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("posts", "include_in_family_photos")
