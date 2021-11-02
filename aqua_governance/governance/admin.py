from django.contrib import admin

from aqua_governance.governance.models import Proposal


@admin.register(Proposal)
class MarketKeyAdmin(admin.ModelAdmin):
    list_display = ['proposed_by', 'title', 'start_at', 'end_at']
    readonly_fields = ['vote_for_result', 'vote_against_result']
    search_fields = ['proposed_by']
    list_filter = ('start_at', 'end_at')
