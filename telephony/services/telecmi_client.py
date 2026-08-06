"""
TeleCMI REST API Client — low-level HTTP adapter.

One function per TeleCMI endpoint. No CRM business logic here.
All functions raise TeleCMIError on non-200 responses or network failures.
"""
import json
import logging

import requests

logger = logging.getLogger(__name__)

TELECMI_BASE_URL = 'https://rest.telecmi.com/v2'
DEFAULT_TIMEOUT = 15  # seconds


class TeleCMIError(Exception):
    """Raised when TeleCMI returns an error response."""
    def __init__(self, message, status_code=None, response_data=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


def _post(path, payload, timeout=DEFAULT_TIMEOUT):
    """Internal POST helper. Returns parsed JSON dict."""
    url = f'{TELECMI_BASE_URL}{path}'
    safe_payload = {
        key: ('***' if key.lower() in {'token', 'secret', 'password'} else value)
        for key, value in payload.items()
    }
    logger.debug('TeleCMI outgoing POST %s payload: %s', path, safe_payload)
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise TeleCMIError(f'Network error calling {url}: {exc}')

    try:
        data = response.json()
    except ValueError:
        raise TeleCMIError(
            f'Non-JSON response from {url}: {response.text[:200]}',
            status_code=response.status_code,
        )

    if response.status_code not in (200, 201) or data.get('code') not in (200, 201, None):
        error_code = data.get('code', response.status_code)
        msg = data.get('msg') or data.get('error') or f'TeleCMI error {error_code}'
        logger.warning('TeleCMI API error %s on %s: %s', error_code, path, msg)
        logger.debug('TeleCMI error response body for %s: %s', path, response.text)
        raise TeleCMIError(msg, status_code=error_code, response_data=data)

    return data


# ──────────────────────────────────────────────
# Authentication
# ──────────────────────────────────────────────

def get_user_login_token(telecmi_user_id: str, password: str) -> str:
    """
    POST /v2/user/login
    Returns the user login token string.
    """
    data = _post('/user/login', {'id': telecmi_user_id, 'password': password})
    token = data.get('token')
    if not token:
        raise TeleCMIError('Login succeeded but no token in response', response_data=data)
    return token


def get_admin_token(app_id: str, secret: str) -> str:
    """
    POST /v2/token
    Returns the admin token (used for live call barge, etc.).
    """
    data = _post('/token', {'appid': app_id, 'secret': secret})
    token = data.get('token')
    if not token:
        raise TeleCMIError('Admin token request succeeded but no token in response', response_data=data)
    return token


# ──────────────────────────────────────────────
# Call control
# ──────────────────────────────────────────────

def click_to_call(token: str, to_number: str, caller_id: str = None, extra_params: dict = None) -> dict:
    """
    POST /v2/click2call
    Rings the agent's softphone first, then dials to_number.
    Returns {'code': 200, 'msg': 'Call initiated', 'request_id': '...'}.

    TeleCMI expects 'to' and 'callerid' as numeric JSON values, not strings.
    """
    try:
        numeric_to = int(to_number)
    except (ValueError, TypeError) as exc:
        raise TeleCMIError(f'Invalid to_number, expected numeric string: {to_number!r}') from exc

    payload = {'token': token, 'to': numeric_to}
    if caller_id:
        try:
            payload['callerid'] = int(caller_id)
        except (ValueError, TypeError) as exc:
            raise TeleCMIError(f'Invalid caller_id, expected numeric string: {caller_id!r}') from exc
    if extra_params:
        payload['extra_params'] = extra_params
    return _post('/click2call', payload)


def hangup_call(token: str, cmiuuid: str) -> dict:
    """
    POST /v2/c2c/hangup
    Hangs up an active call identified by cmiuuid (Leg B identifier).
    """
    return _post('/c2c/hangup', {'token': token, 'cmiuuid': cmiuuid})


# ──────────────────────────────────────────────
# CDR — Call Detail Records
# ──────────────────────────────────────────────

def get_incoming_cdr(
    token: str,
    call_type: int,
    from_ts: int,
    to_ts: int,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """
    POST /v2/user/in_cdr
    call_type: 0 = missed, 1 = answered
    from_ts / to_ts: UTC millisecond timestamps.
    Returns {'count': N, 'cdr': [...], 'code': 200}.
    """
    return _post('/user/in_cdr', {
        'type': call_type,
        'token': token,
        'from': from_ts,
        'to': to_ts,
        'page': page,
        'limit': limit,
    })


def get_outgoing_cdr(
    token: str,
    call_type: int,
    from_ts: int,
    to_ts: int,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """
    POST /v2/user/out_cdr
    call_type: 0 = missed, 1 = answered.
    """
    return _post('/user/out_cdr', {
        'type': call_type,
        'token': token,
        'from': from_ts,
        'to': to_ts,
        'page': page,
        'limit': limit,
    })


def get_callbacks(
    token: str,
    from_ts: int,
    to_ts: int,
    page: int = 1,
    limit: int = 10,
) -> dict:
    """
    POST /v2/callback
    Returns callback (scheduled call-back) records for this agent.
    """
    return _post('/callback', {
        'token': token,
        'from': from_ts,
        'to': to_ts,
        'page': page,
        'limit': limit,
    })


# ──────────────────────────────────────────────
# SMS
# ──────────────────────────────────────────────

def send_sms(token: str, to_number: str, text: str) -> dict:
    """
    POST /v2/messages
    Sends an SMS from the agent's virtual number.
    """
    return _post('/messages', {'token': token, 'to': to_number, 'text': text})


# ──────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────

def add_note(
    token: str,
    caller_name: str,
    from_number: str,
    timestamp_ms: int,
    message: str,
) -> dict:
    """
    POST /v2/user/notes/add
    Adds a note to a call record in TeleCMI.
    """
    return _post('/user/notes/add', {
        'token': token,
        'name': caller_name,
        'from': from_number,
        'date': timestamp_ms,
        'msg': message,
    })


def get_notes(token: str, from_ts: int, to_ts: int) -> dict:
    """
    POST /v2/user/notes (inferred path — check TeleCMI docs for exact URL)
    """
    return _post('/user/notes', {'token': token, 'from': from_ts, 'to': to_ts})


# ──────────────────────────────────────────────
# Caller ID
# ──────────────────────────────────────────────

def get_caller_ids(token: str) -> dict:
    """
    POST /v2/get_callerid
    Returns list of caller IDs available to this agent.
    """
    return _post('/get_callerid', {'token': token})


def set_caller_id(token: str, caller_id: str) -> dict:
    """
    POST /v2/set_callerid
    Updates the active caller ID for this agent.
    """
    return _post('/set_callerid', {'token': token, 'callerid': caller_id})


# ──────────────────────────────────────────────
# Call recordings
# ──────────────────────────────────────────────

def stream_recording(app_id: str, secret: str, filename: str):
    """
    GET /v2/play?appid=...&secret=...&file=...
    Streams the call recording audio file from TeleCMI.

    Returns a requests.Response object (streaming=True) so the caller
    can iterate chunks. The caller is responsible for closing it.

    Raises TeleCMIError on auth failure, missing file, or network errors.
    """
    url = f'{TELECMI_BASE_URL}/play'
    try:
        response = requests.get(
            url,
            params={'appid': app_id, 'secret': secret, 'file': filename},
            timeout=30,
            stream=True,
        )
    except requests.RequestException as exc:
        raise TeleCMIError(f'Network error fetching recording: {exc}')

    if response.status_code == 407:
        raise TeleCMIError('TeleCMI authentication failed for recording', status_code=407)
    if response.status_code == 404:
        raise TeleCMIError('Recording file not found on TeleCMI', status_code=404)
    if response.status_code != 200:
        raise TeleCMIError(
            f'TeleCMI recording error {response.status_code}',
            status_code=response.status_code,
        )

    # TeleCMI signals recording failures with HTTP 200 and a small JSON error
    # body (the same "200 with code in the payload" convention _post() already
    # handles). Without this check we happily stream ~48 bytes of JSON to the
    # browser labelled as audio: the player mounts, shows 0:00 and never plays,
    # and nothing anywhere reports an error because the status was 200.
    content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    is_audio = content_type.startswith('audio/') or content_type in (
        'application/octet-stream',
        'binary/octet-stream',
        '',
    )
    if not is_audio:
        body = response.text[:500]
        response.close()
        message = f'TeleCMI returned {content_type or "an unknown type"} instead of audio'
        code = None
        try:
            payload = json.loads(body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            code = payload.get('code')
            message = payload.get('msg') or payload.get('error') or message
        logger.warning(
            'TeleCMI /play returned non-audio content (%s) for file %s: %s',
            content_type, filename, body,
        )
        raise TeleCMIError(message, status_code=code or 502, response_data=body)

    # A zero-byte body is likewise unplayable — fail loudly rather than handing
    # the browser an empty blob that renders as a 0:00 player.
    if response.headers.get('Content-Length') == '0':
        response.close()
        raise TeleCMIError('TeleCMI returned an empty recording file', status_code=404)

    return response


# ──────────────────────────────────────────────
# Break management
# ──────────────────────────────────────────────

def get_break_records(token: str, from_date_ms: int = None) -> dict:
    """
    POST /v2/user_get_break
    Returns break activity records for this agent.
    from_date_ms: optional UTC millisecond timestamp (defaults to last 24h).
    """
    payload = {'token': token}
    if from_date_ms is not None:
        payload['from_date'] = from_date_ms
    return _post('/user_get_break', payload)
