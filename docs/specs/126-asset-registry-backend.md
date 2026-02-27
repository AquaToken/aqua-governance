# 126: Asset Registry Backend Integration

**Proposal:** #125
**Status:** Draft
**Scope:** aqua-governance (Django) — новый тип пропозала, автоматическое исполнение на Soroban, кеш реестра ассетов

---

## Контекст

- **Почему делаем:** Proposal #125 вводит ончейн реестр допущенных ассетов (AssetEligibilityRegistry, spec 125). Пулы получают AQUA emissions только если все ассеты в них — Allowed в реестре. Бекенд должен (а) позволить создавать governance proposals специально для вайтлистинга/отзыва ассетов, (б) после прохождения голосования автоматически исполнять результат на Soroban-контракте, (в) кешировать состояние реестра и отдавать через API.
- **Контракт-ссылка:** `docs/specs/125-asset-eligibility-registry.md` — Soroban AssetEligibilityRegistry
- **Ограничения:**
  - aqua-governance написан на Django 3.2 + DRF; менять стек нельзя
  - stellar-sdk (уже в зависимостях) используется для Soroban view/invoke calls
  - Ключ оператора хранится в env settings (Phase 1); в Phase 2 заменяется governance executor

---

## Цели

- Добавить `proposal_type` в модель Proposal (GENERAL | ASSET_WHITELIST | ASSET_REVOCATION)
- Добавить структурированное поле `target_asset_address` для ассет-пропозалов
- Celery автоматически исполняет прошедшие asset proposals на Soroban (`set_status`)
- Celery периодически синхронизирует состояние реестра в PostgreSQL
- DRF endpoint `/api/asset-registry/` отдаёт кешированные статусы ассетов

---

## Не цели

- Grace period / grandfathering логика (отдельная задача)
- Emissions pipeline gating (отдельная задача)
- Pool Incentives gating (отдельная задача)
- UI изменения
- ProposalExecRecord на Soroban (не реализовано в Phase 1 контракта)
- Валидация содержимого текста whitelist proposal (требования к полноте описания — на усмотрение DAO)

---

## Требования

### Поведение

#### Proposal flow для asset proposals

1. Пользователь создаёт proposal через `POST /api/proposal/` с `proposal_type=ASSET_WHITELIST` (или `ASSET_REVOCATION`) и `target_asset_address=<SAC address>`.
2. Proposal проходит стандартный lifecycle: DISCUSSION → (7 дней) → VOTING → VOTED.
3. После перехода в VOTED Celery вычисляет **pass condition**:
   ```
   passed = (
     vote_for_result > vote_against_result
     AND
     (vote_for_result + vote_against_result)
       / (aqua_circulating_supply + ice_circulating_supply)
       >= percent_for_quorum / 100
   )
   ```
4. Если `passed`:
   - Формируется `evidence_json` (см. структуру ниже)
   - Вычисляется `meta_hash = SHA256(evidence_json).hex()`
   - Вызывается `AssetEligibilityRegistry.set_status(operator, asset, status, proposal_id, meta_hash)` через Soroban
   - Записывается `ProposalExecution` с tx_hash и статусом SUCCESS
5. Если `not passed` или quorum не набран: `ProposalExecution` с status=SKIPPED (пропозал проиграл, ничего ончейн не пишется).
6. Если Soroban вызов упал (network error, etc.): `ProposalExecution.status = FAILED`, задача будет повторена при следующем запуске.

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

#### Синхронизация реестра

- Celery периодически вызывает `AssetEligibilityRegistry.list(offset, limit)` (пагинированно) и upsert'ит все записи в таблицу `AssetRecord`.
- `synced_at` обновляется при каждой успешной синхронизации.
- Синхронизация запускается также после каждого успешного `set_status` (чтобы новый статус сразу попал в кеш).

---

### API / интерфейсы

#### GET /api/asset-registry/

Список всех ассетов из кеша. Только публичный чтение.

**Query params:**

| Param | Values | Effect |
|-------|--------|--------|
| `status` | `allowed` / `denied` / `unknown` | Фильтр по статусу |
| `ordering` | `asset_address`, `updated_ledger`, `synced_at` | Сортировка |
| `limit` | integer | Размер страницы |

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

Один ассет. 404 если не в кеше.

#### POST /api/proposal/ — расширение для asset proposals

Новые поля в `ProposalCreateSerializer`:

