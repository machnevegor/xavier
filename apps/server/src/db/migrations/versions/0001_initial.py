"""Initial database schema.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _updated() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    """Create the complete application schema."""
    op.create_table(
        "credential_events",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.func.uuidv7(), nullable=False),
        sa.Column("credential_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_credential_events"),
    )
    op.create_index(
        "ix_credential_events_credential_id_id",
        "credential_events",
        ["credential_id", "id"],
    )
    op.create_table(
        "leases",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("holder", sa.Text(), nullable=True),
        sa.Column("token", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_leases"),
    )
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        _created(),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_users_updated_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["users.id"],
            name="fk_users_deleted_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "uq_users_username",
        "users",
        ["username"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "workers",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("internal_url", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_workers"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("prefix", name="uq_api_keys_prefix"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_api_keys_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "chats",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("tool_groups", ARRAY(sa.Text()), nullable=False),
        sa.Column("execute_network", sa.Boolean(), nullable=False),
        sa.Column("proactive", sa.Boolean(), nullable=False),
        sa.Column("declined_capabilities", ARRAY(sa.Text()), nullable=False),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_version", sa.BigInteger(), nullable=False),
        sa.Column("pause_reason", sa.String(length=32), nullable=True),
        sa.Column("title_source", sa.String(length=32), nullable=False),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.Column("snapshot_manifest", sa.Text(), nullable=True),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_chats"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_chats_created_by_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_chats_updated_by_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"], ["users.id"], name="fk_chats_deleted_by_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_chats_created_by", "chats", ["created_by"])
    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_artifacts_chat_id_chats", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_artifacts_created_by_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_artifacts_chat_id_id", "artifacts", ["chat_id", "id"])
    op.create_index("ix_artifacts_created_by", "artifacts", ["created_by"])
    op.create_table(
        "credentials",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("secret_type", sa.String(length=32), nullable=False),
        sa.Column("header", sa.Text(), nullable=True),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("refresh_url", sa.Text(), nullable=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("hosts", ARRAY(sa.Text()), nullable=False),
        sa.Column("grant_agent", sa.Boolean(), nullable=False),
        sa.Column("grant_watch", sa.Boolean(), nullable=False),
        sa.Column("grant_env", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_credentials"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_credentials_chat_id_chats", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_credentials_created_by_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_credentials_updated_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_credentials_created_by", "credentials", ["created_by"])
    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.func.uuidv7(), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_events_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_events_chat_id_id", "events", ["chat_id", "id"])
    op.create_table(
        "executions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action_id", UUID(as_uuid=True), nullable=True),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("network", sa.Boolean(), nullable=False),
        sa.Column("export", ARRAY(sa.Text()), nullable=False),
        sa.Column("input_artifact_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("host", sa.Text(), nullable=True),
        sa.Column("container_id", sa.Text(), nullable=True),
        sa.Column("stdout", sa.Text(), nullable=False),
        sa.Column("stderr", sa.Text(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("output_artifact_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_executions"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_executions_chat_id_chats", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_executions_created_by_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_executions_chat_id_id", "executions", ["chat_id", "id"])
    op.create_index("ix_executions_created_by", "executions", ["created_by"])
    op.create_index(
        "ix_executions_unfinished",
        "executions",
        ["id"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", UUID(as_uuid=True), nullable=True),
        sa.Column("question_id", UUID(as_uuid=True), nullable=True),
        sa.Column("revision_of", UUID(as_uuid=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("image_artifact_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("file_artifact_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_messages_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_messages_chat_id_id", "messages", ["chat_id", "id"])
    op.create_index(
        "ix_messages_queued",
        "messages",
        ["chat_id"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_table(
        "policy_rules",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tool_pattern", sa.Text(), nullable=False),
        sa.Column("resource_pattern", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("effect", sa.String(length=32), nullable=False),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_policy_rules"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_policy_rules_chat_id_chats", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_policy_rules_created_by_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_policy_rules_updated_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_policy_rules_created_by", "policy_rules", ["created_by"])
    op.create_table(
        "turns",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("final_response", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_manifest", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_turns_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_turns_chat_id_id", "turns", ["chat_id", "id"])
    op.create_table(
        "wakeups",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_wakeups"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_wakeups_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_wakeups_pending",
        "wakeups",
        ["chat_id", "due_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "workspaces",
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("host", sa.Text(), nullable=True),
        sa.Column("workspace_volume", sa.Text(), nullable=True),
        sa.Column("home_volume", sa.Text(), nullable=True),
        sa.Column("holder_id", sa.Text(), nullable=True),
        sa.Column("computer_id", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("checkpoint_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "touched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("checkpoint", sa.Text(), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("chat_id", name="pk_workspaces"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_workspaces_chat_id_chats", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "actions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column("arguments", JSONB(), nullable=False),
        sa.Column("pending_arguments", JSONB(), nullable=True),
        sa.Column("args_hash", sa.Text(), nullable=False),
        sa.Column("resources", JSONB(), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("effect", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", JSONB(), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_actions"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_actions_chat_id_chats", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["turns.id"], name="fk_actions_turn_id_turns", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_actions_chat_id_id", "actions", ["chat_id", "id"])
    op.create_index(
        "ix_actions_pending",
        "actions",
        ["id"],
        postgresql_where=sa.text("status IN ('awaiting_approval', 'suspended')"),
    )
    op.create_index("ix_actions_turn_id", "actions", ["turn_id"])
    op.create_table(
        "questions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("call_id", sa.Text(), nullable=False),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_id", UUID(as_uuid=True), nullable=False),
        sa.Column("questions", JSONB(), nullable=False),
        sa.Column("answers", JSONB(), nullable=True),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("live", sa.Boolean(), nullable=False),
        sa.Column("waiting", sa.Boolean(), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_questions"),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_questions_chat_id_chats", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["turns.id"], name="fk_questions_turn_id_turns", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_questions_chat_id_id", "questions", ["chat_id", "id"])
    op.create_index(
        "uq_questions_chat_id_call_id", "questions", ["chat_id", "call_id"], unique=True
    )
    op.create_table(
        "sources",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", UUID(as_uuid=True), nullable=True),
        sa.Column("chat_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("draft", JSONB(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pause_reason", sa.String(length=32), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["credentials.id"],
            name="fk_sources_credential_id_credentials",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["chats.id"], name="fk_sources_chat_id_chats", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_sources_created_by_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_sources_updated_by_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_sources_created_by", "sources", ["created_by"])
    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        _created(),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_alerts_source_id_sources", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_alerts_updated_by_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_alerts_source_id_id", "alerts", ["source_id", "id"])
    op.create_index(
        "uq_alerts_dedup_key_unresolved",
        "alerts",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=True),
        sa.Column("kinds", ARRAY(sa.Text()), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        _created(),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        _updated(),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_subscriptions_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_subscriptions_created_by_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name="fk_subscriptions_updated_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_subscriptions_created_by", "subscriptions", ["created_by"])
    op.create_table(
        "versions",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("change_count", sa.Integer(), nullable=False),
        sa.Column("structure", JSONB(), nullable=True),
        sa.Column("schema_changes", JSONB(), nullable=False),
        sa.Column("changes", JSONB(), nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_versions"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_versions_source_id_sources", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_versions_source_id_resource_key_id",
        "versions",
        ["source_id", "resource_key", "id"],
    )


def downgrade() -> None:
    """Drop the complete application schema in reverse dependency order."""
    for table in (
        "versions",
        "subscriptions",
        "alerts",
        "sources",
        "questions",
        "actions",
        "workspaces",
        "wakeups",
        "turns",
        "policy_rules",
        "messages",
        "executions",
        "events",
        "credentials",
        "artifacts",
        "sessions",
        "chats",
        "api_keys",
        "workers",
        "users",
        "leases",
        "credential_events",
    ):
        op.drop_table(table)
