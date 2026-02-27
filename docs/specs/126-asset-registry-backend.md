# 126: Asset Registry Backend Integration

**Proposal:** #125
**Status:** Draft
**Scope:** aqua-governance (Django) — new proposal type, automatic Soroban execution, asset registry cache

---

## Context

- **Why:** Proposal #125 introduces an on-chain asset eligibility registry (AssetEligibilityRegistry, spec 125). Pools receive AQUA emissions only if all their assets are Allowed in the registry. The backend must (a) support governance proposals specifically for whitelisting/revoking assets, (b) automatically execute passed proposals on the Soroban contract, (c) cache registry state and expose it via API.
- **Contract reference:** `docs/specs/125-asset-eligibility-registry.md` — Soroban AssetEligibilityRegistry
- **Constraints:**
  - aqua-governance is Django 3.2 + DRF; stack must not change
  - stellar-sdk (already a dependency) is used for Soroban view/invoke calls
  - Operator key is stored in env settings (Phase 1); replaced by governance executor in Phase 2

---

## Goals

- Add `proposal_type` to the Proposal model (GENERAL | ASSET_WHITELIST | ASSET_REVOCATION)
- Add `target_asset_address` field for asset proposals
- Celery automatically executes passed asset proposals on Soroban (`set_status`)
- Celery periodically syncs registry state into PostgreSQL
- DRF endpoint `/api/asset-registry/` serves cached asset statuses

---

## Non-goals

- Grace period / grandfathering logic (separate task)
- Emissions pipeline gating (separate task)
- Pool Incentives gating (separate task)
- UI changes
- ProposalExecRecord on Soroban (not implemented in Phase 1 of the contract)
- Validation of whitelist proposal text content (completeness requirements are up to DAO)

---

## Requirements

### Behavior

#### Proposal flow for asset proposals

1. User creates a proposal via `POST /api/proposal/` with `proposal_type=ASSET_WHITELIST` (or `ASSET_REVOCATION`) and `target_asset_address=<SAC address>`.
2. Proposal goes through the standard lifecycle: DISCUSSION → (7 days) → VOTING → VOTED.
   - Asset proposals require a **minimum voting period of 10 days** (`new_end_at - new_start_at >= 10 days`), enforced at submit time. Shorter periods → 400.
3. Once VOTED, Celery evaluates the **pass condition**:
   ```
   passed = (
     vote_for_result > vote_against_result
     AND
     (vote_for_result + vote_against_result)
       / (aqua_circulating_supply + ice_circulating_supply)
       >= percent_for_quorum / 100
   )
   ```
4. If `passed`:
   - Build `evidence_json` (see schema below)
   - Compute `meta_hash = SHA256(evidence_json).hex()`
   - Call `AssetEligibilityRegistry.set_status(operator, asset, status, proposal_id, meta_hash)` via Soroban
   - Record a `ProposalExecution` with tx_hash and status=SUCCESS
5. If `not passed` or quorum not met: record `ProposalExecution` with status=SKIPPED (nothing written on-chain).
6. If the Soroban call fails (network error, timeout, etc.): `ProposalExecution.status = FAILED`; the task will retry on the next run.

#### Evidence JSON

```json
{
  "proposal_id": 125,
  "proposal_url": "https://gov.aqua.network/proposal/125",
  "proposal_type": "ASSET_WHITELIST",
  "target_asset_address": "<SAC address>",
  "tally": {
    "vote_for": "<decimal>",
    "vote_against": "<decimal>",
    "aqua_circulating_supply": "<decimal>",
    "ice_circulating_supply": "<decimal>",
    "percent_for_quorum": 10,
    "start_at": "<ISO datetime>",
    "end_at": "<ISO datetime>"
  },
  "actions": [
    {"asset": "<SAC address>", "status": 1}
  ],
  "computed_at": "<ISO datetime>",
  "computed_by": "aqua-governance-backend"
}
```

#### Registry synchronization

- Celery periodically calls `AssetEligibilityRegistry.list(offset, limit)` (paginated) and upserts all records into the `AssetRecord` table.
- `synced_at` is updated on every successful sync.
- Sync is also triggered immediately after each successful `set_status` call so the new status appears in cache without delay.

---

### API / Interfaces

#### GET /api/asset-registry/

List all assets from the local cache. Public, read-only.

**Query params:**

| Param | Values | Effect |
|-------|--------|--------|
| `status` | `allowed` / `denied` / `unknown` | Filter by status |
| `ordering` | `asset_address`, `updated_ledger`, `synced_at` | Sort order |
| `limit` | integer | Page size |

**Response item:**
```json
{
  "asset_address": "<SAC address>",
  "asset_code": "AQUA",
  "asset_issuer": "<issuer>",
  "status": "allowed",
  "added_ledger": 12345,
  "updated_ledger": 12400,
  "last_proposal_id": 125,
  "meta_hash": "<hex64>",
  "synced_at": "<ISO datetime>"
}
```

#### GET /api/asset-registry/{asset_address}/

Single asset. Returns 404 if not in cache.

#### POST /api/proposal/ — extended for asset proposals

