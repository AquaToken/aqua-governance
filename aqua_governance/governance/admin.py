from django.contrib import admin

from aqua_governance.governance.forms import ProposalAdminForm
from aqua_governance.governance.models import LogVote, Proposal


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'proposed_by', 'hide', 'proposal_status', 'payment_status',
        'title', 'proposal_type', 'start_at', 'end_at', 'onchain_action_type', 'onchain_execution_status',
        '_list_display_quorum',
    ]
    readonly_fields = [
        'vote_for_issuer', 'vote_against_issuer', 'abstain_issuer', 'version',
        'vote_for_result', 'vote_against_result', 'vote_abstain_result', 'aqua_circulating_supply', 'ice_circulating_supply',
        'payment_status', 'onchain_execution_status', 'onchain_execution_tx_hash',
    ]
    search_fields = ['proposed_by']
    fields = [
        'proposed_by', 'title', 'text', 'vote_for_issuer', 'vote_against_issuer', 'abstain_issuer',
        'proposal_status', 'payment_status', 'version', 'start_at', 'end_at', 'hide',
        'vote_for_result', 'vote_against_result', 'vote_abstain_result', 'aqua_circulating_supply',
        'ice_circulating_supply', 'discord_channel_url', 'discord_channel_name', 'discord_username',
        'proposal_type',
        'asset_code', 'asset_issuer', 'asset_contract_address', 'asset_issuer_information',
        'asset_token_description', 'asset_holder_distribution', 'asset_liquidity', 'asset_trading_volume',
        'asset_audit_info', 'asset_stellar_flags', 'asset_related_projects', 'asset_community_references',
        'asset_aquarius_traction', 'asset_issuer_commitments',
        'onchain_action_type', 'onchain_action_args', 'onchain_execution_status', 'onchain_execution_tx_hash',
    ]
    list_filter = ('start_at', 'end_at')
    form = ProposalAdminForm

    class Media:
        css = {
            'all': ('admin/django_quill.css',),
        }

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return self.readonly_fields + ['start_at', 'end_at']

        return self.readonly_fields

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
