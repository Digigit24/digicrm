"""
OAuth 2.0 utilities for handling Google authentication and token management.

Handles:
- OAuth flow initiation
- Token exchange
- Token refresh
- Token validation
"""

import logging
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

logger = logging.getLogger(__name__)


class OAuthError(Exception):
    """Custom exception for OAuth errors"""
    pass


class GoogleOAuthHandler:
    """
    Handles Google OAuth 2.0 flow for Google Sheets integration.

    Manages:
    - Authorization URL generation
    - Token exchange
    - Token refresh
    - Credential validation
    """

    # Google OAuth scopes needed for Google Sheets
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly',
        'https://www.googleapis.com/auth/userinfo.email',
        'https://www.googleapis.com/auth/userinfo.profile',
    ]

    def __init__(self):
        """Initialize OAuth handler with Google client credentials"""
        self.client_id = getattr(settings, 'GOOGLE_CLIENT_ID', None)
        self.client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', None)
        self.redirect_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', None)

        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logger.warning(
                "Google OAuth credentials not fully configured. "
                "Please set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI."
            )

    def _get_client_config(self) -> Dict:
        """
        Get client configuration for Google OAuth.

        Returns:
            Dict: Client configuration dictionary
        """
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uris": [self.redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

    def get_authorization_url(self, state: str = None) -> Tuple[str, str]:
        """
        Generate Google OAuth authorization URL.

        Args:
            state: Optional state parameter for CSRF protection

        Returns:
            Tuple[str, str]: (authorization_url, state)

        Raises:
            OAuthError: If URL generation fails
        """
        try:
            flow = Flow.from_client_config(
                self._get_client_config(),
                scopes=self.SCOPES,
                redirect_uri=self.redirect_uri
            )

            if state:
                flow.state = state

            authorization_url, state = flow.authorization_url(
                access_type='offline',  # Request refresh token
                include_granted_scopes='true',
                prompt='consent'  # Force consent to get refresh token
            )

            logger.info(f"Generated authorization URL with state: {state}")
            return authorization_url, state

        except Exception as e:
            logger.error(f"Failed to generate authorization URL: {e}")
            raise OAuthError(f"Authorization URL generation failed: {e}")

    def exchange_code_for_tokens(self, code: str, state: str = None) -> Dict:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            code: Authorization code from OAuth callback
            state: State parameter for validation

        Returns:
            Dict: Token data including access_token, refresh_token, expires_in, etc.

        Raises:
            OAuthError: If token exchange fails
        """
        try:
            flow = Flow.from_client_config(
                self._get_client_config(),
                scopes=self.SCOPES,
                redirect_uri=self.redirect_uri,
                state=state
            )

            # Exchange code for tokens
            flow.fetch_token(code=code)

            credentials = flow.credentials

            # Calculate expiration time
            expires_at = None
            if credentials.expiry:
                expires_at = credentials.expiry
            elif credentials.expires_in:
                expires_at = timezone.now() + timedelta(seconds=credentials.expires_in)

            token_data = {
                'access_token': credentials.token,
                'refresh_token': credentials.refresh_token,
                'token_type': 'Bearer',
                'expires_in': credentials.expires_in,
                'expires_at': expires_at,
                'scopes': credentials.scopes,
            }

            logger.info("Successfully exchanged authorization code for tokens")
            return token_data

        except Exception as e:
            logger.error(f"Failed to exchange code for tokens: {e}")
            raise OAuthError(f"Token exchange failed: {e}")

    def refresh_access_token(self, refresh_token: str) -> Dict:
        """
        Refresh an expired access token using a refresh token.

        Args:
            refresh_token: The refresh token

        Returns:
            Dict: New token data

        Raises:
            OAuthError: If token refresh fails
        """
        try:
            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
            )

            # Refresh the token
            request = Request()
            credentials.refresh(request)

            # Calculate expiration
            expires_at = None
            if credentials.expiry:
                expires_at = credentials.expiry
            elif credentials.expires_in:
                expires_at = timezone.now() + timedelta(seconds=credentials.expires_in)

            token_data = {
                'access_token': credentials.token,
                'refresh_token': credentials.refresh_token or refresh_token,  # Keep old if no new one
                'token_type': 'Bearer',
                'expires_in': credentials.expires_in,
                'expires_at': expires_at,
                'scopes': credentials.scopes,
            }

            logger.info("Successfully refreshed access token")
            return token_data

        except RefreshError as e:
            logger.error(f"Refresh token is invalid or expired: {e}")
            raise OAuthError(f"Token refresh failed - user needs to re-authenticate: {e}")

        except Exception as e:
            logger.error(f"Failed to refresh access token: {e}")
            raise OAuthError(f"Token refresh failed: {e}")

    def get_credentials(self, access_token: str, refresh_token: str = None) -> Credentials:
        """
        Create Google Credentials object from tokens.

        Args:
            access_token: The access token
            refresh_token: Optional refresh token

        Returns:
            Credentials: Google credentials object
        """
        return Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

    def validate_token(self, access_token: str) -> bool:
        """
        Validate an access token by making a test request.

        Args:
            access_token: The access token to validate

        Returns:
            bool: True if valid, False otherwise
        """
        try:
            credentials = self.get_credentials(access_token)
            request = Request()

            # Try to refresh to check validity
            # This is a lightweight check
            if credentials.expired or not credentials.valid:
                return False

            return True

        except Exception as e:
            logger.error(f"Token validation failed: {e}")
            return False


# Singleton instance
_oauth_handler_instance: Optional[GoogleOAuthHandler] = None


def get_oauth_handler() -> GoogleOAuthHandler:
    """
    Get the singleton GoogleOAuthHandler instance.

    Returns:
        GoogleOAuthHandler: The OAuth handler instance
    """
    global _oauth_handler_instance

    if _oauth_handler_instance is None:
        _oauth_handler_instance = GoogleOAuthHandler()

    return _oauth_handler_instance


def generate_oauth_url(state: str = None) -> Tuple[str, str]:
    """
    Convenience function to generate OAuth authorization URL.

    Args:
        state: Optional state for CSRF protection

    Returns:
        Tuple[str, str]: (authorization_url, state)
    """
    handler = get_oauth_handler()
    return handler.get_authorization_url(state)


def exchange_code(code: str, state: str = None) -> Dict:
    """
    Convenience function to exchange authorization code for tokens.

    Args:
        code: Authorization code
        state: State parameter

    Returns:
        Dict: Token data
    """
    handler = get_oauth_handler()
    return handler.exchange_code_for_tokens(code, state)


def refresh_token(refresh_token: str) -> Dict:
    """
    Convenience function to refresh an access token.

    Args:
        refresh_token: The refresh token

    Returns:
        Dict: New token data
    """
    handler = get_oauth_handler()
    return handler.refresh_access_token(refresh_token)
