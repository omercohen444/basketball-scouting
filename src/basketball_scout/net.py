"""Network trust setup.

Why this exists
---------------
On the current development machine, TLS verification against the bundled
``certifi`` roots fails for every HTTPS host::

    SSLError: certificate verify failed: unable to get local issuer certificate

The required root CA is present in the **Windows certificate store** but not in
``certifi`` — the signature of a TLS-intercepting proxy or endpoint security
product. Two stale environment variables (``REQUESTS_CA_BUNDLE`` and
``SSL_CERT_FILE``, both pointing at an unrelated conda env) make it worse by
overriding whatever bundle a library would otherwise choose.

``truststore`` reroutes verification through the OS trust store, which resolves
it. This affects everything that talks HTTPS — the PBP probe *and* the Gemini
SDK — so it is called once at the top of each entry point.

This is a local environment workaround, not a security relaxation: certificates
are still fully verified, just against the OS trust store. Nothing here ever
disables verification.
"""

from __future__ import annotations

import os

_TRUST_ENV_VARS = ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")

_enabled = False


def enable_system_trust_store(*, clear_stale_env: bool = True) -> bool:
    """Route TLS verification through the OS trust store. Idempotent.

    Returns True if truststore is active. Returns False (without raising) when
    the package is unavailable — on a machine with a working certifi chain the
    default behaviour is already correct, so this must never be fatal.
    """
    global _enabled
    if _enabled:
        return True

    try:
        import truststore
    except ImportError:
        return False

    if clear_stale_env:
        # These override the OS store and point at an unrelated environment on
        # this machine. Cleared for this process only.
        for name in _TRUST_ENV_VARS:
            os.environ.pop(name, None)

    truststore.inject_into_ssl()
    _enabled = True
    return True


def trust_status() -> dict[str, object]:
    """Diagnostic snapshot for debug artifacts."""
    return {
        "truststore_enabled": _enabled,
        "ca_env_overrides": {name: os.environ.get(name) for name in _TRUST_ENV_VARS},
    }
