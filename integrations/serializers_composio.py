"""
Django REST Framework serializers for the Composio integration.

Kept in a separate module from serializers.py (682 lines) purely for
reviewability; the conventions are identical - explicit Meta.fields,
read_only_fields, and a help_text on every field because these schemas feed
drf-spectacular and the MCP tool descriptions.

SECURITY: none of these serializers may ever expose a credential. Composio
holds the tokens, so there is nothing to leak from ComposioConnection - but
``metadata`` is a free-form JSON column, so it is NOT serialized by the list
or detail serializers, and COMPOSIO_API_KEY appears nowhere in this module.
"""

from rest_framework import serializers

from integrations.models import (
    ComposioAuthConfig,
    ComposioConnection,
    ComposioConnectionEvent,
    ComposioConnectionScopeEnum,
    ComposioToolkit,
)


# ---------------------------------------------------------------------------
# Toolkits (catalogue)
# ---------------------------------------------------------------------------

class ComposioToolkitSerializer(serializers.ModelSerializer):
    """
    Serialize a Composio toolkit catalogue entry.

    Agents and the UI use this to discover which third-party apps a tenant can
    connect. ``is_connectable`` answers "can this tenant start a connect flow
    right now"; ``my_connection`` is the caller's own live connection, if any.
    """
    is_connectable = serializers.SerializerMethodField(
        help_text='True when an auth config resolves for the calling tenant, so a connect flow '
                  'can be started immediately. Read-only.'
    )
    connection_count = serializers.SerializerMethodField(
        help_text='Number of live connections the caller has for this toolkit. Read-only.'
    )
    my_connection = serializers.SerializerMethodField(
        help_text='Compact summary of the caller\'s most recent live connection for this toolkit, '
                  'or null. Read-only.'
    )

    class Meta:
        model = ComposioToolkit
        fields = [
            'slug', 'name', 'description', 'logo_url', 'categories',
            'auth_schemes', 'composio_managed_auth_schemes', 'no_auth',
            'tools_count', 'triggers_count', 'is_featured',
            'is_connectable', 'connection_count', 'my_connection',
        ]
        read_only_fields = fields
        extra_kwargs = {
            'slug': {'help_text': 'Composio toolkit slug, uppercase (GMAIL, NOTION, GOOGLEDRIVE, GOOGLECALENDAR). '
                                  'This is the identifier to pass to the initiate endpoint.'},
            'name': {'help_text': 'Human-readable toolkit name.'},
            'description': {'help_text': 'Plain-language description of what this toolkit does.'},
            'logo_url': {'help_text': 'URL of the toolkit logo, for display in the catalogue grid.'},
            'categories': {'help_text': 'Category slugs reported by Composio, for filtering.'},
            'auth_schemes': {'help_text': 'All auth schemes this toolkit supports.'},
            'composio_managed_auth_schemes': {'help_text': 'Schemes Composio manages the OAuth app for. '
                                                           'Non-empty means one-click connect.'},
            'no_auth': {'help_text': 'Whether this toolkit requires no authentication at all.'},
            'tools_count': {'help_text': 'Number of executable tools this toolkit exposes.'},
            'triggers_count': {'help_text': 'Number of event triggers this toolkit exposes.'},
            'is_featured': {'help_text': 'Whether this toolkit is pinned to the top of the catalogue.'},
        }

    def get_is_connectable(self, obj):
        return bool((self.context.get('connectable_slugs') or set()) and
                    obj.slug in self.context['connectable_slugs'])

    def get_connection_count(self, obj):
        return len((self.context.get('connections_by_toolkit') or {}).get(obj.slug, []))

    def get_my_connection(self, obj):
        connections = (self.context.get('connections_by_toolkit') or {}).get(obj.slug, [])
        if not connections:
            return None
        conn = connections[0]
        return {
            'public_id': str(conn.public_id),
            'status': conn.status,
            'alias': conn.alias,
            'account_label': conn.account_label,
            'connected_at': conn.connected_at,
        }


