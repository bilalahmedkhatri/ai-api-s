"""Apply TTS live-preview metadata and append GenZee streaming grants.

Never issues keys, assigns legacy jobs, changes owner permissions or calls providers.
"""

import argparse
import asyncio
import json
import logging

from alembic import command
from alembic.config import Config
from app.db.database import AsyncSessionLocal, engine
from app.models.db_models import AuthorizedApp, User
from sqlalchemy import inspect, select, text

REVISION = 'f53c9e012d74'
PARENT = 'e42b8d901c63'
GRANT = 'GET /api/v1/audio/tts/stream/{job_id}'
ORIGINS = {'http://localhost:3000', 'https://www.genzee.video'}


async def check(owner_email):
    try:
        async with AsyncSessionLocal() as db:
            current = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalars().all()
            if current not in ([PARENT], [REVISION]):
                raise ValueError('Database must already be at the TTS checkpoint revision')
            owner = (await db.execute(select(User).where(User.email == owner_email))).scalar_one_or_none()
            if not owner:
                raise ValueError('Expected FastAPI owner was not found')
            registered = (await db.execute(select(AuthorizedApp).where(AuthorizedApp.allowed_origin.in_(ORIGINS)))).scalars().all()
            if len(registered) != 2 or any(a.owner_user_id != owner.id for a in registered):
                raise ValueError('Both exact origins must belong to the expected FastAPI owner')
            for app in registered:
                grants = json.loads(app.allowed_endpoints_json)
                if not isinstance(grants, list):
                    raise ValueError('Unexpected app grant format')
            return current[0]
    finally:
        await engine.dispose()


async def grant_and_verify(owner_email):
    try:
        async with AsyncSessionLocal() as db:
            owner = (await db.execute(select(User).where(User.email == owner_email))).scalar_one()
            registered = (await db.execute(select(AuthorizedApp).where(AuthorizedApp.allowed_origin.in_(ORIGINS)))).scalars().all()
            if len(registered) != 2 or any(a.owner_user_id != owner.id for a in registered):
                raise ValueError('App ownership changed during migration')
            for app in registered:
                grants = json.loads(app.allowed_endpoints_json)
                if GRANT not in grants:
                    app.allowed_endpoints_json = json.dumps([*grants, GRANT])
            await db.commit()
            version = (await db.execute(text('SELECT version_num FROM alembic_version'))).scalar_one()
            async with engine.connect() as connection:
                def schema_check(sync):
                    schema = inspect(sync)
                    columns = {c['name'] for c in schema.get_columns('tts_jobs')}
                    return {'generation_json', 'authorized_app_id', 'lease_until', 'run_token'} <= columns and schema.has_table('tts_chunks') and 'frame_count' in {c['name'] for c in schema.get_columns('tts_chunks')}
                schema_ok = await connection.run_sync(schema_check)
            if version != REVISION or not schema_ok:
                raise ValueError('Post-migration schema verification failed')
            return {'revision': version, 'schema_verified': schema_ok,
                    'apps': [{'app_id': a.id, 'origin': a.allowed_origin,
                              'stream_granted': GRANT in json.loads(a.allowed_endpoints_json)} for a in registered]}
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-email', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        current = asyncio.run(check(args.owner_email))
        if args.check:
            print(json.dumps({'current_revision': current, 'target_revision': REVISION, 'owner_apps_verified': True}))
            return
        if current != REVISION:
            command.upgrade(Config('alembic.ini'), REVISION)
        logging.disable(logging.CRITICAL)
        print(json.dumps(asyncio.run(grant_and_verify(args.owner_email)), indent=2))
    except Exception as exc:
        # Connection exceptions can contain production credentials; do not print them.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
