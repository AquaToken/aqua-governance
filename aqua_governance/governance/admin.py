from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction

from aqua_governance.governance.asset_proposal_writer import upsert_asset_records
from aqua_governance.governance.asset_serializer_fields import ASSET_FIELDS
from aqua_governance.governance.forms import ProposalAdminForm
from aqua_governance.governance.models import AssetProposalPayload, AssetToken, LogVote, Proposal


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'proposed_by', 'hide', 'draft', 'action', 'proposal_status', 'payment_status',
        'title', 'proposal_type', 'start_at', 'end_at', 'created_at', 'last_updated_at',
        'onchain_action_type', 'onchain_execution_status',
        '_list_display_quorum',
    ]
    readonly_fields = [
        'vote_for_issuer', 'vote_against_issuer', 'abstain_issuer', 'version',
        'created_at', 'last_updated_at',
        'draft', 'status', 'action',
        'proposal_status', 'payment_status',
        'vote_for_result', 'vote_against_result', 'vote_abstain_result',
        'aqua_circulating_supply', 'ice_circulating_supply', 'percent_for_quorum',
        'onchain_action_type', 'onchain_action_args',
        'onchain_execution_status', 'onchain_execution_tx_hash',
        'onchain_execution_started_at', 'onchain_execution_submitted_at', 'onchain_execution_poll_count',
        'new_title', 'new_text', 'new_transaction_hash', 'new_envelope_xdr', 'new_start_at', 'new_end_at',
    ]
    search_fields = ['proposed_by', 'title', 'transaction_hash', 'new_transaction_hash']
    # Use `fieldsets` so the 14 asset_* form fields (declared on
    # AssetPayloadFormMixin) survive `modelform_factory` filtering. Fieldsets
    # are NOT validated against the model by `fields_for_model` — they're a pure
    # layout directive.
    fieldsets = (
        (None, {
            'fields': (
                'proposed_by', 'title', 'text', 'proposal_type', 'is_simple_proposal',
                'hide', 'draft', 'status', 'action',
                'proposal_status', 'payment_status', 'version', 'created_at', 'last_updated_at',
                'transaction_hash', 'envelope_xdr', 'start_at', 'end_at',
                'new_title', 'new_text', 'new_transaction_hash', 'new_envelope_xdr', 'new_start_at', 'new_end_at',
                'vote_for_issuer', 'vote_against_issuer', 'abstain_issuer',
                'vote_for_result', 'vote_against_result', 'vote_abstain_result',
                'aqua_circulating_supply', 'ice_circulating_supply', 'percent_for_quorum',
                'discord_channel_url', 'discord_channel_name', 'discord_username',
            ),
        }),
        ('Asset payload', {
            'classes': ('asset-proposal-section',),
            'fields': (
                'asset_code', 'asset_issuer', 'asset_contract_address',
                'asset_issuer_information', 'asset_token_description', 'asset_holder_distribution',
                'asset_liquidity', 'asset_trading_volume', 'asset_audit_info',
                'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
                'asset_aquarius_traction', 'asset_issuer_commitments',
            ),
        }),
        ('Onchain execution', {
            'classes': ('asset-proposal-section',),
            'fields': (
                'onchain_action_type', 'onchain_action_args',
                'onchain_execution_status', 'onchain_execution_tx_hash',
                'onchain_execution_started_at', 'onchain_execution_submitted_at',
                'onchain_execution_poll_count',
            ),
        }),
    )
    asset_fieldset = (
        'Asset payload', {
            'classes': ('asset-proposal-section',),
            'fields': (
                'asset_code', 'asset_issuer', 'asset_contract_address',
                'asset_issuer_information', 'asset_token_description', 'asset_holder_distribution',
                'asset_liquidity', 'asset_trading_volume', 'asset_audit_info',
                'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
                'asset_aquarius_traction', 'asset_issuer_commitments',
            ),
        },
    )
    onchain_fieldset = (
        'Onchain execution', {
            'classes': ('asset-proposal-section',),
            'fields': (
                'onchain_action_type', 'onchain_action_args',
                'onchain_execution_status', 'onchain_execution_tx_hash',
                'onchain_execution_started_at', 'onchain_execution_submitted_at',
                'onchain_execution_poll_count',
            ),
        },
    )
    list_filter = ('proposal_type', 'proposal_status', 'payment_status', 'draft', 'hide', 'action', 'start_at', 'end_at')
    form = ProposalAdminForm

    class Media:
        css = {
            'all': ('admin/django_quill.css',),
        }
        js = ('admin/proposal_asset_sections.js',)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        if request.method == 'POST':
            with transaction.atomic():
                return super().changeform_view(request, object_id, form_url, extra_context)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        fieldsets = [
            fieldset
            for fieldset in fieldsets
            if fieldset[0] not in (self.asset_fieldset[0], 'Onchain execution')
        ]
        if obj is None or self._is_asset_object(obj):
            fieldsets.insert(1, self.asset_fieldset)
            fieldsets.append(self.onchain_fieldset)
        return fieldsets

    def get_form(self, request, obj=None, change=False, **kwargs):
        # `modelform_factory` filters out declared form fields (like the 14
        # asset_* on AssetPayloadFormMixin) when `fields=...` is restricted to
        # model fields only. Augment the fields list with asset_* names so the
        # mixin's declared fields survive the factory.
        existing_fields = kwargs.get('fields')
        if existing_fields and existing_fields != '__all__':
            fields_with_asset = list(existing_fields)
            for name in ASSET_FIELDS:
                if name not in fields_with_asset:
                    fields_with_asset.append(name)
            kwargs['fields'] = fields_with_asset

        form_class = super().get_form(request, obj, change=change, **kwargs)

        class RequestBoundProposalAdminForm(form_class):
            def __init__(self, *args, **form_kwargs):
                self.request_user = request.user
                super().__init__(*args, **form_kwargs)

        return RequestBoundProposalAdminForm

    def _is_asset_manager(self, request) -> bool:
        return bool(
            request.user.is_authenticated
            and not request.user.is_superuser
            and request.user.has_perm('governance.manage_asset_proposals')
        )

    def _is_asset_object(self, obj) -> bool:
        return bool(obj and Proposal.is_asset_proposal_type(obj.proposal_type))

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self._is_asset_manager(request):
            return queryset.filter(proposal_type__in=Proposal.ASSET_PROPOSAL_TYPES)
        return queryset

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == 'proposal_type' and self._is_asset_manager(request):
            kwargs['choices'] = [
                choice for choice in db_field.choices
                if choice[0] in Proposal.ASSET_PROPOSAL_TYPES
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(self.readonly_fields)
        if self._is_asset_manager(request) and (obj is None or self._is_asset_object(obj)):
            readonly_fields.remove('proposal_status')
        if obj:
            readonly_fields += [
                'proposed_by',
                'proposal_type',
                'transaction_hash', 'envelope_xdr',
            ]
        if not request.user.is_superuser:
            readonly_fields.append('hide')
        return readonly_fields

    def has_add_permission(self, request):
        return request.user.is_superuser or request.user.has_perm('governance.manage_asset_proposals')

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if not request.user.has_perm('governance.manage_asset_proposals'):
            return False
        if obj is None:
            return True
        return self._is_asset_object(obj)

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if not request.user.has_perm('governance.manage_asset_proposals'):
            return False
        if obj is None:
            return True
        return self._is_asset_object(obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop('delete_selected', None)
        return actions

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and not obj.is_asset_proposal:
            raise PermissionDenied('Managers can manage only asset proposals.')
        super().save_model(request, obj, form, change)
        asset_data = (form.cleaned_data or {}).get('_asset_data') or {}
        if asset_data:
            upsert_asset_records(obj, asset_data)

    def _list_display_quorum(self, obj):
        if obj.vote_for_result + obj.vote_against_result + obj.vote_abstain_result >= (
            float(obj.ice_circulating_supply)) * obj.percent_for_quorum / 100:
            return 'Enough votes'
        return 'Not enough votes'

    _list_display_quorum.short_description = 'quorum'


@admin.register(LogVote)
class LogVoteAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'asset_code',
        'vote_choice',
        'amount',
        'original_amount',
        'voted_amount',
        'group_index',
        'claimed',
        'hide',
        'created_at',
        'proposal',
        'account_issuer',
        'claimable_balance_id',
    ]
    readonly_fields = [
        'id',
        'asset_code',
        'claimable_balance_id',
        'transaction_link',
        'account_issuer',
        'proposal',
        'vote_choice',
        'created_at',
        'key',
        'group_index',
        'amount',
        'original_amount',
        'voted_amount',
        'claimed',
        'hide',
    ]
    search_fields = [
        '=id',
        'claimable_balance_id',
        'account_issuer',
        'key',
        '=proposal__id',
        'proposal__vote_for_issuer',
        'proposal__vote_against_issuer',
        'proposal__abstain_issuer',
    ]
    fields = readonly_fields
    list_filter = ('vote_choice', 'asset_code', 'claimed', 'hide')
    ordering = ('-created_at',)
    list_select_related = ('proposal',)

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


class AssetProposalPayloadInline(admin.StackedInline):
    model = AssetProposalPayload
    can_delete = False
    extra = 0
    max_num = 1
    readonly_fields = (
        'asset_token',
        'issuer_information', 'token_description', 'holder_distribution',
        'liquidity', 'trading_volume', 'audit_info', 'stellar_flags',
        'related_projects', 'community_references', 'aquarius_traction',
        'issuer_commitments', 'created_at',
    )

    def has_add_permission(self, request, obj=None):
        return False


ProposalAdmin.inlines = [AssetProposalPayloadInline]


def _recompute_whitelisted_from_history(token):
    """Walk the token's VOTED+SUCCESS proposal history and reset whitelisted state.

    Pure-Python recomputation — does NOT touch the onchain contract. Useful as a
    safety net when `_sync_asset_token_on_success` was missed for some reason and
    the AssetToken row drifted from the history's implied state.

    Wrapped in `transaction.atomic()` and re-fetches the token under
    `select_for_update()` so concurrent poll-task SUCCESS sync cannot clobber the
    recomputed state mid-way through. Closes audit finding X2 (admin-vs-poll race).
    """
    with transaction.atomic():
        # Re-fetch under row lock — ignores any stale state the caller might
        # have read before the action fired. Concurrent SUCCESS sync on the same
        # token will block here until we commit / rollback.
        locked_token = AssetToken.objects.select_for_update().get(pk=token.pk)

        payloads = locked_token.payloads.select_related('proposal').filter(
            proposal__proposal_status=Proposal.VOTED,
            proposal__onchain_execution_status=Proposal.ONCHAIN_EXECUTION_SUCCESS,
        ).order_by('proposal__end_at', 'proposal__id')

        new_whitelisted = False
        new_whitelisted_since = None
        new_unwhitelisted_since = None
        new_last_execution_at = None
        for payload in payloads:
            p = payload.proposal
            ts = p.end_at
            if p.proposal_type == Proposal.PROPOSAL_TYPE_ADD_ASSET:
                new_whitelisted = True
                new_whitelisted_since = ts
            elif p.proposal_type == Proposal.PROPOSAL_TYPE_REMOVE_ASSET:
                new_whitelisted = False
                new_unwhitelisted_since = ts
            new_last_execution_at = ts

        locked_token.whitelisted = new_whitelisted
        locked_token.whitelisted_since = new_whitelisted_since
        locked_token.unwhitelisted_since = new_unwhitelisted_since
        locked_token.last_execution_at = new_last_execution_at
        locked_token.save(update_fields=[
            'whitelisted', 'whitelisted_since', 'unwhitelisted_since',
            'last_execution_at', 'updated_at',
        ])


@admin.register(AssetToken)
class AssetTokenAdmin(admin.ModelAdmin):
    list_display = (
        'contract_address', 'classic_code', 'classic_issuer', 'whitelisted',
        'whitelisted_since', 'unwhitelisted_since', 'last_execution_at',
    )
    search_fields = ('contract_address', 'classic_code', 'classic_issuer')
    list_filter = ('whitelisted',)
    readonly_fields = (
        'contract_address', 'classic_code', 'classic_issuer', 'whitelisted',
        'whitelisted_since', 'unwhitelisted_since', 'last_execution_at',
        'created_at', 'updated_at',
    )
    actions = ['recompute_whitelisted_from_history']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def recompute_whitelisted_from_history(self, request, queryset):
        """Safety-net action: recompute `whitelisted` from VOTED+SUCCESS history.

        Does NOT touch onchain contract — purely local DB recalc. Use when manual
        verification suggests AssetToken drifted from the history. Onchain
        reconciliation is a separate feature (not in this PR).
        """
        updated = 0
        for token in queryset:
            _recompute_whitelisted_from_history(token)
            updated += 1
        self.message_user(
            request,
            f'Recomputed whitelisted from history for {updated} token(s). '
            f'Note: onchain contract state was NOT touched.',
        )
    recompute_whitelisted_from_history.short_description = (
        'Recompute whitelisted from VOTED+SUCCESS proposal history (local only)'
    )
