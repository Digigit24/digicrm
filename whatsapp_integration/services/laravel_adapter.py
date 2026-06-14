"""
laravel_adapter.py

Single service class that wraps ALL calls from DigiCRM to the Laravel WhatsApp adapter.

Rules:
- Only this file ever calls Laravel directly. No other file in DigiCRM does.
- All methods raise LaravelAdapterError on failure so callers can handle gracefully.
- Response data is always returned as plain Python dicts.
"""

import logging
import requests
from django.core.cache import cache

from whatsapp_integration.models import WhatsAppVendorConfig

logger = logging.getLogger(__name__)


class LaravelAdapterError(Exception):
    """Raised when the Laravel adapter returns an error or is unreachable."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def _get_vendor_config(tenant_id: str) -> WhatsAppVendorConfig:
    """Fetch the active WhatsApp vendor config for a tenant."""
    try:
        return WhatsAppVendorConfig.objects.get(tenant_id=tenant_id, is_active=True)
    except WhatsAppVendorConfig.DoesNotExist:
        raise LaravelAdapterError(
            f"No active WhatsApp vendor config found for tenant {tenant_id}. "
            "Please configure it in Admin → WhatsApp Vendor Config.",
            status_code=503
        )


def _make_request(method: str, url: str, token: str, **kwargs) -> dict:
    """Internal helper: make authenticated HTTP request to Laravel adapter."""
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    try:
        resp = requests.request(
            method, url,
            headers=headers,
            timeout=15,
            **kwargs
        )
    except requests.exceptions.Timeout:
        raise LaravelAdapterError("Laravel adapter request timed out", status_code=504)
    except requests.exceptions.ConnectionError as e:
        raise LaravelAdapterError(f"Cannot connect to Laravel adapter: {e}", status_code=503)

    try:
        data = resp.json()
    except Exception:
        raise LaravelAdapterError(
            f"Laravel adapter returned non-JSON response (HTTP {resp.status_code})",
            status_code=502
        )

    if resp.status_code >= 400:
        msg = data.get('message') or data.get('error') or f"HTTP {resp.status_code}"
        raise LaravelAdapterError(f"Laravel adapter error: {msg}", status_code=resp.status_code)

    return data


class LaravelWhatsAppAdapter:
    """
    Facade for all DigiCRM → Laravel WhatsApp API calls.

    Preferred — pass credentials from request headers (no DB lookup):
        adapter = LaravelWhatsAppAdapter(
            tenant_id=request.tenant_id,
            vendor_uid=request.headers.get('X-WA-Vendor-Uid'),
            api_token=request.headers.get('X-WA-Api-Token'),
        )

    Fallback — if headers absent, reads WhatsAppVendorConfig from DB:
        adapter = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
    """

    DEFAULT_BASE_URL = 'https://whatsappapi.celiyo.com/api'

    def __init__(
        self,
        tenant_id: str,
        vendor_uid: str = None,
        api_token: str = None,
        base_url: str = None,
    ):
        self.tenant_id = str(tenant_id)

        if vendor_uid and api_token:
            # Credentials supplied directly (e.g. from request headers)
            self.vendor_uid = vendor_uid
            self.api_token  = api_token
            self.base_url   = (base_url or self.DEFAULT_BASE_URL).rstrip('/')
        else:
            # Fall back to DB lookup
            config = _get_vendor_config(self.tenant_id)
            self.vendor_uid = config.vendor_uid
            self.api_token  = config.api_token
            self.base_url   = config.api_base_url.rstrip('/')

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{self.vendor_uid}/adapter/{path.lstrip('/')}"

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """
        Strip non-digits and ensure Indian country code (91) is prepended.
        DigiCRM stores 10-digit numbers — WhatsApp Cloud API needs full E.164 (without +).
        Examples:
            '9876543210'    → '919876543210'
            '+919876543210' → '919876543210'
            '919876543210'  → '919876543210'  (already correct, untouched)
        """
        digits = ''.join(filter(str.isdigit, phone or ''))
        if len(digits) == 10:
            digits = '91' + digits
        return digits

    # ------------------------------------------------------------------
    # 1. SEND SINGLE MESSAGE TO A PHONE NUMBER
    # ------------------------------------------------------------------
    def send_message(
        self,
        phone: str,
        name: str,
        template_uid: str,
        template_components: list = None,
        digicrm_lead_id: int = None,
    ) -> dict:
        """
        Send a single WhatsApp template message to a phone number.

        Returns dict with: wa_message_id, contact_uid, digicrm_lead_id
        """
        payload = {
            'phone': self._normalize_phone(phone),
            'name': name,
            'template_uid': template_uid,
            'template_components': template_components or [],
            'digicrm_lead_id': digicrm_lead_id,
        }
        return _make_request('POST', self._url('messages/send'), self.api_token, json=payload)

    # ------------------------------------------------------------------
    # 2. CREATE CAMPAIGN FROM A LIST OF CONTACTS
    # ------------------------------------------------------------------
    def create_campaign(
        self,
        name: str,
        contacts: list,
        template_uid: str,
        template_components: list = None,
        scheduled_at: str = None,
        digicrm_campaign_id: int = None,
        timezone: str = 'Asia/Kolkata',
    ) -> dict:
        """
        Create and queue a campaign in Laravel from a raw contact list.

        contacts: list of { "phone": "91...", "name": "..." }
        scheduled_at: ISO 8601 string or None for immediate
        Returns dict with: campaign_uid, group_uid, total_contacts, scheduled_at
        """
        # Normalize all contact phone numbers before sending
        normalized_contacts = [
            {**c, 'phone': self._normalize_phone(c.get('phone', ''))}
            for c in contacts
        ]
        payload = {
            'name': name,
            'template_uid': template_uid,
            'template_components': template_components or [],
            'contacts': normalized_contacts,
            'timezone': timezone,
            'digicrm_campaign_id': digicrm_campaign_id,
        }
        if scheduled_at:
            payload['scheduled_at'] = scheduled_at

        return _make_request(
            'POST',
            self._url('campaigns/from-contacts'),
            self.api_token,
            json=payload
        )

    # ------------------------------------------------------------------
    # 3. CAMPAIGN ANALYTICS (cached 5 minutes)
    # ------------------------------------------------------------------
    def get_campaign_analytics(self, campaign_uid: str) -> dict:
        """
        Get delivery stats for a campaign from Laravel.
        Results cached for 5 minutes to reduce API load.
        """
        cache_key = f'wa_campaign_analytics_{campaign_uid}'
        cached = cache.get(cache_key)
        if cached:
            return cached

        result = _make_request(
            'GET',
            self._url(f'campaigns/{campaign_uid}/analytics'),
            self.api_token
        )
        cache.set(cache_key, result, timeout=300)  # 5 min
        return result

    # ------------------------------------------------------------------
    # 4. CAMPAIGN REPLIES
    # ------------------------------------------------------------------
    def get_campaign_replies(self, campaign_uid: str, page: int = 1, per_page: int = 50) -> dict:
        """
        Get list of contacts who replied to a campaign.
        Used to create follow-up segments.
        """
        return _make_request(
            'GET',
            self._url(f'campaigns/{campaign_uid}/replies'),
            self.api_token,
            params={'page': page, 'per_page': per_page}
        )

    # ------------------------------------------------------------------
    # 5. SEND PLAIN TEXT MESSAGE TO A PHONE NUMBER
    # ------------------------------------------------------------------
    def send_text_message(
        self,
        phone: str,
        name: str,
        text: str,
        digicrm_lead_id: int = None,
    ) -> dict:
        """
        Send a plain text WhatsApp message (requires 24h window open).
        """
        payload = {
            'phone': self._normalize_phone(phone),
            'name': name,
            'text': text,
            'digicrm_lead_id': digicrm_lead_id,
        }
        return _make_request('POST', self._url('messages/send-text'), self.api_token, json=payload)

    # ------------------------------------------------------------------
    # 6. GET CHAT HISTORY BY PHONE
    # ------------------------------------------------------------------
    def get_chat_history(self, phone: str, page: int = 1, per_page: int = 50) -> dict:
        """Fetch paginated WhatsApp message history for a phone number."""
        normalized = self._normalize_phone(phone)
        return _make_request(
            'GET',
            self._url(f'contacts/by-phone/{normalized}/messages'),
            self.api_token,
            params={'page': page, 'per_page': per_page}
        )

    # ------------------------------------------------------------------
    # 7. ASSIGN CHAT USER (BY PHONE)
    # ------------------------------------------------------------------
    def assign_chat_user(self, phone: str, user_uid: str) -> dict:
        """Assign a WhatsApp chat to a team member by phone number."""
        normalized = self._normalize_phone(phone)
        return _make_request(
            'POST',
            self._url(f'contacts/by-phone/{normalized}/assign-user'),
            self.api_token,
            json={'user_uid': user_uid}
        )

    # ------------------------------------------------------------------
    # 8. MARK CHAT AS READ (BY PHONE)
    # ------------------------------------------------------------------
    def mark_chat_read(self, phone: str) -> dict:
        """Mark all messages in a WhatsApp chat as read, by phone number."""
        normalized = self._normalize_phone(phone)
        return _make_request(
            'POST',
            self._url(f'contacts/by-phone/{normalized}/mark-read'),
            self.api_token,
            json={}
        )

    # ------------------------------------------------------------------
    # 9. BLOCK / UNBLOCK WHATSAPP CONTACT (BY PHONE)
    # ------------------------------------------------------------------
    def block_contact(self, phone: str, block: bool = True) -> dict:
        """Block or unblock a WhatsApp contact by phone number."""
        normalized = self._normalize_phone(phone)
        endpoint = 'block' if block else 'unblock'
        return _make_request(
            'POST',
            self._url(f'contacts/by-phone/{normalized}/{endpoint}'),
            self.api_token,
            json={}
        )

    # ------------------------------------------------------------------
    def get_templates(self) -> list:
        """
        List available WhatsApp templates for this vendor.
        Used to populate template dropdowns in the CRM campaign UI.
        Cached 10 minutes.
        """
        cache_key = f'wa_templates_{self.vendor_uid}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Use the existing /templates endpoint (not adapter-specific)
        url = f"{self.base_url}/{self.vendor_uid}/templates"
        result = _make_request('GET', url, self.api_token, params={'per_page': 200})
        templates = result.get('data', result) if isinstance(result, dict) else result
        cache.set(cache_key, templates, timeout=600)  # 10 min
        return templates