New fields in `ProposalCreateSerializer`:

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `proposal_type` | Choice | no (default=GENERAL) | GENERAL / ASSET_WHITELIST / ASSET_REVOCATION |
| `target_asset_address` | CharField(56) | yes if type != GENERAL | SAC contract address of the asset |

Validation:
- if `proposal_type` != GENERAL and `target_asset_address` is empty → 400 (on `POST /api/proposal/`)
- if `proposal_type` != GENERAL and `new_end_at - new_start_at < 10 days` → 400 (on submit action)

#### GET /api/proposal/?proposal_type=...

Filtering via the new `ProposalTypeFilterBackend`:

| Param | Values |
|-------|--------|
| `proposal_type` | `general` / `asset_whitelist` / `asset_revocation` |

---

### Data / Migrations

#### Changes to Proposal (migration 0026)

```python
proposal_type = models.CharField(
    max_length=32,
    choices=[
        ('GENERAL', 'General'),
        ('ASSET_WHITELIST', 'Asset Whitelist'),
        ('ASSET_REVOCATION', 'Asset Revocation'),
    ],
    default='GENERAL',
)
target_asset_address = models.CharField(max_length=56, null=True, blank=True)
```

#### New model: AssetRecord

```python
class AssetRecord(models.Model):
    UNKNOWN = 'unknown'
    ALLOWED = 'allowed'
    DENIED  = 'denied'
    STATUS_CHOICES = [(UNKNOWN, 'Unknown'), (ALLOWED, 'Allowed'), (DENIED, 'Denied')]

    asset_address    = models.CharField(max_length=56, unique=True)
    asset_code       = models.CharField(max_length=12, blank=True)
    asset_issuer     = models.CharField(max_length=56, blank=True)
    status           = models.CharField(max_length=10, choices=STATUS_CHOICES, default=UNKNOWN)
    added_ledger     = models.PositiveIntegerField(default=0)
    updated_ledger   = models.PositiveIntegerField(default=0)
    last_proposal_id = models.BigIntegerField(null=True, blank=True)
    meta_hash        = models.CharField(max_length=64, blank=True)
    synced_at        = models.DateTimeField(auto_now=True)
```

#### New model: ProposalExecution

```python
class ProposalExecution(models.Model):
    PENDING = 'PENDING'
    SUCCESS = 'SUCCESS'
    FAILED  = 'FAILED'
    SKIPPED = 'SKIPPED'  # proposal did not pass

    proposal      = models.OneToOneField(Proposal, on_delete=models.CASCADE)
    status        = models.CharField(max_length=10, default=PENDING)
    tx_hash       = models.CharField(max_length=64, null=True, blank=True)
    meta_hash     = models.CharField(max_length=64, blank=True)
    evidence_json = models.TextField(blank=True)
    executed_at   = models.DateTimeField(null=True, blank=True)
    error         = models.TextField(blank=True)
```

#### New settings (base.py / env)

```python
ASSET_REGISTRY_CONTRACT_ADDRESS  = env('ASSET_REGISTRY_CONTRACT_ADDRESS', default='')
REGISTRY_OPERATOR_SECRET_KEY     = env('REGISTRY_OPERATOR_SECRET_KEY', default='')
REGISTRY_SYNC_PAGE_LIMIT         = 50  # matches MAX_PAGE_LIMIT in contract
SOROBAN_RPC_URL                  = env('SOROBAN_RPC_URL', default='https://soroban-testnet.stellar.org')
GOV_BASE_URL                     = env('GOV_BASE_URL', default='https://gov.aqua.network')
ASSET_PROPOSAL_MIN_VOTING_DAYS   = 10  # minimum voting period mandated by Proposal #125
```

---

### Celery Tasks

#### task_execute_asset_proposals (every 5 min)

```
proposals = Proposal.objects.filter(
    proposal_type__in=[ASSET_WHITELIST, ASSET_REVOCATION],
    proposal_status=VOTED,
).exclude(proposalexecution__isnull=False)

for proposal in proposals:
    ProposalExecution.objects.create(proposal=proposal, status=PENDING)
    _execute_single_asset_proposal(proposal)
```

`_execute_single_asset_proposal`:
1. Evaluate pass condition (formula above); if not passed → status=SKIPPED, return
2. Build evidence_json, compute meta_hash = SHA256(evidence_json).hex()
3. Determine `status_code`: ASSET_WHITELIST → 1, ASSET_REVOCATION → 2
4. Call `soroban.set_asset_status(target_asset_address, status_code, proposal.id, meta_hash)`
5. On success: update ProposalExecution with status=SUCCESS, tx_hash, executed_at=now()
6. Trigger `task_sync_asset_registry.delay()`

On any Soroban exception: status=FAILED, error=str(exc). Retried on next task run.

#### task_sync_asset_registry (every 10 min)

Paginates through `soroban.fetch_registry_page(offset, limit)` until an empty page is returned. For each item, upserts into `AssetRecord` via `update_or_create`. Skips silently if `ASSET_REGISTRY_CONTRACT_ADDRESS` is not configured.

---

### Errors / Edge Cases

