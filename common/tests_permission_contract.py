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

WHERE TO FIX A FAILURE, now that `generated_permissions.py` is genuinely
generated: edit `superadmin/apps/common/permissions_catalog.yaml` and run
`python superadmin/scripts/generate_permissions.py`. Editing the generated file
directly will be overwritten. Being generated does NOT make this test redundant
— it moves its target. A view can still enforce a key nobody put in the YAML,
and that is the same silent 403 as before.

Grantability is still a second step: the Roles UI renders
`superadmin/apps/common/constants.py::PERMISSION_SCHEMA`, which is hand-written
and not produced from the catalog. A key can therefore pass this test and still
be ungrantable. That file is the remaining un-collapsed source of truth.

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


# DRF's standard viewset handlers, and the permission action each implies. A
# class only has the ones its mixins provide: ModelViewSet has all six,
# ReadOnlyModelViewSet has two. Asking the CLASS is how we avoid inventing
# actions a viewset does not route — an early version of this scan assumed
# every viewset was a ModelViewSet and reported telephony.sms.edit/delete as
# missing, when SMSLogViewSet is read-only and can never ask for them.
_VIEWSET_HANDLERS = {
    'list': 'view',
    'retrieve': 'view',
    'create': 'create',
    'update': 'edit',
    'partial_update': 'edit',
    'destroy': 'delete',
}


def _viewset_actions(view):
    """Actions a DRF viewset can actually route, honouring http_method_names."""
    allowed_methods = getattr(view, 'http_method_names', None)
    allowed = {m.lower() for m in allowed_methods} if allowed_methods else None

    # `update` is PUT and `partial_update` is PATCH; restricting
    # http_method_names to one of them removes the other.
    handler_method = {
        'list': 'get', 'retrieve': 'get', 'create': 'post',
        'update': 'put', 'partial_update': 'patch', 'destroy': 'delete',
    }

    actions = set()
    for handler, permission_action in _VIEWSET_HANDLERS.items():
        if not callable(getattr(view, handler, None)):
            continue
        if allowed is not None and handler_method[handler] not in allowed:
            continue
        actions.add(permission_action)

    # @action(detail=..., methods=[...]) routes extra verbs. Its NAME is not in
    # ACTION_PERMISSION_MAP, so HasDigiPermission falls through to the HTTP
    # method — UNLESS the view's own action_permission_map names it, which wins
    # last and is the whole point of that attribute. Missing this override
    # reported integrations.providers.create as required, when
    # ComposioToolkitViewSet maps its POST `sync` action back to 'view'.
    custom_map = getattr(view, 'action_permission_map', {}) or {}
    for attr in vars(view).values():
        mapping = getattr(attr, 'mapping', None) or {}
        for http_method, handler_name in mapping.items():
            if handler_name in custom_map:
                actions.add(custom_map[handler_name])
                continue
            mapped = HasDigiPermission.WRITE_METHOD_ACTION_MAP.get(http_method.upper())
            if mapped:
                actions.add(mapped)

    return actions


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
        # No fixed action: whatever the class's own map, its routed HTTP
        # methods, or its viewset handlers can produce.
        actions = set(getattr(view, 'action_permission_map', {}).values())
        implemented = {m for m in _HTTP_METHODS if callable(getattr(view, m, None))}
        if implemented:
            # A plain APIView: one handler per HTTP verb.
            actions |= {
                HasDigiPermission.WRITE_METHOD_ACTION_MAP[m.upper()]
                for m in implemented
                if m.upper() in HasDigiPermission.WRITE_METHOD_ACTION_MAP
            }
        else:
            actions |= _viewset_actions(view)

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


# KNOWN BACKLOG — empty, and that is the point.
#
# This set existed because 23 keys were enforced by live views and missing from
# the catalog, i.e. admin-only for the same reason `whatsapp.contacts.*` was.
# They have since been added, so the quarantine is empty and every key a view
# can enforce is a key a role can hold.
#
# If you are about to add an entry here, that is a decision to ship a permission
# nobody can be granted. Prefer adding the key. If you genuinely must defer it,
# say which module and why — `test_the_backlog_list_does_not_outlive_the_backlog`
# will make sure the entry disappears once the key lands.
KNOWN_MISSING_FROM_CATALOG: set[str] = set()


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