| Поле | Тип | Обязательность | Примечание |
|------|-----|---------------|-----------|
| `proposal_type` | Choice | нет (default=GENERAL) | GENERAL / ASSET_WHITELIST / ASSET_REVOCATION |
| `target_asset_address` | CharField(56) | да, если type != GENERAL | Адрес SAC контракта ассета |

Валидация: если `proposal_type` != GENERAL и `target_asset_address` пустой → 400.

#### GET /api/proposal/?proposal_type=...

Фильтрация через новый `ProposalTypeFilterBackend`:

| Param | Values |
|-------|--------|
| `proposal_type` | `general` / `asset_whitelist` / `asset_revocation` |

---

### Данные / миграции

#### Изменения в Proposal (0024_proposal_type_and_target_asset.py)

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

#### Новая модель: AssetRecord

```python
class AssetRecord(models.Model):
    UNKNOWN = 'unknown'
    ALLOWED = 'allowed'
    DENIED  = 'denied'
    STATUS_CHOICES = [(UNKNOWN, 'Unknown'), (ALLOWED, 'Allowed'), (DENIED, 'Denied')]

    asset_address  = models.CharField(max_length=56, unique=True)
    asset_code     = models.CharField(max_length=12, blank=True)
    asset_issuer   = models.CharField(max_length=56, blank=True)
    status         = models.CharField(max_length=10, choices=STATUS_CHOICES, default=UNKNOWN)
    added_ledger   = models.PositiveIntegerField(default=0)
    updated_ledger = models.PositiveIntegerField(default=0)
    last_proposal_id = models.BigIntegerField(null=True, blank=True)
    meta_hash      = models.CharField(max_length=64, blank=True)
    synced_at      = models.DateTimeField(auto_now=True)
```

#### Новая модель: ProposalExecution

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

#### Новые настройки (base.py / env)

```python
ASSET_REGISTRY_CONTRACT_ADDRESS = env('ASSET_REGISTRY_CONTRACT_ADDRESS', default='')
REGISTRY_OPERATOR_SECRET_KEY    = env('REGISTRY_OPERATOR_SECRET_KEY', default='')
REGISTRY_SYNC_PAGE_LIMIT        = 50  # MAX_PAGE_LIMIT контракта
```

---

### Celery задачи

#### task_execute_asset_proposals (каждые 5 мин)

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
1. Вычислить pass condition (формула выше)
2. Если не passed → update status=SKIPPED, return
3. Сформировать evidence_json, вычислить meta_hash
4. Определить `status_code`: ASSET_WHITELIST → 1, ASSET_REVOCATION → 2
5. Вызвать Soroban `set_status(operator, asset_address, status_code, proposal.id, meta_hash)`
6. Получить tx_hash из ответа
7. Update ProposalExecution: status=SUCCESS, tx_hash, executed_at=now()
8. Вызвать `task_sync_asset_registry.delay()`

При ошибке Soroban: update status=FAILED, error=str(exc). Повтор при следующем запуске.

#### task_sync_asset_registry (каждые 10 мин)

```
contract = AssetRegistryContract(settings.ASSET_REGISTRY_CONTRACT_ADDRESS)
offset = 0
while True:
    page = contract.list(offset, settings.REGISTRY_SYNC_PAGE_LIMIT)
    for item in page.items:
        AssetRecord.objects.update_or_create(
            asset_address=item.asset_address,
            defaults={status, added_ledger, updated_ledger, last_proposal_id, meta_hash}
        )
    if len(page.items) < settings.REGISTRY_SYNC_PAGE_LIMIT:
        break
    offset += len(page.items)
```

`asset_code` / `asset_issuer` заполняются при первом создании через stellar-sdk asset lookup (или оставляем пустыми, если SAC-адрес не резолвится).

---

### Ошибки / краевые кейсы

| Кейс | Поведение |
|------|----------|
| `target_asset_address` не указан для ASSET_WHITELIST/REVOCATION | 400 при создании proposal |
| Proposal проиграл (quorum не набран / against > for) | ProposalExecution.status=SKIPPED, ничего на контракт не пишется |
| Soroban вызов упал (network/timeout) | status=FAILED, повтор при следующем запуске task_execute |
| `set_status` уже был вызван для этого proposal_id | контракт не отклоняет (не реализовано ProposalExecRecord в Phase 1), но OneToOneField на ProposalExecution предотвращает повторный вызов из бекенда |
| `ASSET_REGISTRY_CONTRACT_ADDRESS` не задан | task_execute и task_sync логируют ошибку и выходят без паники |
| Ключ оператора не задан | task_execute логирует ошибку, status=FAILED |
| Реестр пуст (ещё не синхронизирован) | `/api/asset-registry/` возвращает `[]`, не ошибку |
| Одновременный запуск двух экземпляров task_execute | select_for_update или unique constraint на ProposalExecution предотвращает двойное исполнение |

