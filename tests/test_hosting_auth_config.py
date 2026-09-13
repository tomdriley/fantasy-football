from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from hosting.auth import AUTH_CAPABILITY, PUBLIC_PATHS
from scripts import check_hosting_auth_config as config

CLIENT_ID = "1234567890-example.apps.googleusercontent.com"
ROOT = Path(__file__).resolve().parent.parent


def approved_config():
    return {"properties": {
        "platform": {"enabled": True, "runtimeVersion": "~1"},
        "globalValidation": {
            "requireAuthentication": True, "unauthenticatedClientAction": "Return401",
            "redirectToProvider": "google", "excludedPaths": sorted(PUBLIC_PATHS),
        },
        "identityProviders": {"google": {
            "enabled": True,
            "registration": {"clientId": CLIENT_ID, "clientSecretSettingName": config.SECRET_SETTING},
            "login": {"scopes": ["openid", "profile", "email"]},
            "validation": {"allowedAudiences": [CLIENT_ID]},
        }},
        "httpSettings": {
            "requireHttps": True, "forwardProxy": {"convention": "NoProxy"},
            "routes": {"apiPrefix": "/.auth"},
        },
        "login": {
            "allowedExternalRedirectUrls": [], "tokenStore": {"enabled": False},
            "nonce": {"validateNonce": True},
            "cookieExpiration": {"convention": "FixedTime", "timeToExpiration": "01:00:00"},
        },
    }}


