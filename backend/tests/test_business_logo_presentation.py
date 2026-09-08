from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import httpx
import pytest

os.environ.setdefault('AIBOS_DATABASE_URL', 'postgresql+asyncpg://database.invalid/test')
os.environ.setdefault('AIBOS_AUTH_SECRET_KEY', 'x' * 32)

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.ai_agent import get_ai_agent_provider
from app.api.dependencies.creative import get_creative_generation_provider
from app.core.config import settings
from app.db.session import get_db_session
from app.exceptions.business import BusinessBrandingPersistenceError
from app.main import app
from app.models.business_branding import BusinessBranding
from app.schemas.business import BusinessOnboardingInput
from app.services.business_branding import materialize_business_branding_response
from app.services.marketing import _creative_logo_content
from app.storage.base import StorageOperationError
from app.storage.factory import get_object_storage
from app.storage.local import LocalObjectStorage
from app.storage.s3 import S3ObjectStorage
import test_api_business_logo as logo_fixtures
import test_api_business_onboarding as onboarding_fixtures
import test_chatbot_services as widget_fixtures
import test_storage as storage_fixtures

BUSINESS_ID = UUID('24ee3b0f-f523-492f-beac-e5e801766f88')
KEY = f'businesses/{BUSINESS_ID}/branding/logo/1ad2ab01117146fcbaa5f601f797e9f8.png'
STALE_URL = f'/api/v1/media/{KEY}'
INVALID_KEYS = (
    f'businesses/{uuid4()}/branding/logo/logo.png',
    f'businesses/{BUSINESS_ID}/branding/logo-other/logo.png',
    f'businesses/{BUSINESS_ID}/marketing/logo.png',
    f'businesses/{BUSINESS_ID}/branding/logo/../logo.png',
    f'businesses/{BUSINESS_ID}/branding/logo/nested/logo.png',
    f'businesses/{BUSINESS_ID}/branding/logo//logo.png',
    f'businesses/{BUSINESS_ID}/branding/logo/./logo.png',
    f'businesses/{BUSINESS_ID}/branding/logo/',
    KEY + '?key=foreign', KEY + '#fragment', KEY + '\\other.png',
    KEY + '\x00', KEY + '\n', KEY + '/extra', KEY + '..png',
    KEY.replace('1ad2', '%2e%2e%2f'), KEY.replace('1ad2', '%252e%252e%252f'),
    '/' + KEY, 'https://attacker.example/logo.png', '', None,
)


def make_storage():
    client = storage_fixtures._FakeS3Client()
    storage = S3ObjectStorage(
        bucket='private-media', public_base_url='https://media.example.test',
        region='ap-southeast-1', endpoint_url=None,
        access_key_id='test-key', secret_access_key='test-secret', client=client,
    )
    return storage, client


def branding(key=KEY, url=STALE_URL, business_id=BUSINESS_ID):
    return BusinessBranding(
        business_id=business_id, logo_storage_key=key, logo_url=url,
        primary_color='#123456', secondary_color=None, accent_color=None,
    )


def project(value, storage):
    return materialize_business_branding_response(
        value, business_id=BUSINESS_ID, storage=storage, signed_url_ttl_seconds=900,
    )


def durable_state(value):
    return {column.key: getattr(value, column.key) for column in value.__table__.columns}


def test_s3_stale_row_gets_temporary_url_without_persistence_or_object_io():
    storage, client = make_storage()
    value = branding()
    before = durable_state(value)
    result = project(value, storage)
    assert result.logo_url == f'https://objects.example.test/private-media/{KEY}?X-Amz-Expires=900'
    assert result.primary_color == '#123456'
    assert 'logo_storage_key' not in result.model_dump()
    assert durable_state(value) == before
    assert client.presign_calls == [(('get_object',), {
        'Params': {'Bucket': 'private-media', 'Key': KEY}, 'ExpiresIn': 900,
    })]
    assert client.put_calls == client.get_calls == client.delete_calls == []
    # No fabricated object and no object existence check during projection.
    assert client.objects == {}


@pytest.mark.parametrize('key', INVALID_KEYS)
def test_invalid_or_missing_key_never_signs_or_returns_stale_url(key):
    storage, client = make_storage()
    value = branding(key=key)
    before = durable_state(value)
    assert project(value, storage).logo_url is None
    assert client.presign_calls == []
    assert durable_state(value) == before


@pytest.mark.parametrize('url', [STALE_URL, 'https://attacker.example/logo.png', 'data:image/png;base64,abc', 'https://media.example.test/' + KEY])
def test_logo_url_alone_cannot_establish_ownership(url):
    storage, client = make_storage()
    assert project(branding(key=None, url=url), storage).logo_url is None
    assert client.presign_calls == []


def test_valid_key_is_authoritative_even_when_logo_url_is_external():
    storage, client = make_storage()
    assert project(branding(url='https://attacker.example/a.png'), storage).logo_url
    assert client.presign_calls[0][1]['Params']['Key'] == KEY