class ComposioToolkitDetailSerializer(ComposioToolkitSerializer):
    """Toolkit detail. Adds sync bookkeeping; still exposes no credentials."""

    class Meta(ComposioToolkitSerializer.Meta):
        fields = ComposioToolkitSerializer.Meta.fields + ['is_enabled', 'sort_order', 'last_synced_at']
        read_only_fields = fields
        extra_kwargs = dict(ComposioToolkitSerializer.Meta.extra_kwargs, **{
            'is_enabled': {'help_text': 'Whether operators have opted this toolkit in for tenants.'},
            'sort_order': {'help_text': 'Manual ordering weight within the catalogue.'},
            'last_synced_at': {'help_text': 'When this catalogue entry was last refreshed from Composio. Read-only.'},
        })


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

class ComposioConnectionListSerializer(serializers.ModelSerializer):
    """
    Serialize compact Composio connection records.

    No credentials exist to expose - Composio holds the tokens. The Composio
    entity id (``composio_user_id``) and the raw ``metadata`` blob are
    deliberately omitted: they are internal addressing details.
    """
    toolkit_name = serializers.CharField(
        source='auth_config.name', read_only=True,
        help_text='Display name of the toolkit this connection belongs to. Read-only.'
    )
    is_usable = serializers.BooleanField(
        read_only=True,
        help_text='True only when the connection is ACTIVE and addressable at Composio. Read-only.'
    )

    class Meta:
        model = ComposioConnection
        fields = [
            'public_id', 'toolkit_slug', 'toolkit_name', 'status', 'scope',
            'alias', 'account_label', 'is_usable',
            'connected_at', 'last_used_at', 'created_at',
        ]
        read_only_fields = fields
        extra_kwargs = {
            'public_id': {'help_text': 'Stable identifier for this connection. Use it in all connection URLs. Read-only.'},
            'toolkit_slug': {'help_text': 'Composio toolkit slug (GMAIL, NOTION, GOOGLEDRIVE, GOOGLECALENDAR).'},
            'status': {'help_text': 'PENDING, INITIALIZING, ACTIVE, INACTIVE, FAILED, EXPIRED, REVOKED or DELETED.'},
            'scope': {'help_text': 'USER = private to the owner. TENANT = shared across the tenant.'},
            'alias': {'help_text': 'User-supplied label for this connection, e.g. "Sales inbox".'},
            'account_label': {'help_text': 'Third-party account identifier the provider reports, e.g. the connected '
                                           'Gmail address. Display only - never a credential.'},
            'connected_at': {'help_text': 'When this connection first became ACTIVE, in ISO 8601. Read-only.'},
            'last_used_at': {'help_text': 'When a tool was last executed with this connection. Read-only.'},
            'created_at': {'help_text': 'When this connection row was created, in ISO 8601. Read-only.'},
        }


class ComposioConnectionDetailSerializer(ComposioConnectionListSerializer):
    """
    Full Composio connection detail.

    Adds granted scopes, health fields and the last error so a user can see
    exactly what they authorised and why a connection is unhealthy. Still no
    tokens: there are none to return.
    """

    class Meta(ComposioConnectionListSerializer.Meta):
        fields = ComposioConnectionListSerializer.Meta.fields + [
            'granted_scopes', 'expires_at', 'last_status_check_at',
            'disconnected_at', 'last_error', 'last_error_at', 'updated_at',
        ]
        read_only_fields = fields
        extra_kwargs = dict(ComposioConnectionListSerializer.Meta.extra_kwargs, **{
            'granted_scopes': {'help_text': 'OAuth scopes Composio reports as granted for this account. Read-only.'},
            'expires_at': {'help_text': 'Credential expiry reported by Composio, when available. We do not hold the '
                                        'credential itself. Read-only.'},
            'last_status_check_at': {'help_text': 'When we last polled Composio for this connection status. Read-only.'},
            'disconnected_at': {'help_text': 'When this connection was disconnected or deleted. Read-only.'},
            'last_error': {'help_text': 'Last error reported for this connection, if any. Read-only.'},
            'last_error_at': {'help_text': 'When the last error occurred. Read-only.'},
            'updated_at': {'help_text': 'When this connection row was last updated, in ISO 8601. Read-only.'},
        })


