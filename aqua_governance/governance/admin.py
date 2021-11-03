from django.contrib import admin

from aqua_governance.governance.models import Proposal


@admin.register(Proposal)
class MarketKeyAdmin(admin.ModelAdmin):
    list_display = ['proposed_by', 'title', 'start_at', 'end_at']
    readonly_fields = ['vote_for_result', 'vote_against_result']
    search_fields = ['proposed_by']
    fields = [
        'proposed_by', 'title', 'text', 'vote_for_issuer', 'vote_against_issuer', 'start_at', 'end_at', 'hide',
        'vote_for_result', 'vote_against_result',
    ]
    list_filter = ('start_at', 'end_at')
