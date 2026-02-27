"""add crossword_puzzles table

Revision ID: c2f1d6e4a9ab
Revises: b7e2f3a1c9d4
Create Date: 2026-02-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2f1d6e4a9ab"
down_revision: Union[str, Sequence[str], None] = "b7e2f3a1c9d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "crossword_puzzles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=False),
        sa.Column("edition", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publish_date", "edition", name="uq_crossword_puzzles_publish_date_edition"),
    )
    op.create_index(op.f("ix_crossword_puzzles_id"), "crossword_puzzles", ["id"], unique=False)
    op.create_index(op.f("ix_crossword_puzzles_publish_date"), "crossword_puzzles", ["publish_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_crossword_puzzles_publish_date"), table_name="crossword_puzzles")
    op.drop_index(op.f("ix_crossword_puzzles_id"), table_name="crossword_puzzles")
    op.drop_table("crossword_puzzles")