class ComposioAdminConnectionSerializer(ComposioConnectionDetailSerializer):
    """
    Tenant-admin oversight view of a connection.

    Adds the owning user ids so an admin can see who owns what. Still scoped to
    the admin's own tenant by the viewset queryset - "admin" here means tenant
    admin, never platform admin.
    """
    events_count = serializers.IntegerField(
        read_only=True, help_text='Number of audit events recorded for this connection. Read-only.'
    )

    class Meta(ComposioConnectionDetailSerializer.Meta):
        fields = ComposioConnectionDetailSerializer.Meta.fields + [
            'user_id', 'created_by_user_id', 'events_count',
        ]
        read_only_fields = fields
        extra_kwargs = dict(ComposioConnectionDetailSerializer.Meta.extra_kwargs, **{
            'user_id': {'help_text': 'CRM user who owns this connection. Read-only.'},
            'created_by_user_id': {'help_text': 'User who initiated the connection. Differs from user_id for '
                                                'TENANT-scoped connections. Read-only.'},
        })


class ComposioConnectionEventSerializer(serializers.ModelSerializer):
    """Serialize one entry from a connection's append-only audit trail."""

    class Meta:
        model = ComposioConnectionEvent
        fields = ['id', 'event_type', 'actor_user_id', 'message', 'payload', 'source_ip', 'created_at']
        read_only_fields = fields
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this audit event. Read-only.'},
            'event_type': {'help_text': 'INITIATED, CALLBACK, ACTIVATED, FAILED, REFRESHED, DISABLED, ENABLED, '
                                        'DISCONNECTED, STATUS_SYNC, TOOL_EXECUTED, WEBHOOK or ERROR.'},
            'actor_user_id': {'help_text': 'User who caused the event. Null for system or webhook driven events.'},
            'message': {'help_text': 'Human-readable summary of what happened.'},
            'payload': {'help_text': 'Scrubbed context such as status transition or tool slug. Never contains secrets.'},
            'source_ip': {'help_text': 'Client IP address for user-initiated events.'},
            'created_at': {'help_text': 'When the event was recorded, in ISO 8601. Read-only.'},
        }


# ---------------------------------------------------------------------------
# Auth configs (tenant admin)
# ---------------------------------------------------------------------------

class ComposioAuthConfigSerializer(serializers.ModelSerializer):
    """
    Serialize a Composio auth config.

    ``auth_config_id`` is a public Composio identifier, not a secret. There is
    no client_secret field on this model at all - with Composio-managed auth we
    do not have one, and with BYO-OAuth the secret is posted straight through to
    Composio and never persisted here.
    """
    is_global = serializers.SerializerMethodField(
        help_text='True for the platform-wide default config that every tenant falls back to. Read-only.'
    )

    class Meta:
        model = ComposioAuthConfig
        fields = [
            'public_id', 'toolkit_slug', 'name', 'auth_config_id', 'auth_scheme',
            'is_composio_managed', 'is_active', 'is_global', 'restrict_to_tools',
            'default_tool_versions', 'last_synced_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'public_id', 'auth_config_id', 'auth_scheme', 'is_composio_managed',
            'is_global', 'last_synced_at', 'created_at', 'updated_at',
        ]
        extra_kwargs = {
            'public_id': {'help_text': 'Stable identifier for this auth config. Use it in auth config URLs. Read-only.'},
            'toolkit_slug': {'help_text': 'Composio toolkit slug this config authenticates, uppercase.'},
            'name': {'help_text': 'Display name shown to tenant admins.'},
            'auth_config_id': {'help_text': 'Composio auth config id ("ac_..."). A public identifier, not a secret. Read-only.'},
            'auth_scheme': {'help_text': 'Auth scheme Composio reports: OAUTH2, OAUTH1, API_KEY, BEARER_TOKEN, BASIC or NO_AUTH.'},
            'is_composio_managed': {'help_text': 'True when Composio owns the OAuth app, so no credentials of ours exist.'},
            'is_active': {'help_text': 'Whether users may create new connections against this config.'},
            'restrict_to_tools': {'help_text': 'Allowlist of tool slugs the execute endpoint may call. Empty means none.'},
            'default_tool_versions': {'help_text': 'Map of tool slug to pinned Composio tool version.'},
            'last_synced_at': {'help_text': 'When this row was last reconciled against Composio. Read-only.'},
            'created_at': {'help_text': 'When this config was created, in ISO 8601. Read-only.'},
            'updated_at': {'help_text': 'When this config was last updated, in ISO 8601. Read-only.'},
        }

    def get_is_global(self, obj):
        return obj.tenant_id is None


