"""Add shared endpoint configuration, preserving every legacy model and FK."""

import json

import sqlalchemy as sa
from alembic import op

revision = "e610a2c947bf"
down_revision = "d920f41a672e"
branch_labels = None
depends_on = None


def _credential_free(value):
    if isinstance(value, dict):
        forbidden = {"api_key", "api_token", "token", "secret", "password", "authorization",
                     "access_token", "refresh_token", "encrypted_key", "replicate_api_token"}
        if any(key.lower() in forbidden for key in value):
            raise RuntimeError("Legacy configuration contains inline credentials; move them to the key pool before migration")
        for item in value.values():
            _credential_free(item)
    elif isinstance(value, list):
        for item in value:
            _credential_free(item)


def upgrade():
    # Validate before DDL too, so SQLite failures cannot leave a half-created table.
    bind = op.get_bind()
    names = {row.name: row.provider.strip().lower() for row in bind.execute(sa.text("SELECT name, provider FROM ai_models"))}
    for table in ("tts_models", "media_models"):
        for row in bind.execute(sa.text("SELECT * FROM " + table)).mappings():
            provider = row["provider"].strip().lower()
            if row["name"] in names and names[row["name"]] != provider:
                raise RuntimeError(f"Provider collision for legacy {table} ID {row['id']}; resolve before migration")
            names[row["name"]] = provider
            for key, value in row.items():
                if key.endswith("_json"):
                    _credential_free(json.loads(value or "{}"))
    op.create_table(
        "model_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ai_model_id", sa.Integer(), sa.ForeignKey("ai_models.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("model_type", sa.String(16), nullable=False),
        sa.Column("adapter", sa.String(16), nullable=False),
        sa.Column("endpoint_url", sa.String(2048)),
        sa.Column("api_base_url", sa.String(2048)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("legacy_tts_model_id", sa.Integer(), sa.ForeignKey("tts_models.id", ondelete="RESTRICT"), unique=True),
        sa.Column("legacy_media_model_id", sa.Integer(), sa.ForeignKey("media_models.id", ondelete="RESTRICT"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ai_model_id", "model_type", name="uq_model_endpoint_type"),
        sa.CheckConstraint("model_type IN ('chat','tts','image','video','embedding')", name="ck_endpoint_type"),
        sa.CheckConstraint("adapter IN ('litellm','http','local')", name="ck_endpoint_adapter"),
        sa.CheckConstraint("NOT (legacy_tts_model_id IS NOT NULL AND legacy_media_model_id IS NOT NULL)",
                           name="ck_endpoint_legacy_source"),
    )
    op.create_index("ix_model_endpoints_ai_model_id", "model_endpoints", ["ai_model_id"])
    bind = op.get_bind()
    metadata = sa.MetaData()
    models = sa.Table("ai_models", metadata, autoload_with=bind)
    endpoints = sa.Table("model_endpoints", metadata, autoload_with=bind)
    for table_name in ("tts_models", "media_models"):
        legacy = sa.Table(table_name, metadata, autoload_with=bind)
        for row in bind.execute(sa.select(legacy)).mappings().all():
            catalog = bind.execute(sa.select(models).where(models.c.name == row["name"])).mappings().one_or_none()
            if catalog is not None and catalog["provider"].strip().lower() != row["provider"].strip().lower():
                raise RuntimeError(f"Provider collision for legacy {table_name} ID {row['id']}; resolve before migration")
            if catalog is None:
                model_id = bind.execute(models.insert().values(name=row["name"], provider=row["provider"].strip().lower(),
                                        is_active=row["is_active"], is_free=False,
                                        prompt_price_per_1k=0, completion_price_per_1k=0)
                                        .returning(models.c.id)).scalar_one()
            else:
                model_id = catalog["id"]
            if table_name == "tts_models":
                config = {key: row[key] for key in ("is_local", "default_voice", "default_lang", "default_speed")}
                config["provider_config"] = json.loads(row["provider_config_json"] or "{}")
                values = {"model_type": "tts", "adapter": "local" if row["is_local"] else "http",
                          "endpoint_url": row["api_endpoint_url"], "legacy_tts_model_id": row["id"]}
            else:
                excluded = {"id", "name", "provider", "media_type", "is_active", "created_at", "api_base_url",
                            "api_endpoint_path", "default_params_json", "response_extraction_config_json"}
                config = {key: value for key, value in row.items() if key not in excluded}
                config["default_params"] = json.loads(row["default_params_json"] or "{}")
                config["response_extraction_config"] = json.loads(row["response_extraction_config_json"] or "{}")
                base, path = row["api_base_url"], row["api_endpoint_path"]
                values = {"model_type": row["media_type"], "adapter": "http", "api_base_url": base,
                          "endpoint_url": (base or "") + path if path else base,
                          "legacy_media_model_id": row["id"]}
            _credential_free(config)
            bind.execute(endpoints.insert().values(ai_model_id=model_id, is_active=row["is_active"],
                                                   config_json=json.dumps(config), **values))


def downgrade():
    # Catalog imports may now own usage/history; never delete them on rollback.
    op.drop_index("ix_model_endpoints_ai_model_id", table_name="model_endpoints")
    op.drop_table("model_endpoints")