def test_foreign_branding_row_fails_before_signing():
    storage, client = make_storage()
    with pytest.raises(BusinessBrandingPersistenceError):
        project(branding(business_id=uuid4()), storage)
    assert client.presign_calls == []


def test_missing_branding_does_not_sign():
    storage, client = make_storage()
    assert all(value is None for value in project(None, storage).model_dump().values())
    assert client.presign_calls == []


def test_signer_failure_preserves_durable_values_and_hides_stale_url():
    storage, _ = make_storage()
    value = branding()
    before = durable_state(value)
    with patch.object(storage, 'presentation_url', side_effect=StorageOperationError('private details')):
        result = project(value, storage)
    assert result.logo_url is None
    assert 'private details' not in result.model_dump_json()
    assert durable_state(value) == before


def test_local_logo_uses_existing_media_route():
    with tempfile.TemporaryDirectory() as directory:
        storage = LocalObjectStorage(Path(directory), '/api/v1/media')
        value = branding(url='https://old.invalid/logo.png')
        assert project(value, storage).logo_url == STALE_URL
        assert value.logo_url == 'https://old.invalid/logo.png'


@pytest.mark.asyncio
@pytest.mark.parametrize('key', INVALID_KEYS)
async def test_creative_logo_loading_rejects_invalid_keys_before_object_access(key):
    storage = Mock()
    storage.get = AsyncMock()
    assert await _creative_logo_content(storage, business_id=BUSINESS_ID, branding=branding(key=key)) is None
    storage.get.assert_not_awaited()
    storage.presentation_url.assert_not_called()


@pytest.mark.asyncio
async def test_creative_logo_reads_object_bytes_without_browser_urls():
    storage = Mock()
    storage.get = AsyncMock(return_value=logo_fixtures._image_bytes('PNG'))
    result = await _creative_logo_content(storage, business_id=BUSINESS_ID, branding=branding())
    assert result
    storage.get.assert_awaited_once_with(KEY, max_bytes=logo_fixtures.MAX_LOGO_UPLOAD_BYTES)
    storage.presentation_url.assert_not_called()
    storage.public_url.assert_not_called()


class BusinessLogoPresentationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = logo_fixtures._make_user('owner@example.test')
        self.business = logo_fixtures._make_business('Tenant A', 'tenant-a')
        self.other_business = logo_fixtures._make_business('Tenant B', 'tenant-b')
        self.membership = logo_fixtures._make_membership(self.business, self.user)
        self.key = f'businesses/{self.business.id}/branding/logo/old.png'
        self.branding = branding(key=self.key, url=f'/api/v1/media/{self.key}', business_id=self.business.id)
        self.session = logo_fixtures._FakeAsyncSession(
            businesses=[self.business, self.other_business], memberships=[self.membership],
            events=[],
        )
        self.session.brandings.append(self.branding)
        self.session.capture_committed_state()
        self.storage, self.s3 = make_storage()
        self.overrides = app.dependency_overrides.copy()
        async def db():
            yield self.session
        self.no_provider = Mock(side_effect=AssertionError('No AI provider may be called'))
        app.dependency_overrides.update({
            get_current_user: lambda: self.user,
            get_db_session: db,
            get_object_storage: lambda: self.storage,
            get_ai_agent_provider: self.no_provider,
            get_creative_generation_provider: self.no_provider,
        })
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver')
        self.path = f'/api/v1/businesses/{self.business.id}/branding'

    async def asyncTearDown(self):
        self.no_provider.assert_not_called()
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)

    def assert_presented(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'X-Amz-Expires={settings.storage_signed_url_ttl_seconds}', response.json()['logo_url'])
        self.assertNotIn('logo_storage_key', response.json())
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    async def test_get_projects_stale_logo_without_committing_or_writing(self):
        before = durable_state(self.branding)
        self.assert_presented(await self.client.get(self.path))
        self.assertEqual(durable_state(self.branding), before)
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.s3.put_calls + self.s3.get_calls + self.s3.delete_calls, [])

    async def test_put_preserves_logo_and_projects_after_commit(self):
        old_url = self.branding.logo_url
        with patch.object(self.storage, 'presentation_url', wraps=self.storage.presentation_url) as signer:
            response = await self.client.put(self.path, json={'primary_color': '#ABCDEF'})
        self.assert_presented(response)
        signer.assert_called_once_with(self.key, expires_in_seconds=settings.storage_signed_url_ttl_seconds)
        self.assertEqual(self.branding.logo_url, old_url)
        self.assertEqual(self.branding.logo_storage_key, self.key)
        self.assertEqual(self.branding.primary_color, '#ABCDEF')
        self.assertEqual(self.session.commit_calls, 1)
        self.assertEqual(self.s3.put_calls + self.s3.get_calls + self.s3.delete_calls, [])

    async def test_upload_persists_canonical_reference_but_returns_signed_url(self):
        response = await self.client.post(self.path + '/logo', files={
            'file': ('logo.png', logo_fixtures._image_bytes('PNG'), 'image/png'),
        })
        self.assert_presented(response)
        key = self.branding.logo_storage_key
        self.assertNotEqual(key, self.key)
        self.assertEqual(self.branding.logo_url, self.storage.public_url(key))
        self.assertNotIn('X-Amz', self.branding.logo_url)
        self.assertEqual(self.s3.put_calls[0]['Key'], key)
        self.assertEqual(self.s3.delete_calls[0]['Key'], self.key)
        self.assertEqual(self.s3.get_calls, [])
        self.assertEqual(self.session.commit_calls, 1)

    async def test_foreign_business_cannot_get_put_or_upload_before_signing(self):
        path = f'/api/v1/businesses/{self.other_business.id}/branding'
        for response in (
            await self.client.get(path),
            await self.client.put(path, json={}),
            await self.client.post(path + '/logo', files={'file': ('logo.png', logo_fixtures._image_bytes('PNG'), 'image/png')}),
        ):
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.s3.presign_calls, [])
        self.assertEqual(self.s3.put_calls, [])

    async def test_member_cannot_update_or_upload_before_signing(self):
        self.membership.role = 'staff'
        for response in (
            await self.client.put(self.path, json={}),
            await self.client.post(self.path + '/logo', files={'file': ('logo.png', logo_fixtures._image_bytes('PNG'), 'image/png')}),
        ):
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.s3.presign_calls + self.s3.put_calls, [])

    async def test_tampered_cross_tenant_key_is_not_signed(self):
        self.branding.logo_storage_key = f'businesses/{self.other_business.id}/branding/logo/foreign.png'
        response = await self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['logo_url'])
        self.assertEqual(self.s3.presign_calls, [])

    async def test_replacement_never_cleans_up_a_foreign_logo_key(self):
        self.branding.logo_storage_key = f'businesses/{self.other_business.id}/branding/logo/foreign.png'
        response = await self.client.post(self.path + '/logo', files={
            'file': ('logo.png', logo_fixtures._image_bytes('PNG'), 'image/png'),
        })
        self.assert_presented(response)
        self.assertEqual(self.s3.delete_calls, [])
        self.assertTrue(self.branding.logo_storage_key.startswith(f'businesses/{self.business.id}/branding/logo/'))

    async def test_explicit_deletion_clears_invalid_reference_without_deleting_foreign_object(self):
        self.branding.logo_storage_key = f'businesses/{self.other_business.id}/branding/logo/foreign.png'
        self.branding.logo_url = None
        response = await self.client.delete(self.path + '/logo')
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(self.branding.logo_storage_key)
        self.assertEqual(self.s3.delete_calls + self.s3.presign_calls, [])

    async def test_onboarding_retry_projects_existing_persisted_logo(self):
        # Use the real onboarding retry service and its owner membership lookup.
        payload = onboarding_fixtures.BusinessOnboardingApiTests._valid_payload(
            self,
            business_id=str(self.business.id), branding={'primary_color': '#123456'},
        )
        context = onboarding_fixtures._make_context(BusinessOnboardingInput.model_validate(payload), self.user.id, created=False)
        context.branding.logo_storage_key = self.key
        context.branding.logo_url = self.branding.logo_url
        session = onboarding_fixtures._FakeAsyncSession()
        session.businesses = [context.business]
        session.memberships = [context.membership]
        session.brandings = [context.branding]
        session.scalar = AsyncMock(side_effect=[context.business, context.membership, context.branding])
        self.session = session
        before = durable_state(context.branding)
        response = await self.client.post('/api/v1/businesses', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['created'])
        membership_query = session.scalar.await_args_list[1].args[0].compile().params
        self.assertIn(self.user.id, membership_query.values())
        self.assertIn(context.business.id, membership_query.values())
        self.assertIn('owner', membership_query.values())
        self.assertIn('X-Amz-Expires=', response.json()['branding']['logo_url'])
        self.assertEqual(durable_state(context.branding), before)
        self.assertEqual(self.s3.put_calls + self.s3.get_calls + self.s3.delete_calls, [])


@pytest.mark.asyncio
async def test_widget_authorizes_origin_before_signing_stored_logo():
    from app.services.chatbot import public_widget_config
    from app.exceptions.chatbot import ChatbotOriginError
    _, config, business = widget_fixtures._records()
    key = f'businesses/{business.id}/branding/logo/widget.png'
    value = branding(key=key, business_id=business.id)
    session = widget_fixtures._DomainSession(execute_row=(config, business, value))
    storage, client = make_storage()
    with pytest.raises(ChatbotOriginError):
        await public_widget_config(session, storage=storage, widget_public_id=widget_fixtures.WIDGET_ID, origin='https://attacker.example', referer=None)
    assert client.presign_calls == []
    before = durable_state(value)
    response, _ = await public_widget_config(session, storage=storage, widget_public_id=widget_fixtures.WIDGET_ID, origin='https://example.com', referer=None)
    assert 'X-Amz-Expires=' in response.logo_url
    assert durable_state(value) == before
    assert client.put_calls + client.get_calls + client.delete_calls == []
