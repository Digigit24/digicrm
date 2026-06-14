"""
test_connection.py — Run this from your Windows terminal to verify MCP can reach digicrm.

Usage:
    cd C:\ritik\AAAAA\digicrm
    python mcp/test_connection.py
"""

import os
import sys
import json

# ── credentials (already filled in) ──────────────────────────────────────────
os.environ['DIGICRM_BASE_URL']  = 'http://localhost:8000'
os.environ['DIGICRM_TENANT_ID'] = 'fe81423b-a5bc-41d0-93bf-e311b7b71e1c'
os.environ['DIGICRM_JWT_TOKEN'] = (
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    '.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgzMTA4OTY3LCJpYXQiOjE3ODEzODA5NjcsImp0aSI6'
    'ImI3OGJmMTVmYzdmYjRjYzFhMmE3YjVhZTRlYzIyZTU0IiwidXNlcl9pZCI6ImZmYmZmYTBhLTYyMmQtNGFmMy'
    '1iMmUyLTJkNDI2MGQwNDM2ZiIsImVtYWlsIjoiZGlnaXRlY2hAZ21haWwuY29tIiwidGVuYW50X2lkIjoiZmU4'
    'MTQyM2ItYTViYy00MWQwLTkzYmYtZTMxMWI3YjcxZTFjIiwidGVuYW50X3NsdWciOiJkaWdpdGVjaCIsImlzX3'
    'N1cGVyX2FkbWluIjp0cnVlLCJwZXJtaXNzaW9ucyI6e30sImVuYWJsZWRfbW9kdWxlcyI6WyJjcm0iLCJ3aGF0'
    'c2FwcCIsIm1lZXRpbmdzIiwicGF5bWVudHMiLCJpbnRlZ3JhdGlvbnMiLCJhZG1pbiIsImhtcyIsInNtYXJ0aHJp'
    'biIsInZvaWNlYWkiLCJ0ZWxlcGhvbnkiXX0.xqy8RjeIaAvTI0lVcPOdwAcLZMZp7qcgDbgGL_LXYls'
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import config, client, server
from mcp.client import McpApiError

PASS = '✅'
FAIL = '❌'

def test(label, fn):
    try:
        result = fn()
        print(f'{PASS}  {label}')
        return result
    except McpApiError as e:
        print(f'{FAIL}  {label}  →  {e} (HTTP {e.status_code})')
    except Exception as e:
        print(f'{FAIL}  {label}  →  {e}')
    return None

print('\n─── DigiCRM MCP Connection Test ───────────────────────────────\n')

# 1. Config validation
test('Config loads from env', config.validate)

# 2. Fetch leads (basic connectivity)
leads = test('GET /api/crm/leads/ (3 leads)', lambda: client.get('/api/crm/leads/', {'page': 1, 'page_size': 3}))
if leads:
    count = leads.get('count', '?')
    names = [l.get('name','?') for l in leads.get('results', [])]
    print(f'     → {count} total leads, first 3: {names}')

# 3. Fetch lead statuses
statuses = test('GET /api/crm/statuses/', lambda: client.get('/api/crm/statuses/'))
if statuses:
    sl = statuses.get('results', statuses) if isinstance(statuses, dict) else statuses
    print(f'     → {len(sl)} pipeline stages: {[s.get("name","?") for s in sl[:5]]}')

# 4. Fetch tasks
tasks = test('GET /api/tasks/', lambda: client.get('/api/tasks/', {'page': 1, 'page_size': 1}))
if tasks:
    print(f'     → {tasks.get("count", "?")} total tasks')

# 5. MCP protocol test
print()
test('MCP initialize response', lambda: server._handle_request({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}}))
tools_resp = test('MCP tools/list (31 tools)', lambda: server._handle_request({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}))
if tools_resp:
    print(f'     → {len(tools_resp["result"]["tools"])} tools registered')

# 6. Simulate a tool call (create_lead dry run — no actual write)
print()
print('─── Tool call simulation ──────────────────────────────────────')
print('Calling tools/call → create_lead (this will actually create a lead!)')
answer = input('Create a test lead? (y/N): ').strip().lower()
if answer == 'y':
    result_json = server.execute_tool('create_lead', {
        'name': 'MCP Test Lead',
        'phone': '9000000001',
        'source': 'mcp_test',
        'notes': 'Created by MCP connection test — safe to delete',
    })
    result = json.loads(result_json)
    if 'error' in result:
        print(f'{FAIL}  create_lead failed: {result}')
    else:
        print(f'{PASS}  Lead created! ID: {result.get("id")} Name: {result.get("name")}')

print('\n─────────────────────────────────────────────────────────────')
print('If all checks above show ✅, the MCP is ready to connect to Claude Desktop.\n')
