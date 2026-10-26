"""Offline routing, secret redaction and read-only probe contracts."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.blog.config import APP_KEYS, BlogConfigurationError, load_registry
from app.blog.database import session_for_app
from app.blog.probe import check_target

URL = "postgresql://writer:fake-password@db.example.test/blog?sslmode=require&charset=utf8"


def configured():
    return {
        "BLOG_AZEEMLAB_DATABASE_URL": URL,
        "BLOG_AILETTERGEN_DATABASE_URL": URL.replace("/blog?", "/letters?"),
    }


def test_six_independent_apps_without_urls_are_unavailable():
    registry = load_registry({})
    assert tuple(target.key for target in registry.targets) == APP_KEYS
    assert all(not target.ready for target in registry.targets)
    with pytest.raises(BlogConfigurationError, match="not configured"):
        registry.for_app("azeemlab")


def test_independent_keys_and_secrets_route_to_distinct_databases():
    registry = load_registry(configured())
    first, second = registry.for_app("azeemlab"), registry.for_app("ailettergen")
    assert "/letters?" in second.database_url.get_secret_value()
    normalized = first.database_url.get_secret_value()
    assert normalized.startswith("postgresql+asyncpg://")
    assert "ssl=require" in normalized and "charset" not in normalized
    assert "fake-password" not in repr(registry)
    assert "fake-password" not in str(first.database_url)


@pytest.mark.parametrize("key", [True, 11, None, "../azeemlab", "AZEEMLAB", "unknown"])
def test_arbitrary_or_legacy_numeric_selectors_fail_closed(key):
    with pytest.raises(BlogConfigurationError):
        load_registry(configured()).for_app(key)


def test_legacy_authorized_app_bindings_are_ignored_and_central_url_never_used():
    values = {**configured(), "BLOG_AZEEMLAB_APP_ID": "invalid", "DATABASE_URL": URL}
    assert load_registry(values).for_app("azeemlab").ready
    with pytest.raises(BlogConfigurationError):
        load_registry({"DATABASE_URL": URL, "BLOG_AZEEMLAB_APP_ID": "11"}).for_app("azeemlab")


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///fake-password",
        "mysql://writer:fake-password@db.example.test/blog",
        "postgresql://writer:fake-password@/blog",
        "postgresql://writer:fake-password@db.example.test",
        "postgresql://writer:fake-password@db.example.test/blog?sslmode=disable",
        "postgresql://writer:fake-password@db.example.test/blog?ssl=require&sslmode=verify-full",
        "postgresql://writer:fake-password@db.example.test/blog?unknown=secret",
        "not-a-url-fake-password",
    ],
)
def test_invalid_urls_are_sanitized_without_parser_exception_context(url):
    values = configured()
    values["BLOG_AZEEMLAB_DATABASE_URL"] = url
    with pytest.raises(BlogConfigurationError) as caught:
        load_registry(values)
    assert "fake-password" not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg"])
def test_supported_urls_preserve_encoded_password_and_enable_tls(scheme):
    values = {"BLOG_AZEEMLAB_DATABASE_URL": f"{scheme}://writer:p%40ss%2Fword@db.example.test/blog"}
    normalized = load_registry(values).for_app("azeemlab").database_url.get_secret_value()
    assert "p%40ss%2Fword" in normalized and "ssl=require" in normalized


def test_environment_overrides_file_without_loading_central_settings(monkeypatch):
    monkeypatch.setenv("BLOG_AZEEMLAB_DATABASE_URL", URL.replace("/blog?", "/override?"))
    with patch("app.blog.config.dotenv_values", return_value=configured()):
        assert "/override?" in load_registry().for_app("azeemlab").database_url.get_secret_value()


@pytest.mark.asyncio
async def test_session_closes_and_engine_disposes_on_mutation_failure():
    session = MagicMock()
    context = MagicMock(
        __aenter__=AsyncMock(return_value=session), __aexit__=AsyncMock(return_value=False)
    )
    engine = MagicMock(dispose=AsyncMock())
    with (
        patch("app.blog.database.create_async_engine", return_value=engine) as create,
        patch("app.blog.database.async_sessionmaker", return_value=lambda: context),
    ):
        with pytest.raises(RuntimeError, match="test failure"):
            async with session_for_app(load_registry(configured()), "ailettergen") as current:
                assert current is session
                raise RuntimeError("test failure")
    assert "/letters?" in create.call_args.args[0]
    statement_name = create.call_args.kwargs["connect_args"]["prepared_statement_name_func"]
    assert statement_name() != statement_name()
    assert statement_name().startswith("__blog_")
    assert create.call_args.kwargs["echo"] is False
    assert create.call_args.kwargs["hide_parameters"] is True
    context.__aexit__.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_unconfigured_target_cannot_create_engine():
    with patch("app.blog.database.create_async_engine") as create:
        with pytest.raises(BlogConfigurationError):
            async with session_for_app(load_registry({}), "azeemlab"):
                pytest.fail("unconfigured storage must be unreachable")
    create.assert_not_called()


@pytest.mark.asyncio
async def test_probe_starts_read_only_and_reports_only_planned_table_names():
    registry = load_registry(configured())
    statements = []

    async def execute(statement):
        statements.append(str(statement))
        value = MagicMock()
        value.scalar_one.return_value = "on"
        value.scalars.return_value.all.return_value = ["blog_posts"]
        return value

    session = MagicMock(execute=AsyncMock(side_effect=execute))

    @asynccontextmanager
    async def fake_session(*args):
        yield session

    with patch("app.blog.probe.session_for_app", fake_session):
        result = await check_target(registry, registry.for_app("azeemlab"))
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert all(sql.startswith(("SET TRANSACTION READ ONLY", "SELECT")) for sql in statements)
    assert result["status"] == "connected_read_only"
    assert result["missing_blog_tables"] == ["blog_categories", "blog_assets"]
    assert "fake-password" not in repr(result)
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_probe_error_is_sanitized_and_blank_targets_never_connect():
    registry = load_registry(configured())

    @asynccontextmanager
    async def fake_session(*args):
        raise RuntimeError("fake-password raw server details")
        yield

    with patch("app.blog.probe.session_for_app", fake_session):
        result = await check_target(registry, registry.for_app("azeemlab"))
    assert result == {
        "key": "azeemlab",
        "configured": True,
        "status": "failed",
        "error_type": "RuntimeError",
    }
    with patch("app.blog.probe.session_for_app") as connect:
        blank = next(target for target in registry.targets if target.key == "sikanderart")
        assert (await check_target(registry, blank))["status"] == "not_configured"
        connect.assert_not_called()
