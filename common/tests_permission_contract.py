"""
Every permission key a view ENFORCES must be a key a role can HOLD.

Why this file exists
--------------------
`HasDigiPermission` builds its key from three class attributes on the view —
`permission_module`, `permission_resource`, and an action derived from
`permission_action` / `action_permission_map` / the HTTP method — and then asks
`check_permission` whether the JWT grants it. A key the catalog has never heard
of is not an error anywhere: `get_permission_value` simply returns None and the
request is denied. Every role, forever, silently, with `is_admin_request` as the
only way through.

That is exactly what happened to the WhatsApp contacts surface. Five proxy views
declared `permission_resource='contacts'`, and `whatsapp.contacts.*` existed
nowhere in the catalog, so the contacts, contact-group and label endpoints were
admin-only from the day they shipped. `WhatsAppTemplateSendProxyView` had the
same problem with `permission_action='send'`. Nothing failed, nothing logged;
the only symptom was a 403 that looked like a misconfigured role.

So this scans the real view classes and asserts that every key they can produce
is in `PERMISSION_CATALOG`. It is deliberately a source-level scan rather than a
list someone has to remember to update — the failure mode being guarded against
is precisely "someone added a view and did not update the other place".

Kept out of any app's `tests.py` because it is a cross-app contract, not a test
of one app's behaviour.
"""
import inspect
import pkgutil
from importlib import import_module

from django.test import SimpleTestCase

from common.generated_permissions import PERMISSION_BY_KEY
from common.permissions import HasDigiPermission

# The action a view declares, or that DRF/HTTP mapping can derive for it. Kept
# in sync with HasDigiPermission.ACTION_PERMISSION_MAP / WRITE_METHOD_ACTION_MAP
# by reading them off the class rather than restating them here.
_DERIVABLE_ACTIONS = set(HasDigiPermission.ACTION_PERMISSION_MAP.values()) | set(
    HasDigiPermission.WRITE_METHOD_ACTION_MAP.values()
)

# Apps whose views participate in the CRM permission scheme.
_APPS = (
    'crm',
    'whatsapp_integration',
    'telephony',
    'integrations',
    'real_estate',
    'notifications',
)

_HTTP_METHODS = ('get', 'post', 'put', 'patch', 'delete')


def _view_modules():
    for app in _APPS:
        try:
            module = import_module(app)
        except ImportError:  # pragma: no cover - app not installed in this build
            continue
        for name in ('views',):
            try:
                yield import_module(f'{app}.{name}')
            except ImportError:
                continue
        # Some apps split views into a package.
        path = getattr(module, '__path__', None)
        if not path:
            continue
        for info in pkgutil.iter_modules(path):
            if info.name.startswith('views'):
                try:
                    yield import_module(f'{app}.{info.name}')
                except ImportError:
                    continue


def _permission_keys_a_view_can_require(view):
    """Every key this view class can ask `check_permission` about."""
    module = getattr(view, 'permission_module', 'crm')
    resource = getattr(view, 'permission_resource', None)
    if not resource:
        return set()

    declared = getattr(view, 'permission_action', None)
    if declared:
        actions = {declared}
    else:
        # No fixed action: the class can produce any action its own map or the
        # HTTP-method fallback yields, limited to the methods it implements.
        actions = set(getattr(view, 'action_permission_map', {}).values())
        implemented = {m for m in _HTTP_METHODS if callable(getattr(view, m, None))}
        if implemented:
            actions |= {
                HasDigiPermission.WRITE_METHOD_ACTION_MAP[m.upper()]
                for m in implemented
                if m.upper() in HasDigiPermission.WRITE_METHOD_ACTION_MAP
            }
        else:
            # A ViewSet: DRF actions, not HTTP methods.
            actions |= _DERIVABLE_ACTIONS

    return {f'{module}.{resource}.{action}' for action in actions if action}


