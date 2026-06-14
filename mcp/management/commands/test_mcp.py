"""
Management command: python manage.py test_mcp

Tests every MCP tool by calling _dispatch_tool directly inside Django.
No HTTP, no credentials, no manual log-reading.

Usage:
    python manage.py test_mcp                   # run all tests
    python manage.py test_mcp --section crm     # only crm tools
    python manage.py test_mcp --section tasks
    python manage.py test_mcp --section meetings
    python manage.py test_mcp --tool list_leads # one tool only
    python manage.py test_mcp --dry-run         # skip write operations
"""

import os
import sys
import json
import traceback
from django.core.management.base import BaseCommand
from django.utils import timezone


GREEN  = '\033[92m'
RED    = '\033[91m'
YELLOW = '\033[93m'
CYAN   = '\033[96m'
BOLD   = '\033[1m'
RESET  = '\033[0m'


class Command(BaseCommand):
    help = 'Test all DigiCRM MCP tools and report pass/fail'

    def add_arguments(self, parser):
        parser.add_argument('--section', choices=['crm', 'tasks', 'meetings', 'whatsapp'], help='Only run this section')
        parser.add_argument('--tool', help='Run a single tool by name')
        parser.add_argument('--dry-run', action='store_true', help='Skip write/create/delete tools')
        parser.add_argument('--tenant-id', help='Override DIGICRM_TENANT_ID env var for this run')

    def handle(self, *args, **options):
        from mcp.django_view import _dispatch_tool, TENANT_ID, OWNER_USER_ID

        tenant_id = options.get('tenant_id') or TENANT_ID
        dry_run   = options['dry_run']
        only_tool = options.get('tool')
        section   = options.get('section')

        if not tenant_id:
            self.stderr.write(f'{RED}ERROR: DIGICRM_TENANT_ID not set. Add it to .env or pass --tenant-id <uuid>{RESET}')
            sys.exit(1)

        self.stdout.write(f'\n{BOLD}{CYAN}DigiCRM MCP Tool Test Suite{RESET}')
        self.stdout.write(f'{CYAN}Tenant : {tenant_id}{RESET}')
        self.stdout.write(f'{CYAN}DryRun : {dry_run}{RESET}\n')

        # ── helpers ──────────────────────────────────────────────────────────

        results = {'passed': 0, 'failed': 0, 'skipped': 0}
        created = {}  # track IDs created during test so we can use them later

        def run(tool_name, args_dict, *, skip_if_dry=False, section_name=''):
            """Run one tool, print result."""
            if only_tool and tool_name != only_tool:
                return
            if section and section_name and section_name != section:
                return
            if skip_if_dry and dry_run:
                self.stdout.write(f'  {YELLOW}SKIP{RESET}  {tool_name}  (dry-run)')
                results['skipped'] += 1
                return

            try:
                result = _dispatch_tool(tool_name, args_dict)
                self.stdout.write(f'  {GREEN}PASS{RESET}  {tool_name}')
                self.stdout.write(f'        → {json.dumps(result, default=str)[:120]}')
                results['passed'] += 1
                return result
            except Exception as exc:
                self.stdout.write(f'  {RED}FAIL{RESET}  {tool_name}')
                self.stdout.write(f'        ✗ {exc}')
                tb = traceback.format_exc().strip().split('\n')
                for line in tb[-4:]:
                    self.stdout.write(f'        {line}')
                results['failed'] += 1
                return None

        # ── pick a real lead to use as sample ────────────────────────────────
        from crm.models import Lead
        sample_lead = Lead.objects.filter(tenant_id=tenant_id).first()
        sample_lead_id = sample_lead.id if sample_lead else 1

        from tasks.models import Task
        sample_task = Task.objects.filter(tenant_id=tenant_id).first()
        sample_task_id = sample_task.id if sample_task else 1

        from meetings.models import Meeting
        sample_meeting = Meeting.objects.filter(tenant_id=tenant_id).first()
        sample_meeting_id = sample_meeting.id if sample_meeting else 1

        # ─────────────────────────────────────────────────────────────────────
        self.stdout.write(f'\n{BOLD}── CRM Tools ──────────────────────────────────{RESET}')

        run('list_lead_statuses', {}, section_name='crm')

        r = run('list_leads', {'page': 1, 'page_size': 5}, section_name='crm')

        run('list_leads', {'search': 'test', 'page': 1, 'page_size': 5}, section_name='crm')

        run('get_lead', {'lead_id': sample_lead_id}, section_name='crm')

        # create_lead — write op
        new_lead = run('create_lead', {
            'name': '_MCP_TEST_LEAD',
            'phone': '0000000000',
            'email': 'mcptest@delete.me',
            'source': 'mcp_test',
            'notes': 'Created by test_mcp management command — safe to delete',
        }, skip_if_dry=True, section_name='crm')
        if new_lead:
            created['lead_id'] = new_lead.get('id')

        # update_lead — write op (uses created lead or sample)
        update_id = created.get('lead_id') or sample_lead_id
        run('update_lead', {
            'lead_id': update_id,
            'notes': f'Updated by test_mcp at {timezone.now()}',
        }, skip_if_dry=True, section_name='crm')

        # update_lead_status — write op
        from crm.models import LeadStatus
        first_status = LeadStatus.objects.filter(tenant_id=tenant_id).first()
        if first_status:
            run('update_lead_status', {
                'lead_id': update_id,
                'status_id': first_status.id,
            }, skip_if_dry=True, section_name='crm')
        else:
            self.stdout.write(f'  {YELLOW}SKIP{RESET}  update_lead_status  (no statuses found for tenant)')

        # add_lead_to_group
        from crm.models import LeadGroup
        first_group = LeadGroup.objects.filter(tenant_id=tenant_id).first()
        if first_group and created.get('lead_id'):
            run('add_lead_to_group', {
                'lead_id': created['lead_id'],
                'lead_group_id': first_group.id,
            }, skip_if_dry=True, section_name='crm')
        else:
            self.stdout.write(f'  {YELLOW}SKIP{RESET}  add_lead_to_group  (no groups or no created lead)')

        # create_lead_activity
        run('create_lead_activity', {
            'lead_id': sample_lead_id,
            'type': 'NOTE',
            'content': 'MCP test activity — safe to delete',
        }, skip_if_dry=True, section_name='crm')

        # ─────────────────────────────────────────────────────────────────────
        self.stdout.write(f'\n{BOLD}── Task Tools ──────────────────────────────────{RESET}')

        run('create_task', {
            'title': '_MCP_TEST_TASK',
            'description': 'Created by test_mcp — safe to delete',
            'lead_id': sample_lead_id,
            'priority': 'LOW',
        }, skip_if_dry=True, section_name='tasks')

        if sample_task_id:
            run('update_task', {
                'task_id': sample_task_id,
                'status': 'IN_PROGRESS',
            }, skip_if_dry=True, section_name='tasks')

        # ─────────────────────────────────────────────────────────────────────
        self.stdout.write(f'\n{BOLD}── Meeting Tools ───────────────────────────────{RESET}')

        run('create_meeting', {
            'lead_id': sample_lead_id,
            'title': '_MCP_TEST_MEETING',
            'start_time': (timezone.now() + timezone.timedelta(hours=1)).isoformat(),
            'end_time':   (timezone.now() + timezone.timedelta(hours=2)).isoformat(),
            'notes': 'Created by test_mcp — safe to delete',
        }, skip_if_dry=True, section_name='meetings')

        if sample_meeting_id:
            run('update_meeting', {
                'meeting_id': sample_meeting_id,
                'notes': f'Updated by test_mcp at {timezone.now()}',
            }, skip_if_dry=True, section_name='meetings')

        # ─────────────────────────────────────────────────────────────────────
        # Cleanup: delete the test lead we created
        if created.get('lead_id') and not dry_run:
            try:
                Lead.objects.filter(id=created['lead_id'], name='_MCP_TEST_LEAD').delete()
                self.stdout.write(f'\n  {CYAN}CLEANUP{RESET}  Deleted test lead id={created["lead_id"]}')
            except Exception as e:
                self.stdout.write(f'\n  {YELLOW}CLEANUP WARN{RESET}  Could not delete test lead: {e}')

        # ─────────────────────────────────────────────────────────────────────
        self.stdout.write(f'\n{BOLD}{"─"*50}{RESET}')
        total = results['passed'] + results['failed'] + results['skipped']
        self.stdout.write(
            f'{BOLD}Results: '
            f'{GREEN}{results["passed"]} passed{RESET}  '
            f'{RED}{results["failed"]} failed{RESET}  '
            f'{YELLOW}{results["skipped"]} skipped{RESET}  '
            f'/ {total} total\n'
        )

        if results['failed']:
            sys.exit(1)
