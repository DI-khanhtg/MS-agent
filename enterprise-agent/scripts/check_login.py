"""Check local Entra sign-in and backend token validation without exposing tokens."""

import argparse
import asyncio
from uuid import UUID

import msal

from enterprise_agent.config import Settings
from enterprise_agent.security.token_validation import AuthenticationError, EntraTokenValidator


def report_failure(label: str, result: dict[str, object]) -> None:
    """Report only numeric Entra error codes, never response or credential contents."""
    print(label)
    codes = result.get("error_codes")
    if isinstance(codes, list):
        for code in codes:
            if type(code) is int:
                print(f"AADSTS{code}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true", help="Validate settings offline")
    parser.add_argument("--client-id", help="Select a client from ENTRA_ALLOWED_CLIENT_IDS")
    parser.add_argument(
        "--workiq-token",
        action="store_true",
        help="Also check Entra OBO credentials for Work IQ; does not call the Work IQ service",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings()
        tenant_id = str(UUID(settings.azure_tenant_id))
        backend_id = str(UUID(settings.azure_client_id))
        client_ids = settings.allowed_client_ids
        client_id = args.client_id or (next(iter(client_ids)) if len(client_ids) == 1 else "")
        if client_id not in client_ids:
            raise ValueError("Select an allowed client")
        client_id = str(UUID(client_id))
        if backend_id not in settings.entra_api_audiences:
            raise ValueError("The v2 audience must include the backend client ID")
        if tenant_id not in settings.allowed_tenant_ids or not settings.required_scopes:
            raise ValueError("Tenant and scopes must be allowed")
        if args.workiq_token and (
            not settings.workiq_obo_is_configured
            or not settings.workiq_scope.startswith("api://workiq.svc.cloud.microsoft/")
        ):
            raise ValueError("Configure Work IQ OBO credentials and scope")
        scopes = sorted(f"api://{backend_id}/{scope}" for scope in settings.required_scopes)
    except ValueError:
        print("CONFIG_FAILED: check tenant, audience, scopes, allowed clients and OBO credentials")
        return 2

    if args.check_config:
        print("CONFIG_OK")
        return 0

    print("Opening Microsoft sign-in in your browser (timeout: 180 seconds)...", flush=True)
    try:
        client = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            timeout=15,
        )
        result = client.acquire_token_interactive(scopes=scopes, timeout=180)
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            report_failure("LOGIN_FAILED", result)
            return 1
        asyncio.run(EntraTokenValidator(settings).validate(token))
    except AuthenticationError as error:
        print(f"LOGIN_FAILED: {error.code}")
        return 1
    except KeyboardInterrupt:
        print("LOGIN_CANCELLED")
        return 130
    except Exception as error:
        # SDK exceptions may contain request details: report only the exception type.
        print(f"LOGIN_FAILED: {type(error).__name__}")
        return 1

    print(
        "LOGIN_OK: backend token signature, issuer, audience, tenant, scopes and client validated"
    )
    if args.workiq_token:
        try:
            backend = msal.ConfidentialClientApplication(
                backend_id,
                client_credential=settings.azure_client_secret.get_secret_value(),
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                timeout=15,
            )
            result = backend.acquire_token_on_behalf_of(
                user_assertion=token, scopes=[settings.workiq_scope]
            )
            workiq_token = result.get("access_token")
            if not isinstance(workiq_token, str) or not workiq_token:
                report_failure("WORKIQ_TOKEN_FAILED", result)
                return 1
        except KeyboardInterrupt:
            print("LOGIN_CANCELLED")
            return 130
        except Exception as error:
            print(f"WORKIQ_TOKEN_FAILED: {type(error).__name__}")
            return 1
        print("WORKIQ_TOKEN_OK: Entra accepted backend credentials and delegated Work IQ scope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
