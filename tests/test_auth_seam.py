"""Unit tests for the auth seam (E-13).

The seam is the code path carrying every authorisation guarantee in §5, and it runs on two
surfaces with different trust models. These tests run off-platform — no Databricks, no
network — so the guarantees are checkable on every commit.

The rules being enforced:
  1. Failure is always an error, never a fallback to a broader principal.
  2. The mode is explicit; an unset value must not pick a trust model.
  3. Tokens never appear in reprs, logs or error text.
"""

import pytest
from fleetguard_api.auth.tokens import (
    AuthError,
    ForwardedHeaderTokenProvider,
    Principal,
    SessionTokenProvider,
    StaticTokenProvider,
    build_token_provider,
)

TOKEN = "dapi-not-a-real-token-0123456789"


class TestForwardedHeaderProvider:
    """Databricks Apps: the platform injects the user's token as a header."""

    def test_resolves_the_forwarded_token(self):
        p = ForwardedHeaderTokenProvider().resolve({"X-Forwarded-Access-Token": TOKEN})
        assert p.token == TOKEN
        assert p.source == "databricks-apps"

    def test_header_lookup_is_case_insensitive(self):
        """HTTP headers are case-insensitive; ASGI servers differ in what they hand us."""
        for name in (
            "x-forwarded-access-token",
            "X-Forwarded-Access-Token",
            "X-FORWARDED-ACCESS-TOKEN",
        ):
            assert ForwardedHeaderTokenProvider().resolve({name: TOKEN}).token == TOKEN

    @pytest.mark.parametrize(
        "headers", [{}, {"x-forwarded-access-token": ""}, {"x-forwarded-access-token": "   "}]
    )
    def test_missing_or_blank_token_raises(self, headers):
        """Must fail closed. A blank header is absence, not an empty-string identity."""
        with pytest.raises(AuthError):
            ForwardedHeaderTokenProvider().resolve(headers)

    def test_picks_up_forwarded_identity_when_present(self):
        p = ForwardedHeaderTokenProvider().resolve(
            {"x-forwarded-access-token": TOKEN, "x-forwarded-email": "a@b.com"}
        )
        assert p.user_name == "a@b.com"


class TestSessionProvider:
    """Render: the token came from the U2M OAuth code exchange."""

    def test_resolves_from_session_cookie(self):
        store = {"sess-1": {"access_token": TOKEN, "user_name": "a@b.com"}}
        p = SessionTokenProvider(store.get).resolve({"cookie": "fg_session=sess-1"})
        assert p.token == TOKEN
        assert p.source == "render-u2m"

    def test_parses_the_right_cookie_among_several(self):
        store = {"s2": {"access_token": TOKEN}}
        p = SessionTokenProvider(store.get).resolve(
            {"cookie": "other=1; fg_session=s2; theme=dark"}
        )
        assert p.token == TOKEN

    @pytest.mark.parametrize("cookie", ["", "other=1", "fg_session=", "fg_session=unknown"])
    def test_absent_or_unknown_session_raises(self, cookie):
        with pytest.raises(AuthError):
            SessionTokenProvider({}.get).resolve({"cookie": cookie})

    def test_session_without_a_token_raises(self):
        """A session can exist while the OAuth exchange failed — that is not an identity."""
        store = {"s": {"user_name": "a@b.com"}}
        with pytest.raises(AuthError):
            SessionTokenProvider(store.get).resolve({"cookie": "fg_session=s"})


class TestModeSelection:
    """The mode is explicit. Inference would let a misconfiguration pick a trust model."""

    def test_unset_mode_raises_rather_than_defaulting(self):
        with pytest.raises(AuthError, match="must be one of"):
            build_token_provider({})

    def test_unknown_mode_raises(self):
        with pytest.raises(AuthError):
            build_token_provider({"FLEETGUARD_AUTH_MODE": "trust-me"})

    def test_databricks_apps_mode(self):
        prov = build_token_provider({"FLEETGUARD_AUTH_MODE": "databricks-apps"})
        assert isinstance(prov, ForwardedHeaderTokenProvider)

    def test_render_mode_requires_a_session_lookup(self):
        with pytest.raises(AuthError, match="session_lookup"):
            build_token_provider({"FLEETGUARD_AUTH_MODE": "render-u2m"})

    def test_static_dev_requires_a_token(self):
        with pytest.raises(AuthError, match="FLEETGUARD_DEV_TOKEN"):
            build_token_provider({"FLEETGUARD_AUTH_MODE": "static-dev"})

    def test_static_dev_mode(self):
        prov = build_token_provider(
            {"FLEETGUARD_AUTH_MODE": "static-dev", "FLEETGUARD_DEV_TOKEN": TOKEN}
        )
        assert isinstance(prov, StaticTokenProvider)
        assert prov.resolve({}).token == TOKEN


class TestTokenNeverLeaks:
    """A token in a traceback or log is a credential disclosure."""

    def test_repr_redacts_the_token(self):
        r = repr(Principal(token=TOKEN, user_name="a@b.com", source="test"))
        assert TOKEN not in r
        assert "redacted" in r

    def test_auth_error_messages_carry_no_token(self):
        """Error text reaches clients and logs; it must never quote the credential."""
        with pytest.raises(AuthError) as exc:
            ForwardedHeaderTokenProvider().resolve({"x-forwarded-access-token": ""})
        assert TOKEN not in str(exc.value)


class TestSurfacePortability:
    """The seam's reason for existing: both surfaces yield the same Principal shape."""

    def test_both_providers_produce_an_equivalent_principal(self):
        apps = ForwardedHeaderTokenProvider().resolve(
            {"x-forwarded-access-token": TOKEN, "x-forwarded-email": "a@b.com"}
        )
        store = {"s": {"access_token": TOKEN, "user_name": "a@b.com"}}
        render = SessionTokenProvider(store.get).resolve({"cookie": "fg_session=s"})

        assert apps.token == render.token
        assert apps.user_name == render.user_name
        # Only the provenance differs — which is exactly the migration surface area.
        assert apps.source != render.source
