from django.contrib import admin

from aqua_governance.governance.forms import ProposalAdminForm
from aqua_governance.governance.models import LogVote, Proposal


@admin.register(Proposal)
class ProposalAdmin(admin.ModelAdmin):
    list_display = ['proposed_by', 'title', 'start_at', 'end_at']
    readonly_fields = ['vote_for_issuer', 'vote_against_issuer', 'vote_for_result', 'vote_against_result']
    search_fields = ['proposed_by']
    fields = [
        'proposed_by', 'title', 'text', 'vote_for_issuer', 'vote_against_issuer', 'start_at', 'end_at', 'hide',
        'vote_for_result', 'vote_against_result',
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


@admin.register(LogVote)
class LogVoteAdmin(admin.ModelAdmin):
    list_display = ['account_issuer', 'amount', 'vote_choice', 'created_at']
    readonly_fields = [
        'claimable_balance_id', 'transaction_link', 'account_issuer', 'amount', 'proposal', 'vote_choice', 'created_at',
    ]
    search_fields = ['proposal__id', 'proposal__vote_for_issuer', 'proposal__vote_against_issuer']
    fields = [
        'claimable_balance_id', 'transaction_link', 'account_issuer', 'amount', 'proposal', 'vote_choice', 'created_at',
    ]
    list_filter = ('vote_choice', )

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
