"""Reliability refactor: scheduling, profile observations and notification outbox.

Revision ID: reliability_refactor
Revises: 8321c8f6eed7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "reliability_refactor"
down_revision: str | None = "8321c8f6eed7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("advertisements") as batch:
        batch.add_column(sa.Column("detail_checked_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("monitoring_profiles") as batch:
        batch.add_column(sa.Column("last_scan_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_scan_finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_monitoring_profiles_next_scan_at", ["next_scan_at"])
        batch.create_index("ix_monitoring_profiles_locked_until", ["locked_until"])

    with op.batch_alter_table("scan_history") as batch:
        batch.add_column(sa.Column("trigger", sa.String(length=16), nullable=False, server_default="scheduled"))
        batch.add_column(sa.Column("expected_ads", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("detail_success_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("detail_failure_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))

    op.create_table(
        "profile_merchants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("monitoring_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("profile_id", "merchant_id", name="uq_profile_merchant"),
    )
    op.create_index("ix_profile_merchants_profile_id", "profile_merchants", ["profile_id"])
    op.create_index("ix_profile_merchants_merchant_id", "profile_merchants", ["merchant_id"])

    op.create_table(
        "profile_advertisements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("monitoring_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("advertisement_id", sa.Integer(), sa.ForeignKey("advertisements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("profile_id", "advertisement_id", name="uq_profile_advertisement"),
    )
    op.create_index("ix_profile_advertisements_profile_id", "profile_advertisements", ["profile_id"])
    op.create_index("ix_profile_advertisements_advertisement_id", "profile_advertisements", ["advertisement_id"])

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("monitoring_profiles.id", ondelete="SET NULL")),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id", ondelete="SET NULL")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_index("ix_notification_outbox_event_type", "notification_outbox", ["event_type"])
    op.create_index("ix_notification_outbox_status", "notification_outbox", ["status"])
    op.create_index("ix_notification_outbox_next_attempt_at", "notification_outbox", ["next_attempt_at"])
    op.create_index("ix_notification_outbox_deduplication_key", "notification_outbox", ["deduplication_key"], unique=True)

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outbox_id", sa.Integer(), sa.ForeignKey("notification_outbox.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("outbox_id", "chat_id", name="uq_outbox_chat"),
    )
    op.create_index("ix_notification_deliveries_outbox_id", "notification_deliveries", ["outbox_id"])
    op.create_index("ix_notification_deliveries_status", "notification_deliveries", ["status"])
    op.create_index("ix_notification_deliveries_next_attempt_at", "notification_deliveries", ["next_attempt_at"])

    # Preserve existing baseline semantics without flooding users after deployment.
    op.execute(
        "INSERT OR IGNORE INTO profile_merchants "
        "(profile_id, merchant_id, first_seen_at, last_seen_at, is_active) "
        "SELECT p.id, m.id, m.first_seen_at, m.last_seen_at, 1 FROM monitoring_profiles p CROSS JOIN merchants m"
    )
    op.execute(
        "INSERT OR IGNORE INTO profile_advertisements "
        "(profile_id, advertisement_id, first_seen_at, last_seen_at, is_active) "
        "SELECT p.id, a.id, a.first_seen_at, a.last_seen_at, 1 FROM monitoring_profiles p CROSS JOIN advertisements a"
    )
    op.execute(
        "UPDATE scan_history SET status='ABORTED', finished_at=CURRENT_TIMESTAMP, "
        "error_message='Application stopped before scan completion' WHERE status='RUNNING'"
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notification_outbox")
    op.drop_table("profile_advertisements")
    op.drop_table("profile_merchants")
    with op.batch_alter_table("scan_history") as batch:
        for column in ("duration_ms", "detail_failure_count", "detail_success_count", "pages_fetched", "expected_ads", "trigger"):
            batch.drop_column(column)
    with op.batch_alter_table("monitoring_profiles") as batch:
        batch.drop_index("ix_monitoring_profiles_locked_until")
        batch.drop_index("ix_monitoring_profiles_next_scan_at")
        for column in ("locked_until", "next_scan_at", "last_scan_finished_at", "last_scan_started_at"):
            batch.drop_column(column)
    with op.batch_alter_table("advertisements") as batch:
        batch.drop_column("detail_checked_at")
