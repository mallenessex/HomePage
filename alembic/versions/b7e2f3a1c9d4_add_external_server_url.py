"""add external_server_url to server_settings

Revision ID: b7e2f3a1c9d4
Revises: 24269189aa70
Create Date: 2026-02-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e2f3a1c9d4'
down_revision: Union[str, Sequence[str], None] = '24269189aa70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add external_server_url column to server_settings."""
    op.add_column('server_settings', sa.Column('external_server_url', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove external_server_url column."""
    op.drop_column('server_settings', 'external_server_url')
