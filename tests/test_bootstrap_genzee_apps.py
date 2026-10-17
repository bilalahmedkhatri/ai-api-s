import io

import pytest
from app.models.db_models import AuthorizedApp, User
from app.services.api_key_service import verify_api_key
from dotenv import dotenv_values
from scripts.bootstrap_genzee_apps import (
    GENZEE_ENDPOINTS,
    configure_apps,
    environment_content,
    write_environment,
)
from sqlalchemy import select


async def test_onboarding_reuses_apps_and_keys_and_separates_origins(onboarding_db):
    db, owner, _, _ = onboarding_db
    owner.api_access_enabled = False
    _, first, created = await configure_apps(db, owner.email, {}, set(GENZEE_ENDPOINTS))
    assert owner.api_access_enabled and len(created) == 2
    existing = {name: item['raw_key'] for name, item in first.items()}
    _, second, created_again = await configure_apps(db, owner.email, existing, set(GENZEE_ENDPOINTS))
    assert created_again == []
    assert {name: item['key_id'] for name, item in second.items()} == {name: item['key_id'] for name, item in first.items()}
    local, production = second['local'], second['production']
    assert local['raw_key'] != production['raw_key']
    assert await verify_api_key(db, local['raw_key'], local['app'].allowed_origin)
    assert await verify_api_key(db, local['raw_key'], production['app'].allowed_origin)


@pytest.mark.parametrize('email,permissions', [('missing@example.test', set(GENZEE_ENDPOINTS)), ('owner@example.test', set())])
async def test_invalid_setup_does_not_grant_permission(onboarding_db, email, permissions):
    db, owner, _, _ = onboarding_db
    owner.api_access_enabled = False
    await db.commit()
    with pytest.raises(ValueError):
        await configure_apps(db, email, {}, permissions)
    assert not owner.api_access_enabled
    assert len((await db.execute(select(AuthorizedApp))).scalars().all()) == 2


def test_environment_update_preserves_other_settings_and_parses():
    content = 'OTHER_SETTING="keep"\nGATEWAY_API_KEY="old"\n'
    updated = environment_content(content, {'GATEWAY_API_KEY': 'new-test-key', 'GATEWAY_APP_ORIGIN': 'http://localhost:3000'})
    updated = environment_content(updated, {'GATEWAY_API_KEY': 'new-test-key'})
    values = dotenv_values(stream=io.StringIO(updated))
    assert values['OTHER_SETTING'] == 'keep'
    assert values['GATEWAY_API_KEY'] == 'new-test-key'
    assert values['GATEWAY_APP_ORIGIN'] == 'http://localhost:3000'
    assert updated.count('GATEWAY_API_KEY=') == 1


async def test_conflicting_origin_does_not_grant_new_owner(onboarding_db):
    db, _, apps, _ = onboarding_db
    apps[0].allowed_origin = 'http://localhost:3000'
    other = User(google_id='other-owner', email='other@example.test', is_active=True, api_access_enabled=False)
    db.add(other)
    await db.commit()
    with pytest.raises(ValueError, match='different or ambiguous'):
        await configure_apps(db, other.email, {}, set(GENZEE_ENDPOINTS))
    assert not other.api_access_enabled
    assert len((await db.execute(select(AuthorizedApp))).scalars().all()) == 2


def test_atomic_environment_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / '.env.local'
    path.write_text('OTHER_SETTING=keep\n', encoding='utf-8')
    write_environment(path, {'GATEWAY_API_KEY': 'isolated-fake-key'})
    assert dotenv_values(path)['OTHER_SETTING'] == 'keep'
    assert dotenv_values(path)['GATEWAY_API_KEY'] == 'isolated-fake-key'
    assert list(tmp_path.iterdir()) == [path]
