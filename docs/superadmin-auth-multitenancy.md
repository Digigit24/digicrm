# SuperAdmin Auth & Multitenancy Architecture

> **Scope:** Documents the JWT-based authentication, tenant isolation, and permission system used in DigiCRM (Django), and provides a complete guide to replicating it in a Node.js/Express backend.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [How Authentication Works (Django Reference)](#2-how-authentication-works-django-reference)
3. [Permission System](#3-permission-system)
4. [Tenant Isolation](#4-tenant-isolation)
5. [Environment Variables](#5-environment-variables)
6. [Node.js Implementation Guide](#6-nodejs-implementation-guide)
   - [Project Structure](#61-project-structure)
   - [JWT Middleware](#62-jwt-middleware)
   - [TenantUser Class](#63-tenantuser-class)
   - [Permission Middleware](#64-permission-middleware)
   - [Tenant Query Helpers](#65-tenant-query-helpers)
   - [Route Protection Examples](#66-route-protection-examples)
   - [Model Schema Conventions](#67-model-schema-conventions)
   - [Express App Setup](#68-express-app-setup)
7. [JWT Payload Reference](#7-jwt-payload-reference)
8. [Permission Key Reference](#8-permission-key-reference)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        SUPERADMIN SERVICE                       │
│                    (https://admin.celiyo.com)                   │
│                                                                 │
│  - Manages users, tenants, modules, permissions                 │
│  - Issues signed JWT tokens (HS256 with shared JWT_SECRET_KEY)  │
│  - Login endpoint: POST /api/auth/login/                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │ JWT_SECRET_KEY (shared secret)
                            │
        ┌───────────────────┴───────────────────┐
        │                                       │
        ▼                                       ▼
┌───────────────┐                      ┌─────────────────┐
│  DigiCRM      │                      │  Your Node.js   │
│  (Django)     │                      │  Backend        │
│               │                      │                 │
│  Validates    │                      │  Validates      │
│  same JWT     │                      │  same JWT       │
│  No local     │                      │  No local       │
│  user table   │                      │  user table     │
└───────────────┘                      └─────────────────┘
```

**Key principle:** Neither the CRM app nor any tenant service stores users. The SuperAdmin service is the single source of truth. All tenant apps share the `JWT_SECRET_KEY` and validate tokens locally — no round-trip to SuperAdmin on every request.

---

## 2. How Authentication Works (Django Reference)

### Request Lifecycle

```
Client Request
  │
  ├── Authorization: Bearer <JWT_TOKEN>
  │
  ▼
JWTAuthenticationMiddleware  (common/middleware.py)
  │  1. Skip if PUBLIC_PATH
  │  2. Extract Bearer token
  │  3. jwt.decode(token, JWT_SECRET_KEY, algorithms=['HS256'])
  │  4. Validate required fields
  │  5. Check 'crm' in payload.enabled_modules
  │  6. Set on request:
  │       request.user_id
  │       request.email
  │       request.tenant_id
  │       request.tenant_slug
  │       request.is_super_admin
  │       request.permissions      ← nested dict
  │       request.enabled_modules  ← list
  │  7. Store tenant_id in thread-local storage
  │
  ▼
JWTRequestAuthentication  (common/authentication.py)
  │  DRF authentication class
  │  Reads attributes already set by middleware
  │  Wraps in TenantUser object
  │  Returns (user, None) to DRF
  │
  ▼
HasCRMPermission  (common/permissions.py)
  │  Checks request.permissions for permission key
  │  e.g. 'crm.leads.view'
  │  Handles scopes: 'all' | 'team' | 'own'
  │
  ▼
TenantViewSetMixin  (common/mixins.py)
  │  queryset.filter(tenant_id=request.tenant_id)
  │  serializer.save(tenant_id=request.tenant_id)
  │
  ▼
  Business Logic / Database
```

### Public Paths (No Auth Required)

| Path | Reason |
|------|--------|
| `/` | Root redirect |
| `/api/docs/`, `/api/schema/` | Swagger UI |
| `/admin/*` | Custom admin handles its own auth |
| `/auth/*` | Login endpoints |
| `/static/*` | Static files |
| `/health/` | Load balancer health check |
| OAuth callback (GET) | OAuth flow |

### Login Flow (for Admin UI / Session)

```
POST /auth/superadmin-login/
  { email, password }
      │
      ▼
  POST https://admin.celiyo.com/api/auth/login/
      │
      ▼
  Receive { tokens: { access }, user: { id, email, tenant, ... } }
      │
      ▼
  jwt.decode(access, JWT_SECRET_KEY)
      │
      ├── Check 'crm' in enabled_modules
      ▼
  Create session
  Store jwt_token, tenant_id, tenant_slug in session
      │
      ▼
  Return success / redirect to admin
```

---

## 3. Permission System

### Permission Dict Structure (inside JWT payload)

```json
{
  "permissions": {
    "crm": {
      "leads": {
        "view": "all",
        "create": true,
        "edit": "own",
        "delete": false
      },
      "statuses": {
        "view": "all",
        "create": true,
        "edit": true,
        "delete": false
      }
    }
  }
}
```

### Permission Values

| Value | Meaning |
|-------|---------|
| `false` | No access |
| `true` | Full access (boolean, no scope) |
| `"all"` | Access to all resources in the tenant |
| `"team"` | Access to team resources |
| `"own"` | Access only to resources where `owner_user_id == user_id` |

### Permission Key Format

```
{module}.{resource}.{action}

Examples:
  crm.leads.view
  crm.leads.create
  crm.leads.edit
  crm.leads.delete
  crm.statuses.view
  crm.activities.view
  crm.orders.delete
```

### Super Admin Bypass

If `is_super_admin = true` in the JWT, all permission checks pass automatically.

### How `get_queryset_for_permission` Works

```python
# Always filter by tenant first
queryset = queryset.filter(tenant_id=request.tenant_id)

# Then apply scope
if scope == 'all':
    return queryset                    # All tenant records
elif scope == 'own':
    return queryset.filter(owner_user_id=request.user_id)  # Only owned records
elif scope == 'team':
    return queryset                    # Team filtering (future)
```

---

## 4. Tenant Isolation

### Rule: Every model has `tenant_id`

```python
class Lead(models.Model):
    tenant_id = models.UUIDField(db_index=True)   # REQUIRED on every model
    owner_user_id = models.UUIDField(db_index=True)  # Who owns this record
    # ... business fields
```

### Isolation is enforced at 3 layers

| Layer | Where | What it does |
|-------|-------|--------------|
| **Middleware** | `JWTAuthenticationMiddleware` | Extracts tenant_id from JWT, sets on request |
| **ViewSet** | `TenantViewSetMixin.get_queryset()` | Filters all DB reads by tenant_id |
| **Serializer** | `TenantMixin.create()` | Injects tenant_id on all DB writes |

### TenantUser — No Database User Table

There is **no `auth_user` table** used. Instead, a `TenantUser` object is constructed from the JWT payload on every request:

```python
TenantUser(payload) → in-memory object with:
  .id / .pk          = payload['user_id']
  .email             = payload['email']
  .tenant_id         = payload['tenant_id']
  .tenant_slug       = payload['tenant_slug']
  .is_super_admin    = payload['is_super_admin']
  .permissions       = payload['permissions']
  .enabled_modules   = payload['enabled_modules']
```

---

## 5. Environment Variables

```env
# Django secret key (Django-only)
SECRET_KEY=

# JWT — shared with SuperAdmin, used to validate all tokens
JWT_SECRET_KEY=your-shared-secret-here
JWT_ALGORITHM=HS256

# SuperAdmin service URL
SUPERADMIN_URL=https://admin.celiyo.com

# Database
DATABASE_URL=postgres://user:pass@host:5432/dbname

# CORS
CORS_ALLOW_ALL_ORIGINS=false
CORS_ALLOWED_ORIGINS=http://localhost:3000,https://app.yourdomain.com

# Frontend
FRONTEND_URL=http://localhost:3000
```

---

## 6. Node.js Implementation Guide

### 6.1 Project Structure

```
src/
├── common/
│   ├── TenantUser.js           # In-memory user class (from JWT)
│   ├── jwtMiddleware.js        # Validates JWT, sets req.tenantUser
│   ├── permissionMiddleware.js # Permission check helpers & factory
│   ├── tenantHelpers.js        # Query helpers (filter by tenant)
│   └── constants.js            # Public paths, module name, etc.
├── routes/
│   ├── leads.js
│   ├── statuses.js
│   └── ...
├── models/                     # Sequelize / Prisma / Mongoose models
│   ├── Lead.js
│   └── ...
└── app.js
```

### 6.2 JWT Middleware

```js
// src/common/jwtMiddleware.js
const jwt = require('jsonwebtoken');

const PUBLIC_PATHS = [
  '/',
  '/health',
  '/auth',        // prefix match
  '/api/docs',    // prefix match
  '/api/schema',  // prefix match
  '/static',      // prefix match
];

const MODULE_NAME = 'crm'; // change per service

function isPublicPath(path, method) {
  // OAuth callbacks are public on GET
  if (method === 'GET' && path.includes('/oauth_callback')) return true;

  return PUBLIC_PATHS.some((p) =>
    path === p || path.startsWith(p + '/')
  );
}

function jwtMiddleware(req, res, next) {
  if (isPublicPath(req.path, req.method)) return next();

  const authHeader = req.headers['authorization'];
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Authorization header missing or invalid' });
  }

  const token = authHeader.slice(7);

  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET_KEY, {
      algorithms: [process.env.JWT_ALGORITHM || 'HS256'],
      clockTolerance: 30, // 30s leeway for clock skew
    });

    // Validate required fields
    const required = ['user_id', 'email', 'tenant_id', 'tenant_slug', 'permissions', 'enabled_modules'];
    for (const field of required) {
      if (payload[field] === undefined) {
        return res.status(401).json({ error: `JWT missing required field: ${field}` });
      }
    }

    // Check module access
    if (!payload.enabled_modules.includes(MODULE_NAME)) {
      return res.status(403).json({ error: `Module '${MODULE_NAME}' is not enabled for this tenant` });
    }

    // Set on request (mirrors Django's request attributes)
    req.userId        = payload.user_id;
    req.email         = payload.email;
    req.tenantId      = payload.tenant_id;
    req.tenantSlug    = payload.tenant_slug;
    req.isSuperAdmin  = payload.is_super_admin || false;
    req.permissions   = payload.permissions || {};
    req.enabledModules = payload.enabled_modules || [];
    req.jwtPayload    = payload;

    next();
  } catch (err) {
    if (err.name === 'TokenExpiredError') {
      return res.status(401).json({ error: 'Token expired' });
    }
    if (err.name === 'JsonWebTokenError') {
      return res.status(401).json({ error: 'Invalid token' });
    }
    return res.status(401).json({ error: 'Token validation failed' });
  }
}

module.exports = jwtMiddleware;
```

### 6.3 TenantUser Class

```js
// src/common/TenantUser.js

/**
 * In-memory representation of an authenticated user.
 * Constructed from JWT payload — no database lookup required.
 * Mirrors Django's TenantUser class.
 */
class TenantUser {
  constructor(payload) {
    this.id         = payload.user_id;
    this.email      = payload.email;
    this.firstName  = payload.first_name || '';
    this.lastName   = payload.last_name  || '';
    this.tenantId   = payload.tenant_id;
    this.tenantSlug = payload.tenant_slug;
    this.isSuperAdmin    = payload.is_super_admin || false;
    this.permissions     = payload.permissions || {};
    this.enabledModules  = payload.enabled_modules || [];
  }

  /**
   * Check if user has a specific permission.
   * @param {string} permissionKey - e.g. 'crm.leads.view'
   * @returns {boolean|string} - false, true, 'all', 'own', 'team'
   */
  getPermission(permissionKey) {
    if (this.isSuperAdmin) return 'all';

    const parts = permissionKey.split('.');
    let node = this.permissions;
    for (const part of parts) {
      if (node === undefined || node === null) return false;
      node = node[part];
    }
    return node !== undefined ? node : false;
  }

  hasPerm(permissionKey) {
    const val = this.getPermission(permissionKey);
    return val !== false && val !== null && val !== undefined;
  }

  hasModuleAccess(moduleName) {
    if (this.isSuperAdmin) return true;
    return this.enabledModules.includes(moduleName);
  }

  toString() {
    return `TenantUser(${this.email}, tenant=${this.tenantSlug})`;
  }
}

module.exports = TenantUser;
```

### 6.4 Permission Middleware

```js
// src/common/permissionMiddleware.js
const TenantUser = require('./TenantUser');

/**
 * Factory that returns Express middleware checking a permission key.
 *
 * Usage:
 *   router.get('/leads', requirePermission('crm.leads.view'), listLeads);
 *   router.post('/leads', requirePermission('crm.leads.create'), createLead);
 */
function requirePermission(permissionKey) {
  return (req, res, next) => {
    if (!req.tenantId) {
      return res.status(401).json({ error: 'Not authenticated' });
    }

    const user = new TenantUser(req.jwtPayload);
    const perm = user.getPermission(permissionKey);

    if (!perm) {
      return res.status(403).json({
        error: 'Permission denied',
        required: permissionKey,
      });
    }

    // Attach scope to request so views can use it
    req.permissionScope = perm; // 'all' | 'own' | 'team' | true
    req.currentUser = user;
    next();
  };
}

/**
 * Apply tenant + scope filter to a Sequelize where clause.
 *
 * @param {object} req         - Express request (must have tenantId, userId)
 * @param {string} permKey     - e.g. 'crm.leads.view'
 * @param {string} ownerField  - column name for owner check (default: 'owner_user_id')
 * @returns {object} Sequelize where clause fragment
 */
function getTenantScopeFilter(req, permKey, ownerField = 'owner_user_id') {
  const user   = new TenantUser(req.jwtPayload);
  const scope  = user.getPermission(permKey);
  const filter = { tenant_id: req.tenantId }; // Always filter by tenant first

  if (scope === 'own') {
    filter[ownerField] = req.userId;
  }
  // 'all' or 'team' → no extra filter beyond tenant_id

  return filter;
}

/**
 * Mongoose equivalent — returns a filter object for .find()
 */
function getTenantScopeFilterMongo(req, permKey, ownerField = 'ownerUserId') {
  const user  = new TenantUser(req.jwtPayload);
  const scope = user.getPermission(permKey);
  const filter = { tenantId: req.tenantId };

  if (scope === 'own') {
    filter[ownerField] = req.userId;
  }

  return filter;
}

module.exports = { requirePermission, getTenantScopeFilter, getTenantScopeFilterMongo };
```

### 6.5 Tenant Query Helpers

```js
// src/common/tenantHelpers.js

/**
 * Injects tenant_id into data before creating a record.
 * Mirrors Django's TenantMixin.create()
 */
function injectTenantId(req, data) {
  if (!req.tenantId) {
    throw new Error('tenant_id is required but not found on request');
  }
  return { ...data, tenant_id: req.tenantId };
}

/**
 * Validate that an object belongs to the current tenant.
 * Call this before updating or deleting.
 */
function assertSameTenant(req, record, tenantField = 'tenant_id') {
  const recordTenant = String(record[tenantField]);
  const reqTenant    = String(req.tenantId);
  if (recordTenant !== reqTenant) {
    const err = new Error('Tenant mismatch');
    err.statusCode = 403;
    throw err;
  }
}

module.exports = { injectTenantId, assertSameTenant };
```

### 6.6 Route Protection Examples

#### Sequelize Example

```js
// src/routes/leads.js
const express = require('express');
const router  = express.Router();
const { Lead } = require('../models');
const { requirePermission, getTenantScopeFilter } = require('../common/permissionMiddleware');
const { injectTenantId, assertSameTenant } = require('../common/tenantHelpers');

// List leads — scope-aware
router.get('/', requirePermission('crm.leads.view'), async (req, res) => {
  try {
    const where = getTenantScopeFilter(req, 'crm.leads.view');
    const leads = await Lead.findAll({ where });
    res.json(leads);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Get single lead
router.get('/:id', requirePermission('crm.leads.view'), async (req, res) => {
  try {
    const lead = await Lead.findOne({
      where: { id: req.params.id, tenant_id: req.tenantId },
    });
    if (!lead) return res.status(404).json({ error: 'Not found' });

    // Enforce 'own' scope at object level
    if (req.permissionScope === 'own' && String(lead.owner_user_id) !== String(req.userId)) {
      return res.status(403).json({ error: 'Permission denied (own scope)' });
    }

    res.json(lead);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Create lead — inject tenant automatically
router.post('/', requirePermission('crm.leads.create'), async (req, res) => {
  try {
    const data = injectTenantId(req, {
      ...req.body,
      owner_user_id: req.userId, // default owner = creator
    });
    const lead = await Lead.create(data);
    res.status(201).json(lead);
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// Update lead
router.patch('/:id', requirePermission('crm.leads.edit'), async (req, res) => {
  try {
    const lead = await Lead.findOne({
      where: { id: req.params.id, tenant_id: req.tenantId },
    });
    if (!lead) return res.status(404).json({ error: 'Not found' });
    assertSameTenant(req, lead);

    // Enforce 'own' scope
    if (req.permissionScope === 'own' && String(lead.owner_user_id) !== String(req.userId)) {
      return res.status(403).json({ error: 'Permission denied (own scope)' });
    }

    // Never allow tenant_id to change
    delete req.body.tenant_id;
    await lead.update(req.body);
    res.json(lead);
  } catch (err) {
    res.status(err.statusCode || 500).json({ error: err.message });
  }
});

// Delete lead
router.delete('/:id', requirePermission('crm.leads.delete'), async (req, res) => {
  try {
    const lead = await Lead.findOne({
      where: { id: req.params.id, tenant_id: req.tenantId },
    });
    if (!lead) return res.status(404).json({ error: 'Not found' });
    assertSameTenant(req, lead);

    await lead.destroy();
    res.status(204).send();
  } catch (err) {
    res.status(err.statusCode || 500).json({ error: err.message });
  }
});

module.exports = router;
```

#### Mongoose Example

```js
// src/routes/leads.mongo.js
const express = require('express');
const router  = express.Router();
const Lead    = require('../models/Lead');
const { requirePermission, getTenantScopeFilterMongo } = require('../common/permissionMiddleware');
const { injectTenantId } = require('../common/tenantHelpers');

router.get('/', requirePermission('crm.leads.view'), async (req, res) => {
  const filter = getTenantScopeFilterMongo(req, 'crm.leads.view');
  const leads  = await Lead.find(filter).lean();
  res.json(leads);
});

router.post('/', requirePermission('crm.leads.create'), async (req, res) => {
  const data = injectTenantId(req, { ...req.body, ownerUserId: req.userId });
  const lead = await Lead.create(data);
  res.status(201).json(lead);
});

module.exports = router;
```

### 6.7 Model Schema Conventions

#### Sequelize

```js
// src/models/Lead.js
const { DataTypes } = require('sequelize');
const sequelize = require('../db');

const Lead = sequelize.define('Lead', {
  id:           { type: DataTypes.BIGINT, primaryKey: true, autoIncrement: true },
  tenant_id:    { type: DataTypes.UUID, allowNull: false },  // ← REQUIRED on every model
  owner_user_id:{ type: DataTypes.UUID, allowNull: false },  // ← for 'own' scope checks
  name:         { type: DataTypes.TEXT, defaultValue: 'Unnamed' },
  phone:        { type: DataTypes.TEXT, allowNull: false },
  email:        { type: DataTypes.TEXT },
  status_id:    { type: DataTypes.BIGINT },
  priority:     { type: DataTypes.ENUM('low', 'medium', 'high'), defaultValue: 'medium' },
  lead_score:   { type: DataTypes.INTEGER },
  created_at:   { type: DataTypes.DATE, defaultValue: DataTypes.NOW },
  updated_at:   { type: DataTypes.DATE, defaultValue: DataTypes.NOW },
}, {
  tableName: 'leads',
  timestamps: false,
  indexes: [
    { fields: ['tenant_id'] },        // ← Always index tenant_id
    { fields: ['owner_user_id'] },
    { fields: ['tenant_id', 'status_id'] },
  ],
});

module.exports = Lead;
```

#### Mongoose

```js
// src/models/Lead.js
const mongoose = require('mongoose');

const leadSchema = new mongoose.Schema({
  tenantId:     { type: String, required: true, index: true },  // ← REQUIRED
  ownerUserId:  { type: String, required: true, index: true },  // ← for 'own' scope
  name:         { type: String, default: 'Unnamed' },
  phone:        { type: String, required: true },
  email:        String,
  priority:     { type: String, enum: ['low', 'medium', 'high'], default: 'medium' },
  leadScore:    { type: Number, min: 0, max: 100 },
}, {
  timestamps: true,
  collection: 'leads',
});

// Compound index for common queries
leadSchema.index({ tenantId: 1, ownerUserId: 1 });
leadSchema.index({ tenantId: 1, createdAt: -1 });

module.exports = mongoose.model('Lead', leadSchema);
```

### 6.8 Express App Setup

```js
// src/app.js
const express   = require('express');
const cors      = require('cors');
const jwtMiddleware = require('./common/jwtMiddleware');

// Routes
const leadRoutes   = require('./routes/leads');
const statusRoutes = require('./routes/statuses');

const app = express();

// CORS — allow custom tenant headers
app.use(cors({
  origin: process.env.CORS_ALLOWED_ORIGINS?.split(',') || ['http://localhost:3000'],
  credentials: true,
  allowedHeaders: [
    'content-type', 'authorization',
    'x-tenant-id', 'x-tenant-slug', 'tenanttoken',
  ],
  exposedHeaders: ['x-tenant-id', 'x-tenant-slug'],
}));

app.use(express.json());

// JWT auth on ALL routes (middleware skips public paths internally)
app.use(jwtMiddleware);

// Public routes
app.get('/health', (req, res) => res.json({ status: 'healthy' }));

// Protected API routes
app.use('/api/leads',    leadRoutes);
app.use('/api/statuses', statusRoutes);

// Error handler
app.use((err, req, res, next) => {
  console.error(err);
  res.status(err.statusCode || 500).json({ error: err.message });
});

module.exports = app;
```

```js
// src/server.js
require('dotenv').config();
const app = require('./app');
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
```

---

## 7. JWT Payload Reference

These are the fields the SuperAdmin service embeds in every JWT token:

```json
{
  "user_id": "uuid-string",
  "email": "user@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "tenant_id": "uuid-string",
  "tenant_slug": "acme-corp",
  "is_super_admin": false,
  "enabled_modules": ["crm", "meetings", "payments"],
  "permissions": {
    "crm": {
      "leads":      { "view": "all", "create": true, "edit": "own", "delete": false },
      "statuses":   { "view": "all", "create": true, "edit": true,  "delete": false },
      "activities": { "view": "all", "create": true, "edit": "own", "delete": false },
      "orders":     { "view": "all", "create": true, "edit": "own", "delete": false }
    }
  },
  "iat": 1710000000,
  "exp": 1710086400
}
```

**Notes:**
- `tenant_id` is a UUID identifying the tenant in the SuperAdmin database
- `tenant_slug` is a human-readable slug (e.g. `acme-corp`)
- `is_super_admin` grants full access to all resources if `true`
- `enabled_modules` — your service should verify its own module name is in this list
- Permission values: `false` (deny), `true` (allow, no scope), `"all"`, `"team"`, `"own"`

---

## 8. Permission Key Reference

### CRM Module

| Resource | View | Create | Edit | Delete |
|----------|------|--------|------|--------|
| leads | `crm.leads.view` | `crm.leads.create` | `crm.leads.edit` | `crm.leads.delete` |
| statuses | `crm.statuses.view` | `crm.statuses.create` | `crm.statuses.edit` | `crm.statuses.delete` |
| activities | `crm.activities.view` | `crm.activities.create` | `crm.activities.edit` | `crm.activities.delete` |
| orders | `crm.orders.view` | `crm.orders.create` | `crm.orders.edit` | `crm.orders.delete` |

### Adding a New Module (e.g. `billing`)

1. In SuperAdmin: add `billing` as a module, configure permissions structure
2. In your Node.js app:
   - Change `MODULE_NAME = 'billing'` in `jwtMiddleware.js`
   - Use permission keys like `billing.invoices.view` etc.
   - All other middleware/helpers work unchanged

### Checking Module Access in a Route

```js
// Example: require the 'meetings' module to be enabled
router.get('/meetings', (req, res, next) => {
  if (!req.enabledModules.includes('meetings')) {
    return res.status(403).json({ error: "Module 'meetings' not enabled" });
  }
  next();
}, listMeetings);
```

---

## Quick Reference Card

```
AUTH FLOW
─────────
1. Client → Bearer JWT in Authorization header
2. jwtMiddleware → verify(token, JWT_SECRET_KEY) → set req.tenantId, req.permissions, etc.
3. requirePermission('crm.leads.view') → check req.permissions
4. getTenantScopeFilter(req, 'crm.leads.view') → { tenant_id: X } or { tenant_id: X, owner_user_id: Y }
5. injectTenantId(req, data) → { ...data, tenant_id: X }

EVERY MODEL MUST HAVE
─────────────────────
  tenant_id     (indexed UUID)
  owner_user_id (indexed UUID) — for 'own' scope

PERMISSION LOOKUP
─────────────────
  permissions['crm']['leads']['view']  →  'all' | 'own' | 'team' | true | false

TENANT ISOLATION RULES
──────────────────────
  READ:   always filter by tenant_id (+ owner_user_id if scope == 'own')
  CREATE: always inject tenant_id from request
  UPDATE: never allow tenant_id to change
  DELETE: verify tenant_id matches before deleting
```
