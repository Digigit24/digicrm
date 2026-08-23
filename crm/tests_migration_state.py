"""
The crm app's models must match its migrations.

Why this file exists
--------------------
Migration ``0005_lead_search_indexes`` was hand-written: it issues seven
``AddIndex`` operations for composite ``(tenant_id, ...)`` indexes on ``leads``,
and the declarations were never mirrored into ``crm/models.py``.  Django's
autodetector therefore saw seven indexes in migration state that the model did
not declare and proposed to REMOVE all of them - on a live table.

Nothing warns you about that.  ``makemigrations`` prints the removals, someone
runs ``migrate``, and seven indexes backing lead search, status/owner filtering
and follow-up queries are gone.  It resurfaced repeatedly because deleting the
generated migration file treats the symptom; the drift stays.

So this asserts the invariant directly.  If a future change edits
``crm/models.py`` without a migration - or writes a migration the model does not
reflect - this fails immediately, in CI, instead of turning into a DDL statement
against production.
"""
from django.apps import apps
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
from django.db.migrations.state import ProjectState
from django.test import TestCase

from crm.models import Lead

# Written into migration 0005 and present in production. Names are part of the
# contract: a renamed index is a DROP plus a CREATE, which on a large table is
# exactly the outage this guard exists to prevent.
EXPECTED_LEAD_INDEXES = {
    'lead_tenant_created_idx': ['tenant_id', '-created_at'],
    'lead_tenant_name_idx': ['tenant_id', 'name'],
    'lead_tenant_phone_idx': ['tenant_id', 'phone'],
    'lead_tenant_email_idx': ['tenant_id', 'email'],
    'lead_tenant_status_idx': ['tenant_id', 'status_id'],
    'lead_tenant_owner_idx': ['tenant_id', 'owner_user_id'],
    'lead_tenant_followup_idx': ['tenant_id', 'next_follow_up_at'],
}


class LeadSearchIndexesAreDeclaredTests(TestCase):
    def test_every_index_migration_0005_created_is_declared_on_the_model(self):
        declared = {index.name: list(index.fields) for index in Lead._meta.indexes}

        for name, fields in EXPECTED_LEAD_INDEXES.items():
            self.assertIn(
                name, declared,
                f'{name} exists in the database (migration 0005 created it) but is '
                f'not declared on Lead.Meta.indexes. makemigrations will propose '
                f'to DROP it.',
            )
            self.assertEqual(
                declared[name], fields,
                f'{name} is declared with different columns than migration 0005 '
                f'created. Changing an index in place is a drop and a recreate.',
            )

    def test_the_indexes_lead_with_tenant_id(self):
        """Every lead query is tenant scoped, so tenant_id must come first."""
        for name, fields in EXPECTED_LEAD_INDEXES.items():
            self.assertEqual(fields[0], 'tenant_id', name)


class CrmMigrationsAreUpToDateTests(TestCase):
    def test_makemigrations_would_detect_no_changes_for_crm(self):
        """
        The check `manage.py makemigrations crm --check` performs, as a test.

        Scoped to `crm` on purpose: other apps drift for their own reasons and
        failing this suite for someone else's app would just get it muted.
        """
        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(apps),
            NonInteractiveMigrationQuestioner(specified_apps=set(), dry_run=True),
        )
        changes = autodetector.changes(
            graph=loader.graph, trim_to_apps={'crm'}, convert_apps={'crm'},
        )

        operations = [
            f'{operation.__class__.__name__}: {operation.describe()}'
            for migration in changes.get('crm', [])
            for operation in migration.operations
        ]

        self.assertEqual(
            operations, [],
            'crm/models.py and crm/migrations have drifted. Running '
            'makemigrations now would generate the operations above, and some '
            'of them may be destructive on a live table:\n  '
            + '\n  '.join(operations),
        )
