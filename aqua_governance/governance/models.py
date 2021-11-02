from django.db import models


class Proposal(models.Model):
    proposed_by = models.CharField(max_length=56)
    title = models.CharField(max_length=256)
    text = models.CharField(max_length=256)

    vote_for_issuer = models.CharField(max_length=56)
    vote_against_issuer = models.CharField(max_length=56)

    created_at = models.DateTimeField(auto_now_add=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()

    hide = models.BooleanField(default=False)

    vote_for_result = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)
    vote_against_result = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)


class LogVote(models.Model):
    VOTE_FOR = 'vote_for'
    VOTE_AGAINST = 'vote_against'
    VOTE_TYPES = (
        (VOTE_FOR, 'Vote For'),
        (VOTE_AGAINST, 'Vote Against'),
    )
    claimable_balance_id = models.CharField(max_length=72, unique=True, null=True)
    account_issuer = models.CharField(max_length=56, null=True)
    amount = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)
    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, null=True)
    vote_choice = models.CharField(max_length=15, choices=VOTE_TYPES, default=None, null=True)
    created_at = models.DateTimeField(default=None, null=True)
