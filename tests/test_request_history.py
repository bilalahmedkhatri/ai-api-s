"""Mixed-source admin history: isolated SQL, no providers/storage/configured DB."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.models.db_models import (
    AuthorizedApp,
    FinalResponse,
    MediaGeneration,
    MediaModel,
    Query,
    TTSChunk,
    TTSJob,
    User,
)
from app.services.api_key_service import generate_api_key
from app.services.request_history import daily_statement, projection
from httpx import ASGITransport, AsyncClient
from main import app
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import asyncpg

pytest_plugins = ["tests.test_dashboard"]
BASE = "/api/v1/dashboard/requests"


@pytest.fixture
async def history(dashboard):
    db, member, headers = dashboard
    date = datetime(2026, 10, 19, 10, tzinfo=UTC)
    projects = []
    for name in ("a", "b"):
        project = AuthorizedApp(name=name, owner_user_id=member.user_id, allowed_origin="")
        db.add(project)
        await db.flush()
        projects.append(project)
    query = Query(raw_query="failed prompt", authorized_app_id=projects[0].id, created_at=date)
    query.meta = {"status": "failed", "duration_ms": 500, "api_key": "DO-NOT-EXPOSE",
                  "usage_accounting_status": "not_applicable", "error_type": "ProviderFailure",
                  "known_provider_cost_usd": 0.03, "provider_cost_complete": False,
                  "provider_calls": [{"kind": "chat", "model": "first", "status": "failed",
                                      "prompt_tokens": 20, "cost_usd": 0.03,
                                      "api_key": "DO-NOT-EXPOSE"}]}
    db.add(query)
    direct = Query(raw_query="completed prompt", authorized_app_id=projects[1].id, created_at=date)
    direct.meta = {"status": "completed", "request_type": "generate", "duration_ms": 100,
                   "known_provider_cost_usd": 0.02, "provider_cost_complete": True,
                   "provider_calls": [{"kind": "chat", "model": "second", "status": "completed",
                                       "prompt_tokens": 10, "completion_tokens": 4, "cost_usd": 0.02}]}
    db.add(direct)
    await db.flush()
    # Latest final only: earlier response must not double counts, tokens or filters.
    db.add(FinalResponse(query_id=direct.id, generated_output="old", model_used="old", cost_usd=20))
    final = FinalResponse(query_id=direct.id, generated_output="answer", model_used="actual-model",
                          prompt_tokens=10, completion_tokens=4, cost_usd=0.02)
    final.meta = {"model_attempts": [{"model": "first", "provider": "bad", "status": "failed"},
                                      {"model": "second", "provider": "good", "status": "completed"}]}
    db.add(final)
    job = TTSJob(job_id="tts-fixture", text="voice prompt", status="completed", created_at=date,
                 authorized_app_id=projects[0].id, duration_sec=44, size_bytes=200,
                 generation_json=json.dumps({"model_snapshot": {"name": "gemini-tts", "provider": "gemini",
                                                                "provider_config": {"secret": "DO-NOT-EXPOSE"}},
                                             "voice": "Fenrir"}), run_token="DO-NOT-EXPOSE",
                 object_key="DO-NOT-EXPOSE", audio_url="https://private.test/?secret=DO-NOT-EXPOSE")
    db.add(job)
    await db.flush()
    db.add(TTSChunk(job_id=job.job_id, chunk_index=0, text="voice prompt", status="completed",
                    attempts=2, object_key="DO-NOT-EXPOSE"))
    model = MediaModel(name="image-model", provider="replicate", media_type="image")
    db.add(model)
    await db.flush()
    media = MediaGeneration(model_id=model.id, user_id=member.user_id, prompt="image prompt",
                            status="timeout", elapsed_ms=900, created_at=date,
                            result_urls='["https://private.test/?secret=DO-NOT-EXPOSE"]',
                            error_message="DO-NOT-EXPOSE")
    db.add(media)
    # Exact upper boundary is excluded; older default range also excluded.
    db.add(TTSJob(job_id="boundary", text="excluded", status="processing", created_at=date + timedelta(days=1)))
    await db.commit()
    filters = "date_from=2026-10-19T00:00:00Z&date_to=2026-10-20T00:00:00Z"
    return db, member, headers, filters, query, direct, job, media, projects


async def test_mixed_pagination_summary_and_filters(history):
    db, _, headers, filters, query, direct, job, media, projects = history
    before = db.session.execute(select(Query.id, Query.meta_fields)).all()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(f"{BASE}?{filters}&page_size=2", headers=headers)
        second = await client.get(f"{BASE}?{filters}&page_size=2&page=2", headers=headers)
        assert first.status_code == second.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        items = first.json()["items"] + second.json()["items"]
        assert first.json()["total"] == 4
        assert len({r["record_id"] for r in items}) == 4
        assert [r["record_id"] for r in items] == sorted([r["record_id"] for r in items], reverse=True)
        assert "DO-NOT-EXPOSE" not in first.text + second.text
        complete = await client.get(f"{BASE}?{filters}&provider=good", headers=headers)
        assert complete.json()["total"] == 1
        assert complete.json()["items"][0]["record_id"] == f"query:{direct.id}"
        summary = await client.get(f"{BASE}/summary?{filters}", headers=headers)
        assert summary.status_code == 200 and summary.headers["cache-control"] == "no-store"
        data = summary.json()
        assert data["requests"] == 4 and data["duration_samples"] == 3
        assert data["average_duration_ms"] == 500
        assert data["success_rate"] == 50
        assert data["known_provider_cost_usd"] == pytest.approx(0.05) and data["cost_samples"] == 2
        assert "cost_scope" in data
        assert data["token_scope"] == "latest_final_response_not_all_provider_attempts"
        assert (data["recorded_input_tokens"], data["recorded_output_tokens"]) == (10, 4)
        for params, expected in [("request_type=tts", 1), ("status=failed", 2),
                                 ("model=old", 0), (f"authorized_app_id={projects[0].id}", 2),
                                 (f"authorized_app_id={projects[1].id}", 1),
                                 ("search=tts-fixture", 1), ("search=replicate", 1),
                                 ("search=%25", 0), ("search=actual-model", 1)]:
            listing = (await client.get(f"{BASE}?{filters}&{params}", headers=headers)).json()
            stats = (await client.get(f"{BASE}/summary?{filters}&{params}", headers=headers)).json()
            assert listing["total"] == stats["requests"] == expected
    assert before == db.session.execute(select(Query.id, Query.meta_fields)).all()


async def test_detail_failure_usage_and_tts_children(history):
    _, _, headers, _, query, direct, job, media, _ = history
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.get(f"{BASE}/query:{query.id}", headers=headers)
        assert failed.status_code == 200
        data = failed.json()
        assert data["status"] == "failed" and data["customer_chargeable_tokens"] == 0
        assert data["attempts"][0]["prompt_tokens"] == 20
        assert data["known_provider_cost_usd"] == 0.03
        assert data["external_user_id"] is None
        successful = (await client.get(f"{BASE}/query:{direct.id}", headers=headers)).json()
        assert successful["output"] == "answer" and successful["provider"] == "good"
        assert len(successful["attempts"]) == 1 and successful["attempts_source"] == "provider_calls"
        assert successful["customer_chargeable_tokens"] is None
        voice = await client.get(f"{BASE}/tts:{job.job_id}", headers=headers)
        assert voice.json()["voice"] == "Fenrir" and voice.json()["audio_duration_seconds"] == 44
        assert voice.json()["duration_ms"] is None and voice.json()["input_tokens"] is None
        assert voice.json()["chunks"] == [{"index": 0, "status": "completed", "recorded_chunk_attempts": 2, "has_error": False}]
        image = await client.get(f"{BASE}/media:{media.id}", headers=headers)
        assert image.json()["status"] == "failed" and image.json()["source_status"] == "timeout"
        assert image.json()["authorized_app_id"] is None
        for response in (failed, voice, image):
            assert "DO-NOT-EXPOSE" not in response.text
            assert response.headers["cache-control"] == "no-store"
        for identifier in ("query:no-number", "unknown:1", "media:0", "tts:missing"):
            assert (await client.get(f"{BASE}/{identifier}", headers=headers)).status_code == 404


@pytest.mark.parametrize("params", ["page=0", "page_size=101", "request_type=bad", "status=bad",
    "authorized_app_id=0", "date_from=2026-10-19T00:00:00", "date_from=2024-01-01T00:00:00Z",
    "date_from=2026-10-20T00:00:00Z&date_to=2026-10-19T00:00:00Z"])
async def test_invalid_filters(history, params):
    _, _, headers, _, *_ = history
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"{BASE}?{params}", headers=headers)).status_code == 422


async def test_auth_independently_on_all_routes(history):
    db, member, headers, filters, *_, projects = history
    (await db.get(User, member.user_id)).api_access_enabled = True
    projects[0].is_approved = True
    raw_key, _ = await generate_api_key(db, "app-only", projects[0].id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (f"{BASE}?{filters}", f"{BASE}/summary?{filters}", f"{BASE}/tts:tts-fixture"):
            assert (await client.get(path)).status_code == 401
            assert (await client.get(path, headers={"X-API-Key": raw_key})).status_code == 401
        member.role = "viewer"
        await db.commit()
        for path in (BASE, f"{BASE}/summary", f"{BASE}/tts:tts-fixture"):
            assert (await client.get(path, headers=headers)).status_code == 403


async def test_empty_history_and_timezone_boundaries(history):
    _, _, headers, _, *_ = history
    dates = "date_from=2026-10-19T15:00:00%2B05:00&date_to=2026-10-19T16:00:00%2B05:00"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listing = (await client.get(f"{BASE}?{dates}", headers=headers)).json()
        assert listing["total"] == 4
        assert listing["date_from"].startswith("2026-10-19T10:00:00")
        dates = "date_from=2026-10-19T11:00:00Z&date_to=2026-10-19T12:00:00Z"
        empty = (await client.get(f"{BASE}?{dates}", headers=headers)).json()
        summary = (await client.get(f"{BASE}/summary?{dates}", headers=headers)).json()
        assert empty["items"] == [] and summary["requests"] == 0
        assert summary["complete_cost_requests"] == summary["cost_samples"] == 0
        assert summary["success_rate"] is summary["known_provider_cost_usd"] is None
        assert summary["average_duration_ms"] is None


async def test_video_unknown_and_legacy_null_records(history):
    db, _, headers, filters, *_ = history
    date = datetime(2026, 10, 19, 10, tzinfo=UTC)
    model = MediaModel(name="video-model", provider="replicate", media_type="video")
    db.add(model)
    await db.flush()
    db.add(MediaGeneration(model_id=model.id, prompt="video", status="success", created_at=date))
    db.add(TTSJob(job_id="old-without-snapshot", text="old", status="unexpected", created_at=date))
    db.add(Query(raw_query="old-query", created_at=date))
    await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        video = (await client.get(f"{BASE}?{filters}&request_type=video", headers=headers)).json()
        assert video["total"] == 1 and video["items"][0]["status"] == "completed"
        unknown = (await client.get(f"{BASE}?{filters}&status=unknown", headers=headers)).json()
        assert unknown["total"] == 2
        old = (await client.get(f"{BASE}/tts:old-without-snapshot", headers=headers)).json()
        assert old["model"] is old["known_provider_cost_usd"] is old["authorized_app_id"] is None
        assert old["chunks"] == []


def test_postgres_projection_compiles():
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()))
    sql = str(select(projection(db)).compile(dialect=postgresql.dialect()))
    assert "UNION ALL" in sql and "jsonb_path_query_first" in sql


def test_postgres_union_nulls_do_not_share_typed_parameters():
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=asyncpg.dialect()))
    compiled = select(projection(db)).compile(dialect=asyncpg.dialect())
    # asyncpg infers one type per bind; a shared None cannot represent both float and boolean.
    assert None not in compiled.params.values()
    assert "NULL AS input_tokens" in str(compiled)


async def test_daily_series_counts_nulls_and_weighted_timing(history):
    db, _, headers, _, *_ = history
    query = Query(raw_query="next-day", created_at=datetime(2026, 10, 20, 13, tzinfo=UTC))
    query.meta = {"status": "completed", "duration_ms": 1000}
    db.add(query)
    await db.commit()
    dates = "date_from=2026-10-18T00:00:00Z&date_to=2026-10-21T00:00:00Z"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        data = (await client.get(f"{BASE}/summary?{dates}", headers=headers)).json()
        assert [p["day"] for p in data["series"]] == ["2026-10-18", "2026-10-19", "2026-10-20"]
        empty, measured, last = data["series"]
        assert empty["requests"] == empty["completed"] == empty["failed"] == 0
        assert empty["success_rate"] is empty["average_duration_ms"] is empty["known_provider_cost_usd"] is None
        assert empty["recorded_input_tokens"] is empty["recorded_output_tokens"] is None
        assert measured["requests"] == 4 and measured["completed"] == measured["failed"] == 2
        assert measured["success_rate"] == 50 and measured["duration_samples"] == 3
        assert measured["recorded_input_tokens"] == 10 and measured["input_token_samples"] == 1
        assert measured["known_provider_cost_usd"] == pytest.approx(0.05)
        assert last["requests"] == 2 and last["success_rate"] == 100
        assert data["requests"] == sum(p["requests"] for p in data["series"]) == 6
        assert data["average_duration_ms"] == 625  # Not the unweighted daily mean, 750.
        assert data["success_rate"] == 60
        filtered = (await client.get(f"{BASE}/summary?{dates}&provider=good", headers=headers)).json()
        assert filtered["requests"] == sum(p["requests"] for p in filtered["series"]) == 1
        assert filtered["series"][1]["known_provider_cost_usd"] == 0.02


async def test_partial_utc_buckets_and_maximum_range(history):
    _, _, headers, _, *_ = history
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dates = "date_from=2026-10-19T15:00:00%2B05:00&date_to=2026-10-19T16:00:00%2B05:00"
        data = (await client.get(f"{BASE}/summary?{dates}", headers=headers)).json()
        assert len(data["series"]) == 1 and data["series"][0]["requests"] == 4
        dates = "date_from=2026-01-01T12:00:00Z&date_to=2027-01-02T12:00:00Z"
        data = (await client.get(f"{BASE}/summary?{dates}", headers=headers)).json()
        assert len(data["series"]) == 367  # 366 days with partial first/last UTC days.
        invalid = await client.get(f"{BASE}/summary?date_from=2026-01-01T00:00:00Z&date_to=2027-01-03T00:00:00Z", headers=headers)
        assert invalid.status_code == 422


def test_postgres_daily_grouping_reuses_timezone_bind():
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()))
    compiled = daily_statement(db, projection(db)).compile(dialect=postgresql.dialect())
    assert [name for name in compiled.params if name.startswith("timezone_")] == ["timezone_1"]
    assert str(compiled).count("%(timezone_1)s") == 2
