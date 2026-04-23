import logging
from typing import Optional

def get_google_oidc_token(audience: str, logger) -> Optional[str]:
    """Fetch Google OIDC ID token for the given audience."""
    logger.log_info(f"[telemetry] Attempting to fetch OIDC token for audience: {audience}")
    try:
        import google.auth
        import google.auth.transport.requests
        from google.oauth2 import id_token

        credentials, project = google.auth.default()
        auth_req = google.auth.transport.requests.Request()
        token = id_token.fetch_id_token(auth_req, audience=audience)
        return token
    except ImportError:
        logger.log_info("[telemetry] google-auth not installed, skipping OIDC token fetch.")
        return None
    except Exception as e:
        logger.log_info(f"[telemetry] Could not fetch OIDC token: {e}")
        return None

def get_google_bearer_token_provider(endpoint: str, logger):
    """Returns a function that fetches a Google OIDC token and adds Bearer prefix."""
    from urllib.parse import urlparse
    parsed_url = urlparse(endpoint)
    audience = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    def get_bearer():
        t = get_google_oidc_token(audience, logger)
        return f"Bearer {t}" if t else None
        
    return get_bearer