class TestHostingAuthConfig(unittest.TestCase):
    def test_approved_config_and_shared_constants_match(self):
        config.validate_config(approved_config(), config.AUTH_PHASE, CLIENT_ID, AUTH_CAPABILITY)
        self.assertEqual(config.PUBLIC_PATHS, PUBLIC_PATHS)
        self.assertEqual(config.AUTH_LOCK, AUTH_CAPABILITY)

    def test_azure_unused_provider_defaults_must_be_explicitly_disabled(self):
        payload = approved_config()
        for name in ("azureActiveDirectory", "facebook", "gitHub", "twitter", "legacyMicrosoftAccount", "apple"):
            payload["properties"]["identityProviders"][name] = {
                "enabled": False, "registration": {}, "login": {},
            }
        config.validate_config(payload, config.AUTH_PHASE, CLIENT_ID, AUTH_CAPABILITY)
        payload["properties"]["identityProviders"]["facebook"]["enabled"] = True
        with self.assertRaises(config.InvalidAuthConfig):
            config.validate_config(payload, config.AUTH_PHASE, CLIENT_ID, AUTH_CAPABILITY)

    def test_legacy_deployment_only_before_authentication_and_lock(self):
        disabled = {"properties": {"platform": {"enabled": False}}}
        for phase in ("deployment", "database-readonly"):
            config.validate_config(disabled, phase)
            for payload, lock in ((approved_config(), ""), (disabled, AUTH_CAPABILITY), ({}, "")):
                with self.subTest(phase=phase, lock=lock), self.assertRaises(config.InvalidAuthConfig):
                    config.validate_config(payload, phase, auth_lock=lock)
        disabled["properties"]["identityProviders"] = {"google": {"enabled": True}}
        with self.assertRaises(config.InvalidAuthConfig):
            config.validate_config(disabled, "deployment")

    def test_missing_or_wrong_client_id_and_lock_fail_closed(self):
        for client_id, lock in (("", AUTH_CAPABILITY), ("other.apps.googleusercontent.com", AUTH_CAPABILITY),
                                (CLIENT_ID, ""), (CLIENT_ID, "other"), ("bad\n", AUTH_CAPABILITY)):
            with self.subTest(client_id=client_id, lock=lock), self.assertRaises(config.InvalidAuthConfig):
                config.validate_config(approved_config(), config.AUTH_PHASE, client_id, lock)

    def test_observed_disabled_azure_config_can_contain_null_unused_sections(self):
        observed = {"properties": {
            "platform": {"enabled": False},
            "globalValidation": None, "identityProviders": None,
            "httpSettings": None, "login": None,
        }}
        for phase in ("deployment", "database-readonly"):
            config.validate_config(observed, phase)
            with self.assertRaises(config.InvalidAuthConfig):
                config.validate_config(observed, phase, auth_lock=AUTH_CAPABILITY)
        with self.assertRaises(config.InvalidAuthConfig):
            config.validate_config(observed, config.AUTH_PHASE, CLIENT_ID, AUTH_CAPABILITY)

    def test_null_required_auth_sections_still_fail_closed(self):
        for key in ("platform", "globalValidation", "identityProviders", "httpSettings", "login"):
            payload = approved_config()
            payload["properties"][key] = None
            with self.subTest(key=key), self.assertRaises(config.InvalidAuthConfig):
                config.validate_config(payload, config.AUTH_PHASE, CLIENT_ID, AUTH_CAPABILITY)

    def test_weakened_or_malformed_auth_config_is_rejected(self):
        changes = [
            (("platform", "enabled"), False),
            (("platform", "runtimeVersion"), "~2"),
            (("platform", "configFilePath"), "auth.json"),
            (("globalValidation", "requireAuthentication"), False),
            (("globalValidation", "unauthenticatedClientAction"), "AllowAnonymous"),
            (("globalValidation", "redirectToProvider"), "aad"),
            (("globalValidation", "excludedPaths"), ["/fantasy-football/*"]),
            (("globalValidation", "excludedPaths"), [*sorted(PUBLIC_PATHS), "/fantasy-football/api/sample"]),
            (("globalValidation", "excludedPaths"), [*sorted(PUBLIC_PATHS)[:-1], None]),
            (("identityProviders", "google", "enabled"), False),
            (("identityProviders", "google", "registration", "clientId"), "wrong"),
            (("identityProviders", "google", "registration", "clientSecretSettingName"), "OTHER_SECRET"),
            (("identityProviders", "google", "validation", "allowedAudiences"), [CLIENT_ID, "other"]),
            (("identityProviders", "google", "login", "scopes"), ["openid", "calendar"]),
            (("identityProviders", "azureActiveDirectory"), {"enabled": True}),
            (("identityProviders", "customOpenIdConnectProviders"), {"other": {"enabled": True}}),
            (("httpSettings", "requireHttps"), False),
            (("httpSettings", "forwardProxy", "convention"), "Standard"),
            (("httpSettings", "routes", "apiPrefix"), "/custom-auth"),
            (("login", "tokenStore", "enabled"), True),
            (("login", "allowedExternalRedirectUrls"), ["https://other.example"]),
            (("login", "nonce", "validateNonce"), False),
            (("login", "cookieExpiration", "timeToExpiration"), "08:00:00"),
            (("login", "routes", "logoutEndpoint"), "/custom-logout"),
            (("platform",), None), (("identityProviders",), []), (("login",), "invalid"),
        ]
        for keys, value in changes:
            payload = deepcopy(approved_config())
            obj = payload["properties"]
            for key in keys[:-1]:
                obj = obj.setdefault(key, {})
            obj[keys[-1]] = value
            with self.subTest(keys=keys, value=value), self.assertRaises(config.InvalidAuthConfig):
                config.validate_config(payload, config.AUTH_PHASE, CLIENT_ID, AUTH_CAPABILITY)

    def test_cli_bounds_and_sanitizes_input_without_any_azure_write(self):
        for raw, expected in (
            (json.dumps(approved_config()), 0),
            ('{"private":"not-for-logs",', 1), ("[" * 1200, 1),
            ("x" * (config.MAX_CONFIG_BYTES + 1), 1), ("[]", 1),
        ):
            with patch("sys.stdin", io.StringIO(raw)), patch("sys.stdout", io.StringIO()) as stdout, \
                    patch("sys.stderr", io.StringIO()) as stderr:
                result = config.main([
                    "--expected-phase", config.AUTH_PHASE, "--google-client-id", CLIENT_ID,
                    "--auth-lock", AUTH_CAPABILITY,
                ])
            self.assertEqual(result, expected)
            self.assertNotIn("not-for-logs", stdout.getvalue() + stderr.getvalue())
            self.assertNotIn(CLIENT_ID, stdout.getvalue() + stderr.getvalue())

    def test_templates_touch_only_stage_auth_settings_and_exact_secret_grant(self):
        auth = (ROOT / "infra/azure/stage-auth.bicep").read_text()
        settings = (ROOT / "infra/azure/stage-auth-settings.bicep").read_text()
        self.assertIn("name: 'authsettingsV2'", auth)
        self.assertNotIn("name: 'appsettings'", auth)
        for path in PUBLIC_PATHS:
            self.assertIn(f"'{path}'", auth)
        self.assertNotIn("'/fantasy-football/api/sample'", auth)
        self.assertNotIn("'/fantasy-football/api/session'", auth)
        for name in ("azureActiveDirectory", "facebook", "gitHub", "twitter", "legacyMicrosoftAccount", "apple"):
            self.assertIn(name + ": { enabled: false }", auth)
        self.assertIn("scope: secret", settings)
        self.assertIn("param googleSecretName string = 'google-auth-stage-client-secret'", settings)
        self.assertIn("name: googleSecretName", settings)
        self.assertIn("principalId: stage.identity.principalId", settings)
        self.assertIn("@secure()\nparam existingAppSettings object", settings)
        self.assertIn("properties: union(existingAppSettings,", settings)
        self.assertNotIn("list('${stage.id}/config/appsettings'", settings)
        self.assertIn("FFOPT_HOSTING_PHASE: 'authentication-only'", settings)
        self.assertIn("@Microsoft.KeyVault(SecretUri=", settings)
        self.assertNotIn("FFOPT_DB_", settings)
        for template in (auth, settings):
            self.assertIn("Microsoft.Web/sites@2024-11-01' existing", template)
            self.assertIn("Microsoft.Web/sites/slots@2024-11-01' existing", template)
            self.assertNotIn("clientSecret:", template)


if __name__ == "__main__":
    unittest.main()