class ComposioAuthConfigCreateSerializer(serializers.Serializer):
    """Request body for creating a tenant-owned Composio auth config."""
    toolkit_slug = serializers.CharField(
        max_length=100,
        help_text='Composio toolkit slug to create an auth config for, e.g. NOTION. Case-insensitive.'
    )
    name = serializers.CharField(
        max_length=200, required=False, allow_blank=True,
        help_text='Optional display name. Defaults to the toolkit slug.'
    )
    use_composio_managed = serializers.BooleanField(
        default=True,
        help_text='Must be true. Bringing your own OAuth app is not supported through this endpoint yet, '
                  'because the client secret would have to transit our API.'
    )

    def validate_toolkit_slug(self, value):
        return value.strip().upper()


# ---------------------------------------------------------------------------
# Flow request bodies
# ---------------------------------------------------------------------------

class ComposioInitiateSerializer(serializers.Serializer):
    """
    Request body for starting a Composio hosted-auth flow.

    The caller never supplies a tenant, user or Composio entity id - those come
    from the JWT alone and are derived server-side.
    """
    toolkit_slug = serializers.CharField(
        max_length=100,
        help_text='Composio toolkit slug to connect, e.g. GMAIL, NOTION, GOOGLEDRIVE or GOOGLECALENDAR. '
                  'Case-insensitive.'
    )
    alias = serializers.CharField(
        max_length=200, required=False, allow_blank=True, allow_null=True,
        help_text='Optional user-facing label for this connection, e.g. "Sales inbox". Lets one user hold '
                  'several accounts for the same toolkit.'
    )
    scope = serializers.ChoiceField(
        choices=ComposioConnectionScopeEnum.choices,
        default=ComposioConnectionScopeEnum.USER,
        help_text='USER creates a connection private to the caller. TENANT creates one shared with the whole '
                  'tenant and requires tenant admin rights.'
    )
    return_to = serializers.CharField(
        max_length=500, required=False, allow_blank=True, allow_null=True,
        help_text='Relative frontend path to return the browser to after authorising, e.g. "/integrations". '
                  'Must be relative and on the server-side allowlist; anything else falls back to /integrations.'
    )

    def validate_toolkit_slug(self, value):
        return value.strip().upper()

    def validate_alias(self, value):
        return (value or '').strip() or None


class ComposioRefreshSerializer(serializers.Serializer):
    """Request body for re-authorising an existing connection."""
    return_to = serializers.CharField(
        max_length=500, required=False, allow_blank=True, allow_null=True,
        help_text='Relative frontend path to return the browser to after re-authorising.'
    )


class ComposioExecuteSerializer(serializers.Serializer):
    """
    Request body for executing a Composio tool through a connection.

    The tool slug is validated server-side against the auth config's
    ``restrict_to_tools`` allowlist - an arbitrary slug from the browser is
    never accepted.
    """
    tool_slug = serializers.CharField(
        max_length=200,
        help_text='Composio tool slug to execute, e.g. GMAIL_GET_PROFILE. Must appear in the auth config '
                  'restrict_to_tools allowlist.'
    )
    arguments = serializers.JSONField(
        required=False, default=dict,
        help_text='Arguments object for the tool, matching the tool schema Composio publishes.'
    )

    def validate_tool_slug(self, value):
        return value.strip().upper()


class ComposioRevokeSerializer(serializers.Serializer):
    """Request body for a tenant admin revoking someone else's connection."""
    reason = serializers.CharField(
        max_length=500, required=False, allow_blank=True,
        help_text='Optional reason, recorded on the audit trail.'
    )