def _all_required_keys():
    found = {}
    for module in _view_modules():
        for name, obj in vars(module).items():
            if not inspect.isclass(obj) or not getattr(obj, 'permission_resource', None):
                continue
            if obj.__module__ != module.__name__:
                continue  # imported into this module, not defined here
            for key in _permission_keys_a_view_can_require(obj):
                found.setdefault(key, set()).add(f'{module.__name__}.{name}')
    return found


# KNOWN BACKLOG — not an approval.
#
# Every key below is enforced by a live view and missing from the catalog, i.e.
# admin-only today for exactly the same reason `whatsapp.contacts.*` was. They
# are quarantined rather than fixed here because adding twenty-eight permission
# keys across four more modules is a deliberate, security-adjacent decision per
# module, not a drive-by on a WhatsApp ticket — and because each one needs
# someone to confirm the action set matches what the views really expose.
#
# The list is here, in code, so the gap is visible and so a NEW gap still fails
# this test. Shrink it; do not grow it.
KNOWN_MISSING_FROM_CATALOG = {
    'crm.settings.create',
    'crm.settings.delete',
    'integrations.providers.create',
    'integrations.providers.delete',
    'integrations.providers.edit',
    'real_estate.leads.create',
    'real_estate.leads.delete',
    'real_estate.leads.edit',
    'real_estate.leads.view',
    'real_estate.projects.create',
    'real_estate.projects.delete',
    'real_estate.projects.edit',
    'real_estate.projects.view',
    'real_estate.units.create',
    'real_estate.units.delete',
    'real_estate.units.edit',
    'real_estate.units.view',
    'telephony.analytics.view',
    'telephony.campaigns.create',
    'telephony.campaigns.delete',
    'telephony.campaigns.edit',
    'telephony.campaigns.view',
    'telephony.sms.delete',
    'telephony.sms.edit',
    'whatsapp.flows.create',
    'whatsapp.flows.delete',
    'whatsapp.flows.edit',
    'whatsapp.flows.view',
}


class EnforcedPermissionsExistInCatalogTests(SimpleTestCase):
    def test_every_key_a_view_enforces_is_grantable(self):
        required = _all_required_keys()

        # A guard that scans nothing is a guard that passes forever.
        self.assertGreater(len(required), 40, 'view scan found suspiciously few permission keys')

        missing = {
            key: sorted(views)
            for key, views in sorted(required.items())
            if key not in PERMISSION_BY_KEY and key not in KNOWN_MISSING_FROM_CATALOG
        }

        self.assertEqual(
            missing,
            {},
            'These views enforce permission keys that are not in the catalog, so no '
            'role can be granted them and every non-admin request is denied:\n  '
            + '\n  '.join(f'{key}  <- {", ".join(views)}' for key, views in missing.items()),
        )

    def test_the_backlog_list_does_not_outlive_the_backlog(self):
        """Deleting a quarantined key from the catalog's gap must delete it here too."""
        fixed = sorted(key for key in KNOWN_MISSING_FROM_CATALOG if key in PERMISSION_BY_KEY)
        self.assertEqual(
            fixed,
            [],
            'These keys are in the catalog now — remove them from '
            f'KNOWN_MISSING_FROM_CATALOG: {fixed}',
        )

        required = set(_all_required_keys())
        stale = sorted(KNOWN_MISSING_FROM_CATALOG - required)
        self.assertEqual(
            stale,
            [],
            'These keys are no longer enforced by any view — remove them from '
            f'KNOWN_MISSING_FROM_CATALOG: {stale}',
        )

    def test_the_whatsapp_contacts_and_template_send_keys_are_present(self):
        """The specific gap this file was written for. Pinned by name."""
        for key in (
            'whatsapp.contacts.view',
            'whatsapp.contacts.create',
            'whatsapp.contacts.edit',
            'whatsapp.contacts.delete',
            'whatsapp.templates.send',
        ):
            self.assertIn(key, PERMISSION_BY_KEY, key)
            self.assertEqual(PERMISSION_BY_KEY[key]['status'], 'active', key)