---

## Архитектура / изменения по модулям

| Файл / модуль | Изменение |
|--------------|----------|
| `governance/models.py` | +`proposal_type`, `+target_asset_address` в Proposal; +`AssetRecord`; +`ProposalExecution` |
| `governance/migrations/0024_*.py` | Миграция под новые поля и модели |
| `governance/serializers_v2.py` | `ProposalCreateSerializer`: +`proposal_type`, `+target_asset_address`, валидация пары |
| `governance/filters.py` | +`ProposalTypeFilterBackend` |
| `governance/views.py` | `ProposalViewSet.filter_backends`: добавить `ProposalTypeFilterBackend` |
| `governance/serializers.py` (новый) | `AssetRecordSerializer` |
| `governance/views.py` | +`AssetRegistryView` (ListModelMixin + RetrieveModelMixin) |
| `governance/urls.py` | +`router.register('asset-registry', AssetRegistryView)` |
| `governance/tasks.py` | +`task_execute_asset_proposals`, +`task_sync_asset_registry`, `+_execute_single_asset_proposal` |
| `utils/soroban.py` (новый) | Тонкая обёртка над stellar-sdk для `set_status` invoke и `list` view call |
| `taskapp/__init__.py` | +beat schedule для двух новых задач |
| `config/settings/base.py` | +`ASSET_REGISTRY_CONTRACT_ADDRESS`, `+REGISTRY_OPERATOR_SECRET_KEY`, `+REGISTRY_SYNC_PAGE_LIMIT` |
| `governance/admin.py` | +`AssetRecord`, `+ProposalExecution` в Django admin (read-only) |

---

## Тест-план

### Юнит

- `test_proposal_type_validation`: ASSET_WHITELIST без `target_asset_address` → 400
- `test_pass_condition`: pass/fail при разных соотношениях for/against и quorum
- `test_evidence_json_schema`: структура evidence содержит все обязательные поля
- `test_meta_hash_deterministic`: один и тот же evidence → один и тот же sha256
- `test_asset_record_upsert`: повторный sync не дублирует записи
- `test_proposal_execution_unique`: повторный запуск task_execute не создаёт второй ProposalExecution
- `test_proposal_type_filter`: `?proposal_type=asset_whitelist` фильтрует корректно

### Интеграционные

- `test_full_flow_whitelist`: создать ASSET_WHITELIST proposal → провести через VOTED → mock Soroban → ProposalExecution.status=SUCCESS
- `test_full_flow_failed_proposal`: proposal не набрал quorum → status=SKIPPED, Soroban не вызывается
- `test_sync_registry`: mock list() контракта → AssetRecord upserted корректно
- `test_asset_registry_api`: GET /api/asset-registry/ возвращает корректный JSON
- `test_soroban_error_retry`: Soroban падает → status=FAILED → следующий запуск задачи пробует снова

### E2E

- (stagenet) полный прогон: создать пропозал → проголосовать → убедиться в ProposalExecution.status=SUCCESS и AssetRecord.status=ALLOWED

---

## Риски и откат

| Риск | Митигация |
|------|----------|
| Ключ оператора скомпрометирован | `REGISTRY_OPERATOR_SECRET_KEY` в env/vault; ротируется через `add_writer/remove_writer` на контракте без деплоя нового бекенда |
| Soroban network downtime | task_execute FAILED + retry; ручное исполнение через admin action как fallback |
| Некорректный `target_asset_address` (не SAC) | Soroban вернёт ошибку → status=FAILED; proposal не будет повторно исполнен автоматически (нужно admin вмешательство) |
| Двойной write (при сбое после Soroban tx, но до сохранения в БД) | tx_hash в ProposalExecution позволяет проверить on-chain; повторный set_status на контракте с тем же proposal_id безвреден (статус перезапишется теми же данными) |
| Откат фичи | `proposal_type` default=GENERAL — старые proposals и API не сломаются. Новые Celery задачи можно отключить через beat schedule без кода. |

---

## Out of scope (Phase 1)

- Grace period: отдельная задача
- Emissions gating: отдельная задача
- Pool Incentives gating: отдельная задача
- `execute_proposal` на контракте вместо `set_status` (ProposalExecRecord): Phase 2
- Замена оператора на governance executor: Phase 2
