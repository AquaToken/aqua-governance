from datetime import datetime

import requests
from django.conf import settings
from django.db import models

from django_quill.fields import QuillField
from model_utils import FieldTracker
from stellar_sdk import Keypair


class Proposal(models.Model):
    HORIZON_ERROR = 'HORIZON_ERROR'
    BAD_MEMO = 'BAD_MEMO'
    INVALID_PAYMENT = 'INVALID_PAYMENT'
    FINE = 'FINE'
    FAILED_TRANSACTION = 'FAILED_TRANSACTION'

    PROPOSAL_STATUS_CHOICES = (
        (HORIZON_ERROR, 'Bad horizon response'),
        (BAD_MEMO, 'Bad transaction memo'),
        (INVALID_PAYMENT, 'Invalid payment'),
        (FINE, 'Fine'),
        (FAILED_TRANSACTION, 'Transaction unsuccessful'),
    )  # TODO: remove it

    DISCUSSION = 'DISCUSSION'
    VOTING = 'VOTING'
    VOTED = 'VOTED'
    EXPIRED = 'EXPIRED'

    NEW_PROPOSAL_STATUS_CHOICES = (
        (DISCUSSION, 'Proposal under discussion'),
        (VOTING, 'Proposal under voting'),
        (VOTED, 'Voted'),
        (EXPIRED, 'Expired'),
    )

    PAYMENT_STATUS_CHOICES = (
        (HORIZON_ERROR, 'Bad horizon response'),
        (BAD_MEMO, 'Bad transaction memo'),
        (INVALID_PAYMENT, 'Invalid payment'),
        (FAILED_TRANSACTION, 'Transaction unsuccessful'),
        (FINE, 'Fine'),
    )

    NONE = 'NONE'
    TO_UPDATE = 'TO_UPDATE'
    TO_SUBMIT = 'TO_SUBMIT'
    TO_CREATE = 'TO_CREATE'

    PROPOSAL_ACTION_CHOICES = (
        (TO_UPDATE, 'To update'),
        (TO_SUBMIT, 'To submit'),
        (TO_CREATE, 'To create'),
        (NONE, 'None'),
    )

    proposed_by = models.CharField(max_length=56)
    title = models.CharField(max_length=256)
    text = QuillField()
    version = models.PositiveSmallIntegerField(default=1)

    vote_for_issuer = models.CharField(max_length=56)
    vote_against_issuer = models.CharField(max_length=56)

    created_at = models.DateTimeField(auto_now_add=True)
    last_updated_at = models.DateTimeField(auto_now_add=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)

    hide = models.BooleanField(default=False)
    is_simple_proposal = models.BooleanField(default=True)  # for future custom voting options
    draft = models.BooleanField(default=False)

    status = models.CharField(choices=PROPOSAL_STATUS_CHOICES, max_length=64, default=FINE)  # TODO: remove
    proposal_status = models.CharField(choices=NEW_PROPOSAL_STATUS_CHOICES, max_length=64, default=DISCUSSION)
    payment_status = models.CharField(choices=PAYMENT_STATUS_CHOICES, max_length=64, default=FINE)

    vote_for_result = models.DecimalField(decimal_places=7, max_digits=20, default=0, blank=True, null=True)
    vote_against_result = models.DecimalField(decimal_places=7, max_digits=20, default=0, blank=True, null=True)

    transaction_hash = models.CharField(max_length=64, unique=True, null=True)
    envelope_xdr = models.TextField(null=True, blank=True)

    aqua_circulating_supply = models.DecimalField(decimal_places=7, max_digits=20, default=0, blank=True)

    discord_channel_url = models.URLField(blank=True, null=True)
    discord_channel_name = models.CharField(max_length=64, blank=True, null=True)
    discord_username = models.CharField(max_length=64, blank=True, null=True)

    new_title = models.CharField(max_length=256, null=True)
    new_text = QuillField(null=True)
    new_transaction_hash = models.CharField(max_length=64, unique=True, null=True)
    new_envelope_xdr = models.TextField(null=True, blank=True)
    new_start_at = models.DateTimeField(null=True, blank=True)
    new_end_at = models.DateTimeField(null=True, blank=True)

    action = models.CharField(choices=PROPOSAL_ACTION_CHOICES, max_length=64, default=NONE)

    voting_time_tracker = FieldTracker(fields=['end_at'])

    def __str__(self):
        return str(self.id)

    def check_transaction(self):
        from aqua_governance.utils.payments import check_proposal_status

        if self.action == self.TO_UPDATE:
            status = check_proposal_status(self.new_transaction_hash, self.new_text.html, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
            if status == self.FINE:
                HistoryProposal.objects.create(
                    version=self.version,
                    title=self.title,
                    text=self.text,
                    transaction_hash=self.transaction_hash,
                    envelope_xdr=self.envelope_xdr,
                    proposal=self,
                    created_at=self.last_updated_at,
                )
                self.payment_status = status
                self.last_updated_at = datetime.now()
                self.text = self.new_text
                self.title = self.new_title
                self.version = self.version + 1
                self.transaction_hash = self.new_transaction_hash
                self.envelope_xdr = self.new_envelope_xdr
                self.action = self.NONE
                self.save()
            else:
                self.payment_status = status
                self.save()

        elif self.action == self.TO_SUBMIT:
            status = check_proposal_status(self.new_transaction_hash, self.text.html, settings.PROPOSAL_SUBMIT_COST)
            if status == self.FINE:
                HistoryProposal.objects.create(
                    version=self.version,
                    hide=True,
                    title=self.title,
                    text=self.text,
                    transaction_hash=self.transaction_hash,
                    envelope_xdr=self.envelope_xdr,
                    proposal=self,
                    created_at=self.last_updated_at,
                )
                self.payment_status = status
                self.proposal_status = self.VOTING
                self.last_updated_at = datetime.now()
                self.start_at = self.new_start_at
                self.end_at = self.new_end_at
                self.transaction_hash = self.new_transaction_hash
                self.envelope_xdr = self.new_envelope_xdr
                self.action = self.NONE
                self.save()
            else:
                self.payment_status = status
                self.save()

        elif self.action == self.TO_CREATE:
            status = check_proposal_status(self.transaction_hash, self.text.html, settings.PROPOSAL_CREATE_OR_UPDATE_COST)
            if not (status == self.HORIZON_ERROR and self.status == self.HORIZON_ERROR):
                if status != self.HORIZON_ERROR:
                    self.draft = False
                    self.action = self.NONE
                    if status != self.FINE:
                        self.hide = True
                self.payment_status = status
                self.save()

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self.vote_against_issuer:
            keypair = Keypair.random()
            self.vote_against_issuer = keypair.public_key
        if not self.vote_for_issuer:
            keypair = Keypair.random()
            self.vote_for_issuer = keypair.public_key

        if not self.pk:
            response = requests.get(settings.AQUA_CIRCULATING_URL)
            if response.status_code == 200:
                self.aqua_circulating_supply = response.json()

        super(Proposal, self).save(force_insert, force_update, using, update_fields)


class LogVote(models.Model):
    VOTE_FOR = 'vote_for'
    VOTE_AGAINST = 'vote_against'
    VOTE_TYPES = (
        (VOTE_FOR, 'Vote For'),
        (VOTE_AGAINST, 'Vote Against'),
    )
    ASSET_TYPES = (
        (settings.AQUA_ASSET_CODE, settings.AQUA_ASSET_CODE),
        (settings.GOVERNANCE_ICE_ASSET_CODE, settings.GOVERNANCE_ICE_ASSET_CODE),
    )

    claimable_balance_id = models.CharField(max_length=72, unique=True, null=True)
    transaction_link = models.URLField(null=True)
    account_issuer = models.CharField(max_length=56, null=True)
    amount = models.DecimalField(decimal_places=7, max_digits=20, blank=True, null=True)
    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, null=True)
    vote_choice = models.CharField(max_length=15, choices=VOTE_TYPES, default=None, null=True)
    created_at = models.DateTimeField(default=None, null=True)
    asset_code = models.CharField(max_length=12, choices=ASSET_TYPES, default=settings.AQUA_ASSET_CODE)

    def __str__(self):
        return str(self.id)


class HistoryProposal(models.Model):
    version = models.PositiveSmallIntegerField()
    hide = models.BooleanField(default=False)

    title = models.CharField(max_length=256)
    text = QuillField()

    created_at = models.DateTimeField()

    transaction_hash = models.CharField(max_length=64, unique=True, null=True)
    envelope_xdr = models.TextField(null=True, blank=True)
    proposal = models.ForeignKey(Proposal, related_name='history_proposal', on_delete=models.CASCADE, null=True)

    def __str__(self):
        return 'History proposal ' + str(self.id)