| Case | Behavior |
|------|---------|
| `target_asset_address` missing for ASSET_WHITELIST/REVOCATION | 400 on proposal creation |
| Voting period < 10 days for asset proposal | 400 on proposal submit action |
| Proposal did not pass (quorum not met or against > for) | ProposalExecution.status=SKIPPED; nothing written on-chain |
| Soroban call fails (network/timeout) | status=FAILED; retried on next task_execute run |
| `set_status` called again for same proposal_id | Contract does not reject it (no ProposalExecRecord in Phase 1), but `OneToOneField` on ProposalExecution prevents a second call from the backend |
| `ASSET_REGISTRY_CONTRACT_ADDRESS` not set | task_execute and task_sync log a warning and return early |
| Operator key not set | task_execute raises ImproperlyConfigured → caught as exception → status=FAILED |
| Registry not yet synced | `GET /api/asset-registry/` returns `[]`, not an error |
| Two task_execute instances running simultaneously | `OneToOneField` unique constraint prevents double execution |

---

## Architecture / Module Changes

| File / module | Change |
|--------------|--------|
| `governance/models.py` | + `proposal_type`, `target_asset_address` on Proposal; + `AssetRecord`; + `ProposalExecution` |
| `governance/migrations/0026_*.py` | Migration for new fields and models |
| `governance/serializers_v2.py` | `ProposalCreateSerializer`: + `proposal_type`, `target_asset_address`, pair validation; + `AssetRecordSerializer` |
| `governance/filters.py` | + `ProposalTypeFilterBackend` |
| `governance/views.py` | `ProposalViewSet.filter_backends`: add `ProposalTypeFilterBackend`; + `AssetRegistryView` |
| `governance/urls.py` | + `router.register('asset-registry', AssetRegistryView)` |
| `governance/tasks.py` | + `task_execute_asset_proposals`, `task_sync_asset_registry` |
| `utils/soroban.py` | New — thin wrapper over stellar-sdk v13 for `set_status` invoke and `list` view call |
| `taskapp/__init__.py` | + beat schedule for both new tasks |
| `config/settings/base.py` | + `ASSET_REGISTRY_CONTRACT_ADDRESS`, `REGISTRY_OPERATOR_SECRET_KEY`, `REGISTRY_SYNC_PAGE_LIMIT`, `SOROBAN_RPC_URL`, `GOV_BASE_URL`, `ASSET_PROPOSAL_MIN_VOTING_DAYS` |
| `governance/admin.py` | + `AssetRecord`, `ProposalExecution` (read-only) |

---

## Test Plan

### Unit

- `test_proposal_type_validation`: ASSET_WHITELIST without `target_asset_address` → 400
- `test_asset_proposal_min_voting_period`: submit ASSET_WHITELIST with 9-day voting period → 400; 10-day → accepted
- `test_pass_condition`: pass/fail across different for/against and quorum values
- `test_evidence_json_schema`: evidence contains all required fields
- `test_meta_hash_deterministic`: same evidence input → same SHA256
- `test_asset_record_upsert`: repeated sync does not duplicate records
- `test_proposal_execution_unique`: repeated task_execute run does not create a second ProposalExecution
- `test_proposal_type_filter`: `?proposal_type=asset_whitelist` filters correctly

### Integration

- `test_full_flow_whitelist`: create ASSET_WHITELIST proposal → advance to VOTED → mock Soroban → ProposalExecution.status=SUCCESS
- `test_full_flow_failed_proposal`: proposal did not reach quorum → status=SKIPPED, Soroban not called
- `test_sync_registry`: mock `list()` response → AssetRecord upserted correctly
- `test_asset_registry_api`: GET /api/asset-registry/ returns correct JSON
- `test_soroban_error_retry`: Soroban raises exception → status=FAILED → next task run retries

### E2E

- (stagenet) full run: create proposal → vote → verify ProposalExecution.status=SUCCESS and AssetRecord.status=ALLOWED

---

## Risks and Rollback

| Risk | Mitigation |
|------|-----------|
| Operator key compromised | `REGISTRY_OPERATOR_SECRET_KEY` stored in env/vault; rotated via `add_writer/remove_writer` on the contract without redeploying the backend |
| Soroban network downtime | task_execute sets FAILED + auto-retries; manual re-execution via Django admin as fallback |
| Invalid `target_asset_address` (not a SAC) | Soroban returns an error → status=FAILED; proposal will not auto-retry (requires admin intervention) |
| Double write (Soroban tx sent but DB save fails) | tx_hash in ProposalExecution allows on-chain verification; a repeated `set_status` with the same proposal_id is safe (contract overwrites with identical data) |
| Feature rollback | `proposal_type` defaults to GENERAL — existing proposals and API are unaffected. New Celery tasks can be disabled via beat schedule without a code change. |

---

## Out of Scope (Phase 1)

- Grace period: separate task
- Emissions gating: separate task
- Pool Incentives gating: separate task
- `execute_proposal` on the contract instead of `set_status` (ProposalExecRecord): Phase 2
- Replacing the operator with a governance executor: Phase 2
