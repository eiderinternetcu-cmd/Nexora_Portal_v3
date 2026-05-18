"""Initial schema — users, subscribers, plans, subscriptions, devices, audit_logs

Revision ID: 001
Revises:
Create Date: 2026-05-17
"""
from typing import Sequence, Union
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(128)),
        sa.Column("role", sa.Enum("admin", "reseller", name="userrole"), nullable=False, server_default="reseller"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_ip", sa.String(45)),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── subscribers ──────────────────────────────────────────────────────────
    op.create_table(
        "subscribers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("activation_code", sa.String(64)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(32)),
        sa.Column("full_name", sa.String(128)),
        sa.Column("id_cedula", sa.String(32)),
        sa.Column("status", sa.Enum("active", "expired", "suspended", "banned", name="subscriberstatus"),
                  nullable=False, server_default="active"),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_subscribers_username", "subscribers", ["username"], unique=True)
    op.create_index("ix_subscribers_activation_code", "subscribers", ["activation_code"], unique=True)
    op.create_index("ix_subscribers_email", "subscribers", ["email"])
    op.create_index("ix_subscribers_id_cedula", "subscribers", ["id_cedula"])
    op.create_index("ix_subscribers_status", "subscribers", ["status"])

    # ── plans ────────────────────────────────────────────────────────────────
    op.create_table(
        "plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("max_connections", sa.Integer, nullable=False, server_default="1"),
        sa.Column("max_devices", sa.Integer, nullable=False, server_default="2"),
        sa.Column("duration_days", sa.Integer, nullable=False, server_default="30"),
        sa.Column("price", sa.Numeric(10, 2)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_plans_name", "plans", ["name"], unique=True)

    # ── subscriptions ────────────────────────────────────────────────────────
    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("subscriber_id", UUID(as_uuid=True),
                  sa.ForeignKey("subscribers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_id", UUID(as_uuid=True),
                  sa.ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("renewal_note", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_subscriptions_subscriber_id", "subscriptions", ["subscriber_id"])
    op.create_index("ix_subscriptions_plan_id", "subscriptions", ["plan_id"])
    op.create_index("ix_subscriptions_expires_at", "subscriptions", ["expires_at"])
    op.create_index("ix_subscriptions_is_active", "subscriptions", ["is_active"])

    # ── devices ──────────────────────────────────────────────────────────────
    op.create_table(
        "devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("subscriber_id", UUID(as_uuid=True),
                  sa.ForeignKey("subscribers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("mac_address", sa.String(32)),
        sa.Column("model", sa.String(128)),
        sa.Column("brand", sa.String(64)),
        sa.Column("device_type", sa.String(32)),
        sa.Column("app_version", sa.String(32)),
        sa.Column("os_version", sa.String(32)),
        sa.Column("user_agent", sa.Text),
        sa.Column("last_ip", sa.String(45)),
        sa.Column("is_blocked", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("block_reason", sa.String(255)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_devices_device_id", "devices", ["device_id"], unique=True)
    op.create_index("ix_devices_subscriber_id", "devices", ["subscriber_id"])
    op.create_index("ix_devices_mac_address", "devices", ["mac_address"])
    op.create_index("ix_devices_is_blocked", "devices", ["is_blocked"])
    op.create_index("ix_devices_last_seen_at", "devices", ["last_seen_at"])

    # ── audit_logs ───────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_username", sa.String(64)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64)),
        sa.Column("target_id", sa.String(128)),
        sa.Column("details", JSONB),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_target_type", "audit_logs", ["target_type"])
    op.create_index("ix_audit_logs_target_id", "audit_logs", ["target_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # ── updated_at trigger ───────────────────────────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ("users", "subscribers", "plans", "subscriptions", "devices"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
        """)


def downgrade() -> None:
    for table in ("users", "subscribers", "plans", "subscriptions", "devices"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at()")
    op.drop_table("audit_logs")
    op.drop_table("devices")
    op.drop_table("subscriptions")
    op.drop_table("plans")
    op.drop_table("subscribers")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS userrole")
    op.execute("DROP TYPE IF EXISTS subscriberstatus")
