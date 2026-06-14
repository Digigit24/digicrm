#!/usr/bin/env python3
"""
DigiCRM MCP HTTP Test Suite — all 31 tools
Usage:
    python mcp/test_http.py --url https://crm.celiyo.com/mcp/sse --secret 'letmegoin@0008'
    python mcp/test_http.py --tool list_leads
    python mcp/test_http.py --dry-run      # skip write/destructive calls
"""

import os, sys, json, argparse, requests
from datetime import datetime, timedelta, timezone as tz

GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--url',     default=os.environ.get('MCP_URL', 'http://localhost:8000/mcp/sse'))
    p.add_argument('--secret',  default=os.environ.get('MCP_SECRET', ''))
    p.add_argument('--tool',    help='Run only this tool')
    p.add_argument('--dry-run', action='store_true', help='Skip write tools')
    p.add_argument('--timeout', type=int, default=20)
    return p.parse_args()


class MCPClient:
    def __init__(self, url, secret, timeout=20):
        self.url     = url
        self.timeout = timeout
        self.headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if secret:
            self.headers['Authorization'] = 'Bearer ' + secret
        self._id = 0

    def _call(self, method, params=None):
        self._id += 1
        body = {'jsonrpc': '2.0', 'id': self._id, 'method': method}
        if params is not None:
            body['params'] = params
        try:
            r = requests.post(self.url, json=body, headers=self.headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError('Cannot connect to %s: %s' % (self.url, e))
        except requests.exceptions.HTTPError as e:
            raise RuntimeError('HTTP %s: %s' % (r.status_code, r.text[:200]))
        except Exception as e:
            raise RuntimeError(str(e))
        if 'error' in data:
            err = data['error']
            raise RuntimeError('JSON-RPC %s: %s' % (err.get('code'), err.get('message')))
        return data.get('result')

    def initialize(self):
        return self._call('initialize', {
            'protocolVersion': '2025-03-26',
            'capabilities': {},
            'clientInfo': {'name': 'test_http', 'version': '1.0'},
        })

    def tools_list(self):
        r = self._call('tools/list')
        return [t['name'] for t in r.get('tools', [])]

    def tool_call(self, name, arguments):
        r = self._call('tools/call', {'name': name, 'arguments': arguments})
        if r and r.get('content'):
            text = r['content'][0].get('text', '')
            try:
                return json.loads(text)
            except Exception:
                return text
        return r


def run_all(args):
    client  = MCPClient(args.url, args.secret, args.timeout)
    dry_run = args.dry_run
    only    = args.tool
    results = {'passed': 0, 'failed': 0, 'skipped': 0}
    sample  = {}

    print('\n%s%sDigiCRM MCP HTTP Test Suite — 31 tools%s' % (BOLD, CYAN, RESET))
    print('%sURL   : %s%s' % (CYAN, args.url, RESET))
    print('%sAuth  : %s%s' % (CYAN, ('Bearer ***' if args.secret else 'none'), RESET))
    print('%sDry   : %s%s\n' % (CYAN, dry_run, RESET))

    # ── Handshake ────────────────────────────────────────────────────────────────
    print('%s── Handshake %s' % (BOLD, RESET))
    try:
        info  = client.initialize()
        proto = info.get('protocolVersion', '?')
        sname = info.get('serverInfo', {}).get('name', '?')
        print('  %sPASS%s  initialize  protocol=%s  server=%s' % (GREEN, RESET, proto, sname))
    except Exception as e:
        print('  %sFAIL%s  initialize  %s' % (RED, RESET, e))
        print('\n%sCannot reach MCP server — aborting.%s' % (RED, RESET))
        sys.exit(1)

    try:
        tool_names = client.tools_list()
        print('  %sPASS%s  tools/list  %d tools' % (GREEN, RESET, len(tool_names)))
    except Exception as e:
        print('  %sFAIL%s  tools/list  %s' % (RED, RESET, e))
        tool_names = []

    def run(tool_name, arguments, *, write=False, label=None):
        if only and tool_name != only:
            return None
        if tool_names and tool_name not in tool_names:
            print('  %sMISS%s  %s  (not in tools/list)' % (YELLOW, RESET, tool_name))
            results['skipped'] += 1
            return None
        if write and dry_run:
            print('  %sSKIP%s  %s  (dry-run)' % (YELLOW, RESET, tool_name))
            results['skipped'] += 1
            return None
        try:
            result = client.tool_call(tool_name, arguments)
            preview = json.dumps(result, default=str)[:160]
            print('  %sPASS%s  %s' % (GREEN, RESET, tool_name))
            if label:
                print('        %s' % label)
            print('        %s' % preview)
            results['passed'] += 1
            return result
        except Exception as exc:
            print('  %sFAIL%s  %s' % (RED, RESET, tool_name))
            print('        %s' % exc)
            results['failed'] += 1
            return None

    # ── Phase 1: CRM Core ────────────────────────────────────────────────────────
    print('\n%s── Phase 1: CRM Core (10 tools)%s' % (BOLD, RESET))

    run('list_lead_statuses', {})

    r = run('list_leads', {'page': 1, 'page_size': 3})
    if r and r.get('results'):
        sample['lead_id']   = r['results'][0]['id']
        sample['lead_name'] = r['results'][0].get('name', '')
        print('        i sample lead id=%s  name=%s' % (sample['lead_id'], sample['lead_name']))

    if sample.get('lead_id'):
        run('list_leads', {'search': sample['lead_name'][:6], 'page': 1, 'page_size': 5},
            label='search=%s...' % sample['lead_name'][:6])

    if sample.get('lead_id'):
        run('get_lead', {'lead_id': sample['lead_id']})

    new = run('create_lead', {
        'name': '_MCP_TEST', 'phone': '0000000001',
        'email': 'mcp@test.local', 'source': 'mcp_test',
    }, write=True)
    if new:
        sample['new_lead_id'] = new.get('id')
        sample.setdefault('lead_id', new.get('id'))

    target = sample.get('new_lead_id') or sample.get('lead_id')

    if target:
        run('update_lead', {'lead_id': target, 'notes': 'MCP test run'}, write=True)

    if sample.get('lead_id'):
        r = run('list_lead_statuses', {})
        if r and r.get('results') and target:
            status_id = r['results'][0]['id']
            run('update_lead_status', {'lead_id': target, 'status_id': status_id}, write=True)

    if target:
        run('bulk_import_leads', {
            'leads': [
                {'name': '_BULK1', 'phone': '0000000002', 'source': 'mcp_test'},
                {'name': '_BULK2', 'phone': '0000000003', 'source': 'mcp_test'},
            ]
        }, write=True)

    if sample.get('lead_id'):
        run('create_lead_activity', {
            'lead_id': sample['lead_id'],
            'type':    'NOTE',
            'content': 'MCP test note',
        }, write=True)

    if sample.get('lead_id'):
        run('add_lead_to_group', {
            'lead_id': sample['lead_id'],
            'lead_group_id': 1,
        }, write=True)

    # ── Phase 1: Tasks & Meetings ────────────────────────────────────────────────
    print('\n%s── Phase 1: Tasks & Meetings%s' % (BOLD, RESET))

    if sample.get('lead_id'):
        t = run('create_task', {
            'title': '_MCP_TEST_TASK', 'lead_id': sample['lead_id'],
            'priority': 'LOW', 'description': 'MCP test',
        }, write=True)
        if t:
            sample['task_id'] = t.get('id')
    else:
        print('  %sSKIP%s  create_task (no lead_id)' % (YELLOW, RESET))
        results['skipped'] += 1

    if sample.get('task_id'):
        run('update_task', {'task_id': sample['task_id'], 'status': 'IN_PROGRESS'}, write=True)

    now = datetime.now(tz=tz.utc)
    if sample.get('lead_id'):
        m = run('create_meeting', {
            'lead_id': sample['lead_id'],
            'title':      '_MCP_TEST_MEETING',
            'start_time': (now + timedelta(hours=1)).isoformat(),
            'end_time':   (now + timedelta(hours=2)).isoformat(),
            'notes':      'MCP test',
        }, write=True)
        if m:
            sample['meeting_id'] = m.get('id')
    else:
        print('  %sSKIP%s  create_meeting (no lead_id)' % (YELLOW, RESET))
        results['skipped'] += 1

    if sample.get('meeting_id'):
        run('update_meeting', {
            'meeting_id': sample['meeting_id'],
            'notes': 'Updated by MCP test',
        }, write=True)

    # ── Phase 2: WhatsApp (reads) ─────────────────────────────────────────────────
    print('\n%s── Phase 2: WhatsApp Reads (3 tools)%s' % (BOLD, RESET))

    if sample.get('lead_id'):
        run('get_lead_chat',        {'lead_id': sample['lead_id']})
        run('get_lead_enrollments', {'lead_id': sample['lead_id']})
    else:
        print('  %sSKIP%s  get_lead_chat / get_lead_enrollments (no lead_id)' % (YELLOW, RESET))
        results['skipped'] += 2

    run('get_whatsapp_templates', {})

    # ── Phase 2: WhatsApp (writes) ────────────────────────────────────────────────
    print('\n%s── Phase 2: WhatsApp Writes (7 tools) — needs WA creds%s' % (BOLD, RESET))
    print('   (These will fail if WA_VENDOR_UID/WA_API_TOKEN not set)')

    if sample.get('lead_id'):
        try:
            templates_r = client.tool_call('get_whatsapp_templates', {}) if not dry_run else None
        except Exception:
            templates_r = None
        t_uid = None
        if templates_r and templates_r.get('results'):
            t_uid = templates_r['results'][0].get('_uid') or templates_r['results'][0].get('uid') or templates_r['results'][0].get('id')

        if t_uid:
            run('send_whatsapp_template', {
                'lead_id': sample['lead_id'], 'template_uid': t_uid,
            }, write=True)
            run('agent_send_whatsapp', {
                'lead_id': sample['lead_id'], 'template_uid': t_uid,
            }, write=True)
        else:
            print('  %sSKIP%s  send_whatsapp_template / agent_send_whatsapp (no template UID)' % (YELLOW, RESET))
            results['skipped'] += 2

        run('send_whatsapp_text', {
            'lead_id': sample['lead_id'], 'text': 'MCP test message',
        }, write=True)
        run('mark_chat_read',   {'lead_id': sample['lead_id']}, write=True)
        run('assign_lead_chat_user', {'lead_id': sample['lead_id'], 'user_uid': 'test-user-uid'}, write=True)
        run('block_whatsapp_contact', {'lead_id': sample['lead_id'], 'block': False}, write=True)
    else:
        print('  %sSKIP%s  WhatsApp write tools (no lead_id)' % (YELLOW, RESET))
        results['skipped'] += 6

    run('log_agent_activity', {
        'action_type': 'LOG_ACTIVITY',
        'summary': 'MCP test run completed',
    }, write=True)

    # ── Phase 3: Sequences ────────────────────────────────────────────────────────
    print('\n%s── Phase 3: Sequences (4 tools)%s' % (BOLD, RESET))

    seq = run('create_sequence', {
        'name': '_MCP_TEST_SEQ',
        'description': 'MCP test sequence',
        'stop_on_reply': True,
    }, write=True)
    if seq:
        sample['seq_id'] = seq.get('id')

    if sample.get('seq_id'):
        step = run('add_sequence_step', {
            'sequence_id':  sample['seq_id'],
            'step_number':  1,
            'delay_days':   0,
            'template_uid': 'placeholder_uid',
            'template_name': 'placeholder',
        }, write=True)
        if step:
            sample['step_id'] = step.get('id')

    if sample.get('step_id'):
        run('update_sequence_step', {
            'step_id':   sample['step_id'],
            'delay_days': 1,
        }, write=True)
        run('delete_sequence_step', {'step_id': sample['step_id']}, write=True)

    # ── Phase 3: Enrollment ───────────────────────────────────────────────────────
    print('\n%s── Phase 3: Enrollments (4 tools)%s' % (BOLD, RESET))

    if sample.get('lead_id') and sample.get('seq_id'):
        enr = run('enroll_lead_in_sequence', {
            'lead_id': sample['lead_id'], 'sequence_id': sample['seq_id'],
        }, write=True)
        if enr:
            sample['enrollment_id'] = enr.get('id')
    else:
        print('  %sSKIP%s  enroll_lead_in_sequence (missing lead_id or seq_id)' % (YELLOW, RESET))
        results['skipped'] += 1

    if sample.get('enrollment_id'):
        run('pause_enrollment',  {'enrollment_id': sample['enrollment_id']}, write=True)
        run('resume_enrollment', {'enrollment_id': sample['enrollment_id']}, write=True)
        run('unenroll_lead',     {'enrollment_id': sample['enrollment_id']}, write=True)

    # ── Phase 3: Campaigns ────────────────────────────────────────────────────────
    print('\n%s── Phase 3: Campaigns (3 tools)%s' % (BOLD, RESET))

    camp = run('create_campaign', {
        'name': '_MCP_TEST_CAMP',
        'lead_group_id': 1,
        'template_uid':  'placeholder_uid',
        'template_name': 'placeholder',
    }, write=True)
    if camp:
        sample['campaign_id'] = camp.get('id')

    if sample.get('campaign_id'):
        run('launch_campaign', {'campaign_id': sample['campaign_id']}, write=True)
        run('get_campaign_analytics', {'campaign_id': sample['campaign_id']})

    # ── Summary ───────────────────────────────────────────────────────────────────
    total = results['passed'] + results['failed'] + results['skipped']
    print('\n%s%s%s' % (BOLD, '─' * 55, RESET))
    print('%sResults: %s%d passed%s  %s%d failed%s  %s%d skipped%s  / %d total\n' % (
        BOLD,
        GREEN,  results['passed'],  RESET,
        RED,    results['failed'],  RESET,
        YELLOW, results['skipped'], RESET,
        total,
    ))

    if results['failed']:
        sys.exit(1)


def main():
    run_all(parse_args())

if __name__ == '__main__':
    main()
