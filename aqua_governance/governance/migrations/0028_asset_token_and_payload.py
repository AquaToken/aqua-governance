"""Stage 2 single-shot migration.

Creates `AssetToken` + `AssetProposalPayload`, backfills them from the existing
`Proposal.asset_*` columns, then drops the 14 legacy columns from `Proposal`.

Forward order:
    1. CreateModel AssetToken, CreateModel AssetProposalPayload, AddIndex × 2, AddField FK.
    2. RunPython backfill: 3-pass — tokens, whitelisted state, payloads.
    3. RunPython pre-drop coverage assertion (every asset proposal has payload+token).
    4. RemoveField × 14 from Proposal.

Reverse order (rollback to 0027):
    1. AddField × 14 back onto Proposal (state-only + schema).
    2. RunPython restore: copy AssetToken.classic_code/issuer/contract_address + payload narratives
       back into Proposal.asset_* for every asset proposal. Same coverage assertion runs.
    3. RemoveField FK + RemoveIndex × 2 + DeleteModel × 2.

Rollback target: `manage.py migrate governance 0027`.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


ASSET_PROPOSAL_TYPES = ('ADD_ASSET', 'REMOVE_ASSET')
ADD_ASSET = 'ADD_ASSET'
REMOVE_ASSET = 'REMOVE_ASSET'
PROPOSAL_VOTED = 'VOTED'
ONCHAIN_EXECUTION_SUCCESS = 'SUCCESS'

NARRATIVE_FIELD_MAP = (
    # (Proposal.<field>, AssetProposalPayload.<field>)
    ('asset_issuer_information', 'issuer_information'),
    ('asset_token_description', 'token_description'),
    ('asset_holder_distribution', 'holder_distribution'),
    ('asset_liquidity', 'liquidity'),
    ('asset_trading_volume', 'trading_volume'),
    ('asset_audit_info', 'audit_info'),
    ('asset_stellar_flags', 'stellar_flags'),
    ('asset_related_projects', 'related_projects'),
    ('asset_community_references', 'community_references'),
    ('asset_aquarius_traction', 'aquarius_traction'),
    ('asset_issuer_commitments', 'issuer_commitments'),
)


def _assert_network_passphrase():
    passphrase = getattr(settings, 'NETWORK_PASSPHRASE', None)
    if not passphrase:
        raise RuntimeError(
            '[0028 single-shot] NETWORK_PASSPHRASE is required for backfill — '
            'refusing to compute canonical contract_addresses without it.',
        )
    return passphrase


def _canonical_contract_address(proposal, passphrase):
    from stellar_sdk import Asset

    explicit = (proposal.asset_contract_address or '').strip()
    code = (proposal.asset_code or '').strip()
    issuer = (proposal.asset_issuer or '').strip()

    derived = None
    if code and issuer:
        try:
            derived = Asset(code, issuer).contract_id(passphrase)
        except Exception as exc:
            print('[0028 backfill] Proposal {}: classic→contract derivation failed: {}'.format(proposal.id, exc))
            derived = None

    if explicit and derived and explicit != derived:
        print(
            '[0028 backfill] Proposal {}: explicit contract_address {} differs from derived {}; '
            'using derived as canonical.'.format(proposal.id, explicit, derived)
        )
        return derived

    return derived or explicit or None


def backfill_asset_records(apps, schema_editor):
    """Pre-drop: create AssetToken + AssetProposalPayload from Proposal.asset_*."""
    Proposal = apps.get_model('governance', 'Proposal')
    AssetToken = apps.get_model('governance', 'AssetToken')
    AssetProposalPayload = apps.get_model('governance', 'AssetProposalPayload')

    passphrase = _assert_network_passphrase()
    asset_proposals = list(
        Proposal.objects.filter(proposal_type__in=ASSET_PROPOSAL_TYPES).order_by('end_at', 'id')
    )

    proposal_keys = {}
    tokens_cache = {}

    # Pass 1 — AssetToken per unique canonical contract_address.
    for proposal in asset_proposals:
        key = _canonical_contract_address(proposal, passphrase)
        if not key:
            print('[0028 backfill] Proposal {}: missing classic pair and contract_address; skipping.'.format(proposal.id))
            continue
        proposal_keys[proposal.id] = key
        if key not in tokens_cache:
            token, _ = AssetToken.objects.get_or_create(
                contract_address=key,
                defaults={
                    'classic_code': (proposal.asset_code or None),
                    'classic_issuer': (proposal.asset_issuer or None),
                },
            )
            tokens_cache[key] = token

    # Pass 2 — restore whitelisted state chronologically by SUCCESS execution.
    for proposal in asset_proposals:
        if proposal.id not in proposal_keys:
            continue
        if proposal.proposal_status != PROPOSAL_VOTED:
            continue
        if proposal.onchain_execution_status != ONCHAIN_EXECUTION_SUCCESS:
            continue
        token = tokens_cache[proposal_keys[proposal.id]]
        ts = proposal.end_at
        if proposal.proposal_type == ADD_ASSET:
            token.whitelisted = True
            token.whitelisted_since = ts
        elif proposal.proposal_type == REMOVE_ASSET:
            token.whitelisted = False
            token.unwhitelisted_since = ts
        token.last_execution_at = ts
        token.save()

    # Pass 3 — AssetProposalPayload per asset proposal.
    for proposal in asset_proposals:
        if proposal.id not in proposal_keys:
            continue
        token = tokens_cache[proposal_keys[proposal.id]]
        payload_kwargs = {
            payload_field: (getattr(proposal, source_field) or '')
            for source_field, payload_field in NARRATIVE_FIELD_MAP
        }
        AssetProposalPayload.objects.create(
            proposal=proposal,
            asset_token=token,
            **payload_kwargs,
        )


def assert_full_asset_coverage(apps, schema_editor):
    """Pre-drop guard: refuse to RemoveField if any asset proposal lacks payload/token.

    Reverse direction also calls this — refuse to AddField+restore if any asset
    proposal is unrepresented in the new tables.
    """
    Proposal = apps.get_model('governance', 'Proposal')
    AssetProposalPayload = apps.get_model('governance', 'AssetProposalPayload')

    expected_ids = set(
        Proposal.objects.filter(proposal_type__in=ASSET_PROPOSAL_TYPES)
        .values_list('id', flat=True)
    )
    covered_ids = set(
        AssetProposalPayload.objects.filter(
            proposal_id__in=expected_ids,
        ).values_list('proposal_id', flat=True)
    )
    missing = expected_ids - covered_ids
    if missing:
        raise RuntimeError(
            '[0028 coverage] {} asset proposals lack AssetProposalPayload: ids={}; '
            'refusing to drop/restore Proposal.asset_* columns.'.format(len(missing), sorted(missing)),
        )


def restore_asset_columns(apps, schema_editor):
    """Reverse direction: copy AssetToken/Payload data back into Proposal.asset_*."""
    Proposal = apps.get_model('governance', 'Proposal')
    AssetProposalPayload = apps.get_model('governance', 'AssetProposalPayload')

    assert_full_asset_coverage(apps, schema_editor)

    payloads = AssetProposalPayload.objects.select_related('asset_token', 'proposal').all()
    for payload in payloads:
        proposal = payload.proposal
        token = payload.asset_token
        proposal.asset_code = token.classic_code or None
        proposal.asset_issuer = token.classic_issuer or None
        proposal.asset_contract_address = token.contract_address or None
        for source_field, payload_attr in NARRATIVE_FIELD_MAP:
            setattr(proposal, source_field, getattr(payload, payload_attr, '') or '')
        proposal.save(update_fields=[
            'asset_code', 'asset_issuer', 'asset_contract_address',
        ] + [source for source, _ in NARRATIVE_FIELD_MAP])


def empty_reverse_backfill(apps, schema_editor):
    """Reverse of backfill_asset_records: noop.

    The actual data restoration happens in restore_asset_columns (the reverse half
    of the RemoveField step, which runs first). By the time we'd reverse this step,
    columns are already back and populated. AssetProposalPayload + AssetToken get
    dropped together with the tables (CreateModel reverse).
    """
    return


def empty_reverse_coverage(apps, schema_editor):
    return


def empty_forward_restore(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ('governance', '0027_proposal_manage_asset_proposals_permission'),
    ]

    operations = [
        # Step 1 — create new tables.
        migrations.CreateModel(
            name='AssetProposalPayload',
            fields=[
                ('proposal', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='asset_payload', serialize=False, to='governance.proposal')),
                ('issuer_information', models.TextField(blank=True, default='')),
                ('token_description', models.TextField(blank=True, default='')),
                ('holder_distribution', models.TextField(blank=True, default='')),
                ('liquidity', models.TextField(blank=True, default='')),
                ('trading_volume', models.TextField(blank=True, default='')),
                ('audit_info', models.TextField(blank=True, default='')),
                ('stellar_flags', models.TextField(blank=True, default='')),
                ('related_projects', models.TextField(blank=True, default='')),
                ('community_references', models.TextField(blank=True, default='')),
                ('aquarius_traction', models.TextField(blank=True, default='')),
                ('issuer_commitments', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='AssetToken',
            fields=[
                ('contract_address', models.CharField(max_length=128, primary_key=True, serialize=False)),
                ('classic_code', models.CharField(blank=True, max_length=64, null=True)),
                ('classic_issuer', models.CharField(blank=True, max_length=56, null=True)),
                ('whitelisted', models.BooleanField(default=False)),
                ('whitelisted_since', models.DateTimeField(blank=True, null=True)),
                ('unwhitelisted_since', models.DateTimeField(blank=True, null=True)),
                ('last_execution_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name='assettoken',
            index=models.Index(fields=['whitelisted'], name='governance__whiteli_d68f30_idx'),
        ),
        migrations.AddIndex(
            model_name='assettoken',
            index=models.Index(fields=['classic_code', 'classic_issuer'], name='governance__classic_9f0c71_idx'),
        ),
        migrations.AddField(
            model_name='assetproposalpayload',
            name='asset_token',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payloads', to='governance.assettoken'),
        ),

        # Step 2 — backfill from Proposal.asset_*.
        migrations.RunPython(backfill_asset_records, empty_reverse_backfill),

        # Step 3 — pre-drop / post-restore coverage assertion.
        migrations.RunPython(assert_full_asset_coverage, empty_reverse_coverage),

        # Step 4 — reverse-side data restoration runs BEFORE RemoveField rollback.
        # Forward direction is a no-op (asset_* are already populated from prior
        # release; this RunPython is a marker for the reverse path which copies
        # data back from AssetProposalPayload+AssetToken into Proposal.asset_*).
        migrations.RunPython(empty_forward_restore, restore_asset_columns),

        # Step 5 — drop the 14 legacy columns from Proposal.
        migrations.RemoveField(model_name='proposal', name='asset_code'),
        migrations.RemoveField(model_name='proposal', name='asset_issuer'),
        migrations.RemoveField(model_name='proposal', name='asset_contract_address'),
        migrations.RemoveField(model_name='proposal', name='asset_issuer_information'),
        migrations.RemoveField(model_name='proposal', name='asset_token_description'),
        migrations.RemoveField(model_name='proposal', name='asset_holder_distribution'),
        migrations.RemoveField(model_name='proposal', name='asset_liquidity'),
        migrations.RemoveField(model_name='proposal', name='asset_trading_volume'),
        migrations.RemoveField(model_name='proposal', name='asset_audit_info'),
        migrations.RemoveField(model_name='proposal', name='asset_stellar_flags'),
        migrations.RemoveField(model_name='proposal', name='asset_related_projects'),
        migrations.RemoveField(model_name='proposal', name='asset_community_references'),
        migrations.RemoveField(model_name='proposal', name='asset_aquarius_traction'),
        migrations.RemoveField(model_name='proposal', name='asset_issuer_commitments'),
    ]
