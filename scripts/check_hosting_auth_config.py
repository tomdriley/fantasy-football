#!/usr/bin/env python3
"""Validate a stage authsettingsV2 GET response without reading or printing secrets."""

from __future__ import annotations

import argparse
import json
import re
import sys

AUTH_PHASE = "authentication-only"
WRITE_PHASE = "authenticated-write"
AUTH_PHASES = (AUTH_PHASE, WRITE_PHASE)
AUTH_LOCK = "google-allowlist-v1"
SECRET_SETTING = "GOOGLE_PROVIDER_AUTHENTICATION_SECRET"
PUBLIC_PATHS = {
    "/healthz", "/readyz", "/fantasy-football", "/fantasy-football/",
    "/fantasy-football/api/status",
}
MAX_CONFIG_BYTES = 65536


class InvalidAuthConfig(ValueError):
    pass


def section(parent, key):
    value = parent.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvalidAuthConfig("Authentication configuration has an invalid object.")
    return value


def validate_config(payload, phase: str, client_id: str = "", auth_lock: str = ""):
    if not isinstance(payload, dict):
        raise InvalidAuthConfig("Authentication configuration must be an object.")
    if phase not in ("deployment", "database-readonly", *AUTH_PHASES):
        raise InvalidAuthConfig("Invalid hosting phase.")
    if auth_lock not in ("", AUTH_LOCK):
        raise InvalidAuthConfig("Invalid authentication checkpoint lock.")
    properties = section(payload, "properties")
    platform = section(properties, "platform")
    providers = section(properties, "identityProviders")
    google = section(providers, "google") if providers.get("google") is not None else {}
    if phase not in AUTH_PHASES:
        if auth_lock or platform.get("enabled") is not False or google.get("enabled") is True:
            raise InvalidAuthConfig("Legacy deployment is forbidden once authentication is enabled or locked.")
        return
    if auth_lock != AUTH_LOCK:
        raise InvalidAuthConfig("Authentication deployment requires the persistent checkpoint lock.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}\.apps\.googleusercontent\.com", client_id):
        raise InvalidAuthConfig("The operator-approved Google client ID is required.")
    if platform.get("enabled") is not True or platform.get("runtimeVersion") != "~1" or platform.get("configFilePath"):
        raise InvalidAuthConfig("Enabled, inline Easy Auth V2 configuration is required.")
    global_validation = section(properties, "globalValidation")
    exclusions = global_validation.get("excludedPaths")
    if (
        global_validation.get("requireAuthentication") is not True
        or global_validation.get("unauthenticatedClientAction") != "Return401"
        or global_validation.get("redirectToProvider") != "google"
        or not isinstance(exclusions, list)
        or any(not isinstance(path, str) for path in exclusions)
        or len(exclusions) != len(PUBLIC_PATHS) or set(exclusions) != PUBLIC_PATHS
    ):
        raise InvalidAuthConfig("Easy Auth must require authentication except for the exact technical public paths.")
    registration = section(google, "registration")
    if (
        google.get("enabled") is not True
        or registration.get("clientId") != client_id
        or registration.get("clientSecretSettingName") != SECRET_SETTING
        or section(google, "validation").get("allowedAudiences") != [client_id]
        or section(google, "login").get("scopes") != ["openid", "profile", "email"]
    ):
        raise InvalidAuthConfig("Google provider, audience, scopes or secret setting differ from the approved configuration.")
    for provider, config in providers.items():
        if provider == "google" or config is None or config == {}:
            continue
        if provider == "customOpenIdConnectProviders" or not isinstance(config, dict) or config.get("enabled") is not False:
            raise InvalidAuthConfig("Only the Google identity provider may be enabled.")
    http = section(properties, "httpSettings")
    if (
        http.get("requireHttps") is not True
        or section(http, "forwardProxy").get("convention") != "NoProxy"
        or section(http, "routes").get("apiPrefix") != "/.auth"
    ):
        raise InvalidAuthConfig("HTTPS, the direct App Service origin and the standard auth prefix are required.")
    login = section(properties, "login")
    if (
        login.get("allowedExternalRedirectUrls") not in (None, [])
        or section(login, "tokenStore").get("enabled") is not False
        or section(login, "nonce").get("validateNonce") is not True
        or section(login, "cookieExpiration").get("convention") != "FixedTime"
        or section(login, "cookieExpiration").get("timeToExpiration") != "01:00:00"
        or section(login, "routes").get("logoutEndpoint")
    ):
        raise InvalidAuthConfig("Login must use nonce checks, bounded sessions, no token store or external redirects.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-phase", required=True, choices=("deployment", "database-readonly", *AUTH_PHASES))
    parser.add_argument("--google-client-id", default="")
    parser.add_argument("--auth-lock", default="")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise InvalidAuthConfig("Authentication configuration exceeds the size limit.")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise InvalidAuthConfig("Authentication configuration is not valid JSON.") from exc
        validate_config(payload, args.expected_phase, args.google_client_id, args.auth_lock)
    except InvalidAuthConfig as exc:
        print(f"Authentication configuration rejected: {exc}", file=sys.stderr)
        return 1
    print("Stage authentication configuration verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
