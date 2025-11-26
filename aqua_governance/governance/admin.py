from django.contrib import admin

from aqua_governance.governance.forms import ProposalAdminForm
from aqua_governance.governance.models import LogVote, Proposal


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = [
        'proposed_by', 'hide', 'proposal_status', 'payment_status',
        'title', 'start_at', 'end_at', '_list_display_quorum',
    ]
    readonly_fields = [
        'vote_for_issuer', 'vote_against_issuer', 'version',
        'vote_for_result', 'vote_against_result', 'aqua_circulating_supply', 'ice_circulating_supply',
        'payment_status',
    ]
    search_fields = ['proposed_by']
    fields = [
        'proposed_by', 'title', 'text', 'vote_for_issuer', 'vote_against_issuer',
        'proposal_status', 'payment_status', 'version', 'start_at', 'end_at', 'hide',
        'vote_for_result', 'vote_against_result', 'aqua_circulating_supply', 'ice_circulating_supply',
        'discord_channel_url', 'discord_channel_name', 'discord_username',
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
        if obj.vote_for_result + obj.vote_against_result >= (float(obj.aqua_circulating_supply) + float(obj.ice_circulating_supply)) * obj.percent_for_quorum / 100:
            return 'Enough votes'
        return 'Not enough votes'
    _list_display_quorum.short_description = 'quorum'


@admin.register(LogVote)
class LogVoteAdmin(admin.ModelAdmin):
    list_display = ['asset_code', 'amount', 'vote_choice', 'created_at']
    readonly_fields = [
        'asset_code',
        'claimable_balance_id', 'transaction_link', 'account_issuer', 'amount', 'proposal', 'vote_choice', 'created_at',
    ]
    search_fields = ['proposal__id', 'proposal__vote_for_issuer', 'proposal__vote_against_issuer']
    fields = [
        'asset_code',
        'claimable_balance_id', 'transaction_link', 'account_issuer', 'amount', 'proposal', 'vote_choice', 'created_at',
    ]
    list_filter = ('vote_choice', 'claimed')

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
