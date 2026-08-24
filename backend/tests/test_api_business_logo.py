import os
import unittest
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4

import httpx
from PIL import Image
from sqlalchemy.exc import SQLAlchemyError


os.environ["AIBOS_DATABASE_URL"] = "postgresql+asyncpg://database.invalid/test"
os.environ["AIBOS_AUTH_SECRET_KEY"] = "x" * 32

from app.api.dependencies.auth import get_current_user  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.business import Business  # noqa: E402
from app.models.business_branding import BusinessBranding  # noqa: E402
from app.models.business_membership import BusinessMembership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.logo_image import (  # noqa: E402
    MAX_LOGO_DIMENSION,
    MAX_LOGO_UPLOAD_BYTES,
)
from app.storage.base import (  # noqa: E402
    ObjectStorage,
    StorageOperationError,
)
from app.storage.factory import get_object_storage  # noqa: E402


class BusinessLogoApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[str] = []
        self.user = _make_user("member@example.test")
        self.business = _make_business("Tenant A", "tenant-a")
        self.other_business = _make_business("Tenant B", "tenant-b")
        self.membership = _make_membership(self.business, self.user)
        self.session = _FakeAsyncSession(
            businesses=[self.business, self.other_business],
            memberships=[self.membership],
            events=self.events,
        )
        self.storage = _FakeStorage(events=self.events)
        self.original_overrides = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_current_user() -> User:
            return self.user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_current_user] = override_current_user
        app.dependency_overrides[get_object_storage] = lambda: self.storage
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)

    def test_openapi_documents_multipart_upload_and_bodyless_delete(self) -> None:
        operations = app.openapi()["paths"][self._openapi_path()]

        self.assertIn("post", operations)
        self.assertIn("delete", operations)
        self.assertTrue(operations["post"]["security"])
        self.assertTrue(operations["delete"]["security"])
        self.assertIn(
            "multipart/form-data",
            operations["post"]["requestBody"]["content"],
        )
        self.assertNotIn("requestBody", operations["delete"])
        self.assertNotIn("content", operations["delete"]["responses"]["204"])

    async def test_authentication_is_required(self) -> None:
        del app.dependency_overrides[get_current_user]

        response = await self._upload(self.business.id, _image_bytes("PNG"))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.session.execute_calls, 0)
        self.assertEqual(self.storage.objects, {})

    async def test_png_jpeg_and_webp_are_accepted_from_actual_bytes(self) -> None:
        expected_content_types = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
        }
        for image_format, expected_content_type in expected_content_types.items():
            with self.subTest(image_format=image_format):
                self.session.brandings.clear()
                self.storage.objects.clear()
                response = await self._upload(
                    self.business.id,
                    _image_bytes(image_format),
                    filename="../../caller-controlled.exe",
                    content_type="application/octet-stream",
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(self.storage.objects), 1)
                key = next(iter(self.storage.objects))
                stored_content, stored_type = self.storage.objects[key]
                self.assertEqual(stored_type, expected_content_type)
                self.assertNotIn("caller-controlled", key)
                self.assertNotIn("..", key)
                self.assertTrue(
                    key.startswith(f"businesses/{self.business.id}/branding/logo/")
                )
                self.assertNotEqual(stored_content, _image_bytes(image_format))

    async def test_fake_unsupported_oversized_and_absurd_images_are_rejected(
        self,
    ) -> None:
        gif = BytesIO()
        Image.new("RGB", (8, 8)).save(gif, format="GIF")
        huge_dimensions = BytesIO()
        Image.new("RGB", (MAX_LOGO_DIMENSION + 1, 1)).save(
            huge_dimensions,
            format="PNG",
        )
        invalid_uploads = (
            (b"not an image", 422),
            (gif.getvalue(), 422),
            (b"x" * (MAX_LOGO_UPLOAD_BYTES + 1), 413),
            (huge_dimensions.getvalue(), 422),
        )
        for content, expected_status in invalid_uploads:
            with self.subTest(expected_status=expected_status, size=len(content)):
                response = await self._upload(self.business.id, content)
                self.assertEqual(response.status_code, expected_status)

        self.assertEqual(self.storage.objects, {})
        self.assertEqual(self.session.commit_calls, 0)

    async def test_client_cannot_supply_logo_url_key_or_business_id(self) -> None:
        for field_name in ("logo_url", "logo_storage_key", "business_id"):
            with self.subTest(field_name=field_name):
                response = await self.client.post(
                    self._path(self.business.id),
                    files={
                        "file": ("logo.png", _image_bytes("PNG"), "image/png"),
                        field_name: (None, "caller-controlled"),
                    },
                )
                self.assertEqual(response.status_code, 422)

        self.assertEqual(self.storage.objects, {})
        self.assertEqual(self.session.commit_calls, 0)

    async def test_upload_persists_trusted_reference_and_get_returns_it(self) -> None:
        response = await self._upload(self.business.id, _image_bytes("PNG"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.commit_calls, 1)
        branding = self.session.brandings[0]
        self.assertEqual(response.json()["logo_url"], branding.logo_url)
        self.assertNotIn("logo_storage_key", response.text)
        self.assertNotIn("storage", response.text.lower())
        self.assertIsInstance(branding.logo_storage_key, str)
        self.assertNotIsInstance(branding.logo_url, bytes)
        loaded = await self.client.get(
            f"/api/v1/businesses/{self.business.id}/branding"
        )
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["logo_url"], branding.logo_url)

    async def test_replacement_commits_new_reference_before_deleting_old(self) -> None:
        old_key = f"businesses/{self.business.id}/branding/logo/old-logo.png"
        old_url = self.storage.public_url(old_key)
        branding = _make_branding(
            self.business.id,
            logo_url=old_url,
            logo_storage_key=old_key,
            primary_color="#176B45",
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[old_key] = (b"old", "image/png")

        response = await self._upload(self.business.id, _image_bytes("WEBP"))

        self.assertEqual(response.status_code, 200)
        new_key = branding.logo_storage_key
        assert new_key is not None
        self.assertIn(new_key, self.storage.objects)
        self.assertNotIn(old_key, self.storage.objects)
        put_index = self.events.index(f"put:{new_key}")
        commit_index = self.events.index("commit")
        delete_index = self.events.index(f"delete:{old_key}")
        self.assertLess(put_index, commit_index)
        self.assertLess(commit_index, delete_index)
        self.assertEqual(branding.primary_color, "#176B45")

    async def test_commit_failure_cleans_new_object_and_preserves_old_logo(
        self,
    ) -> None:
        old_key = f"businesses/{self.business.id}/branding/logo/old-logo.png"
        old_url = self.storage.public_url(old_key)
        branding = _make_branding(
            self.business.id,
            logo_url=old_url,
            logo_storage_key=old_key,
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[old_key] = (b"old", "image/png")
        self.session.commit_error = SQLAlchemyError("private commit detail")

        response = await self._upload(self.business.id, _image_bytes("PNG"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.session.rollback_calls, 1)
        self.assertEqual(branding.logo_storage_key, old_key)
        self.assertEqual(branding.logo_url, old_url)
        self.assertEqual(set(self.storage.objects), {old_key})
        self.assertNotIn("private", response.text)

    async def test_flush_failure_cleans_new_object_and_preserves_old_logo(self) -> None:
        old_key = f"businesses/{self.business.id}/branding/logo/old-logo.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(old_key),
            logo_storage_key=old_key,
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[old_key] = (b"old", "image/png")
        self.session.flush_error = SQLAlchemyError("private flush detail")

        response = await self._upload(self.business.id, _image_bytes("JPEG"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(set(self.storage.objects), {old_key})
        self.assertEqual(branding.logo_storage_key, old_key)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_storage_failure_preserves_database_and_old_object(self) -> None:
        old_key = f"businesses/{self.business.id}/branding/logo/old-logo.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(old_key),
            logo_storage_key=old_key,
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[old_key] = (b"old", "image/png")
        self.storage.put_error = StorageOperationError("private storage detail")

        response = await self._upload(self.business.id, _image_bytes("PNG"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(branding.logo_storage_key, old_key)
        self.assertEqual(set(self.storage.objects), {old_key})
        self.assertNotIn("private", response.text)

    async def test_old_cleanup_failure_does_not_rollback_new_logo(self) -> None:
        old_key = f"businesses/{self.business.id}/branding/logo/old-logo.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(old_key),
            logo_storage_key=old_key,
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[old_key] = (b"old", "image/png")
        self.storage.delete_error_keys.add(old_key)

        response = await self._upload(self.business.id, _image_bytes("PNG"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.session.commit_calls, 1)
        self.assertNotEqual(branding.logo_storage_key, old_key)
        self.assertIn(old_key, self.storage.objects)

    async def test_delete_is_idempotent_and_preserves_brand_colors(self) -> None:
        key = f"businesses/{self.business.id}/branding/logo/current.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(key),
            logo_storage_key=key,
            primary_color="#176B45",
            accent_color="#D36F32",
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[key] = (b"logo", "image/png")

        first = await self.client.delete(self._path(self.business.id))
        second = await self.client.delete(self._path(self.business.id))

        self.assertEqual(first.status_code, 204)
        self.assertEqual(first.content, b"")
        self.assertEqual(second.status_code, 204)
        self.assertIsNone(branding.logo_url)
        self.assertIsNone(branding.logo_storage_key)
        self.assertEqual(branding.primary_color, "#176B45")
        self.assertEqual(branding.accent_color, "#D36F32")
        self.assertNotIn(key, self.storage.objects)
        self.assertEqual(first.headers["Cache-Control"], "no-store")

    async def test_delete_removes_empty_branding_row_after_commit(self) -> None:
        key = f"businesses/{self.business.id}/branding/logo/current.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(key),
            logo_storage_key=key,
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[key] = (b"logo", "image/png")

        response = await self.client.delete(self._path(self.business.id))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.session.brandings, [])
        self.assertLess(
            self.events.index("commit"),
            self.events.index(f"delete:{key}"),
        )

    async def test_delete_commit_failure_preserves_reference_and_object(
        self,
    ) -> None:
        key = f"businesses/{self.business.id}/branding/logo/current.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(key),
            logo_storage_key=key,
            primary_color="#176B45",
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[key] = (b"logo", "image/png")
        self.session.commit_error = SQLAlchemyError("private commit detail")

        response = await self.client.delete(self._path(self.business.id))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(branding.logo_storage_key, key)
        self.assertEqual(branding.logo_url, self.storage.public_url(key))
        self.assertIn(key, self.storage.objects)
        self.assertNotIn(f"delete:{key}", self.events)
        self.assertNotIn("private", response.text)

    async def test_delete_cleanup_failure_keeps_committed_logo_removal(
        self,
    ) -> None:
        key = f"businesses/{self.business.id}/branding/logo/current.png"
        branding = _make_branding(
            self.business.id,
            logo_url=self.storage.public_url(key),
            logo_storage_key=key,
            primary_color="#176B45",
        )
        self.session.brandings.append(branding)
        self.session.capture_committed_state()
        self.storage.objects[key] = (b"logo", "image/png")
        self.storage.delete_error_keys.add(key)

        response = await self.client.delete(self._path(self.business.id))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.session.commit_calls, 1)
        self.assertIsNone(branding.logo_url)
        self.assertIsNone(branding.logo_storage_key)
        self.assertEqual(branding.primary_color, "#176B45")
        self.assertIn(key, self.storage.objects)

    async def test_cross_tenant_upload_and_delete_are_safe_404s(self) -> None:
        hidden_key = f"businesses/{self.other_business.id}/branding/logo/hidden.png"
        hidden = _make_branding(
            self.other_business.id,
            logo_url=self.storage.public_url(hidden_key),
            logo_storage_key=hidden_key,
        )
        self.session.brandings.append(hidden)
        self.session.capture_committed_state()
        self.storage.objects[hidden_key] = (b"hidden", "image/png")

        upload = await self._upload(
            self.other_business.id,
            _image_bytes("PNG"),
        )
        deletion = await self.client.delete(self._path(self.other_business.id))
        missing = await self.client.delete(self._path(uuid4()))

        self.assertEqual(upload.status_code, 404)
        self.assertEqual(deletion.status_code, 404)
        self.assertEqual(deletion.json(), missing.json())
        self.assertEqual(hidden.logo_storage_key, hidden_key)
        self.assertEqual(set(self.storage.objects), {hidden_key})
        self.assertEqual(self.session.commit_calls, 0)

    async def test_local_media_route_does_not_path_traverse(self) -> None:
        for path in (
            "/api/v1/media/../.env",
            "/api/v1/media/%2e%2e/.env",
            "/api/v1/media/businesses/%2e%2e/%2e%2e/.env",
        ):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertIn(response.status_code, {404, 405})

    async def _upload(
        self,
        business_id: UUID,
        content: bytes,
        *,
        filename: str = "logo.png",
        content_type: str = "image/png",
    ) -> httpx.Response:
        return await self.client.post(
            self._path(business_id),
            files={"file": (filename, content, content_type)},
        )

    @staticmethod
    def _path(business_id: UUID) -> str:
        return f"/api/v1/businesses/{business_id}/branding/logo"

    @staticmethod
    def _openapi_path() -> str:
        return "/api/v1/businesses/{business_id}/branding/logo"


class _FakeAccessResult:
    def __init__(
        self,
        row: tuple[Business, BusinessMembership] | None,
    ) -> None:
        self.row = row

    def one_or_none(self) -> tuple[Business, BusinessMembership] | None:
        return self.row


class _FakeAsyncSession:
    def __init__(
        self,
        *,
        businesses: list[Business],
        memberships: list[BusinessMembership],
        events: list[str],
    ) -> None:
        self.businesses = businesses
        self.memberships = memberships
        self.brandings: list[BusinessBranding] = []
        self.events = events
        self.execute_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.flush_error: SQLAlchemyError | None = None
        self.commit_error: SQLAlchemyError | None = None
        self._committed: dict[
            UUID, tuple[str | None, str | None, str | None, str | None, str | None]
        ] = {}

    async def execute(self, statement: object) -> _FakeAccessResult:
        self.execute_calls += 1
        parameters = statement.compile().params
        user_id = _parameter(parameters, "user_id")
        business_id = _parameter(parameters, "business_id")
        membership = next(
            (
                item
                for item in self.memberships
                if item.user_id == user_id and item.business_id == business_id
            ),
            None,
        )
        business = next(
            (item for item in self.businesses if item.id == business_id),
            None,
        )
        return _FakeAccessResult(
            (business, membership)
            if business is not None and membership is not None
            else None
        )

    async def scalar(self, statement: object) -> BusinessBranding | None:
        business_id = _parameter(statement.compile().params, "business_id")
        return next(
            (
                branding
                for branding in self.brandings
                if branding.business_id == business_id
            ),
            None,
        )

    def add(self, instance: object) -> None:
        if isinstance(instance, BusinessBranding):
            self.brandings.append(instance)

    async def delete(self, instance: object) -> None:
        if isinstance(instance, BusinessBranding):
            self.brandings.remove(instance)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error

    async def commit(self) -> None:
        self.commit_calls += 1
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error
        self.capture_committed_state()

    async def rollback(self) -> None:
        self.rollback_calls += 1
        current = {branding.business_id: branding for branding in self.brandings}
        for business_id in list(current):
            if business_id not in self._committed:
                self.brandings.remove(current[business_id])
        for business_id, values in self._committed.items():
            branding = current.get(business_id)
            if branding is None:
                branding = BusinessBranding(business_id=business_id)
                self.brandings.append(branding)
            (
                branding.logo_url,
                branding.logo_storage_key,
                branding.primary_color,
                branding.secondary_color,
                branding.accent_color,
            ) = values

    def capture_committed_state(self) -> None:
        self._committed = {
            branding.business_id: (
                branding.logo_url,
                branding.logo_storage_key,
                branding.primary_color,
                branding.secondary_color,
                branding.accent_color,
            )
            for branding in self.brandings
        }


class _FakeStorage(ObjectStorage):
    def __init__(self, *, events: list[str]) -> None:
        self.events = events
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_error: StorageOperationError | None = None
        self.delete_error_keys: set[str] = set()

    async def put(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        self.events.append(f"put:{object_key}")
        if self.put_error is not None:
            raise self.put_error
        self.objects[object_key] = (content, content_type)

    async def delete(self, object_key: str) -> None:
        self.events.append(f"delete:{object_key}")
        if object_key in self.delete_error_keys:
            raise StorageOperationError("private delete detail")
        self.objects.pop(object_key, None)

    def public_url(self, object_key: str) -> str:
        return f"/api/v1/media/{object_key}"


def _parameter(parameters: dict[str, object], prefix: str) -> object:
    return next(value for name, value in parameters.items() if name.startswith(prefix))


def _image_bytes(image_format: str) -> bytes:
    image = Image.new("RGB", (40, 20), (32, 96, 160))
    output = BytesIO()
    options = {"quality": 80} if image_format in {"JPEG", "WEBP"} else {}
    image.save(output, format=image_format, **options)
    return output.getvalue()


def _make_user(email: str) -> User:
    user = User(
        email=email,
        password_hash="stored-test-hash",
        first_name="Test",
        last_name="User",
        status="active",
        is_email_verified=True,
    )
    user.id = uuid4()
    user.created_at = datetime.now(UTC)
    user.updated_at = user.created_at
    return user


def _make_business(name: str, slug: str) -> Business:
    business = Business(
        name=name,
        slug=slug,
        business_type="services",
        status="active",
        timezone="UTC",
        currency="USD",
        locale="en",
    )
    business.id = uuid4()
    business.created_at = datetime.now(UTC)
    business.updated_at = business.created_at
    return business


def _make_membership(
    business: Business,
    user: User,
) -> BusinessMembership:
    membership = BusinessMembership(
        business_id=business.id,
        user_id=user.id,
        role="owner",
        status="active",
    )
    membership.id = uuid4()
    membership.created_at = datetime.now(UTC)
    membership.updated_at = membership.created_at
    return membership


def _make_branding(
    business_id: UUID,
    *,
    logo_url: str | None = None,
    logo_storage_key: str | None = None,
    primary_color: str | None = None,
    secondary_color: str | None = None,
    accent_color: str | None = None,
) -> BusinessBranding:
    branding = BusinessBranding(
        business_id=business_id,
        logo_url=logo_url,
        logo_storage_key=logo_storage_key,
        primary_color=primary_color,
        secondary_color=secondary_color,
        accent_color=accent_color,
    )
    branding.created_at = datetime.now(UTC)
    branding.updated_at = branding.created_at
    return branding
