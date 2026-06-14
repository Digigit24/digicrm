"""
config.py — MCP server configuration loaded from environment variables.

Required env vars:
  DIGICRM_BASE_URL   — digicrm Django API base, e.g. http://localhost:8000
  DIGICRM_JWT_TOKEN  — valid JWT for the MCP service account
  DIGICRM_TENANT_ID  — tenant UUID (never from agent input)

Optional:
  MCP_LOG_LEVEL      — logging level, default INFO
"""

import os

DIGICRM_BASE_URL  = os.environ.get('DIGICRM_BASE_URL', 'http://localhost:8000').rstrip('/')
DIGICRM_JWT_TOKEN = os.environ.get('DIGICRM_JWT_TOKEN', '')
DIGICRM_TENANT_ID = os.environ.get('DIGICRM_TENANT_ID', '')
MCP_LOG_LEVEL     = os.environ.get('MCP_LOG_LEVEL', 'INFO')

# WhatsApp vendor credentials (passed to digicrm as headers so it can call Laravel)
WA_VENDOR_UID = os.environ.get('WA_VENDOR_UID', '')
WA_API_TOKEN  = os.environ.get('WA_API_TOKEN', '')
WA_BASE_URL   = os.environ.get('WA_BASE_URL', '')

def validate():
    missing = [k for k, v in {
        'DIGICRM_BASE_URL': DIGICRM_BASE_URL,
        'DIGICRM_JWT_TOKEN': DIGICRM_JWT_TOKEN,
        'DIGICRM_TENANT_ID': DIGICRM_TENANT_ID,
    }.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")
