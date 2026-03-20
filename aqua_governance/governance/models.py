import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

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

    PROPOSAL_TYPE_GENERAL = 'GENERAL'
    PROPOSAL_TYPE_ADD_ASSET = 'ADD_ASSET'
    PROPOSAL_TYPE_REMOVE_ASSET = 'REMOVE_ASSET'
    PROPOSAL_TYPE_CHOICES = (
        (PROPOSAL_TYPE_GENERAL, 'General proposal'),
        (PROPOSAL_TYPE_ADD_ASSET, 'Add asset proposal'),
        (PROPOSAL_TYPE_REMOVE_ASSET, 'Remove asset proposal'),
    )
    ASSET_PROPOSAL_TYPES = (
        PROPOSAL_TYPE_ADD_ASSET,
        PROPOSAL_TYPE_REMOVE_ASSET,
    )
    EXECUTION_SOURCE_FIELDS = (
        'proposal_type',
        'asset_code',
        'asset_issuer',
        'asset_contract_address',
        'asset_issuer_information',
        'asset_token_description',
        'asset_holder_distribution',
        'asset_liquidity',
        'asset_trading_volume',
        'asset_audit_info',
        'asset_stellar_flags',
        'asset_related_projects',
        'asset_community_references',
        'asset_aquarius_traction',
        'asset_issuer_commitments',
    )

    ONCHAIN_ACTION_NONE = 'NONE'
    ONCHAIN_ACTION_ADD_ASSET = PROPOSAL_TYPE_ADD_ASSET
    ONCHAIN_ACTION_REMOVE_ASSET = PROPOSAL_TYPE_REMOVE_ASSET
    ONCHAIN_ACTION_CHOICES = (
        (ONCHAIN_ACTION_NONE, 'No onchain action'),
        (ONCHAIN_ACTION_ADD_ASSET, 'Add asset'),
        (ONCHAIN_ACTION_REMOVE_ASSET, 'Remove asset'),
    )

    ONCHAIN_EXECUTION_NOT_REQUIRED = 'NOT_REQUIRED'
    ONCHAIN_EXECUTION_PENDING = 'PENDING'
    ONCHAIN_EXECUTION_IN_PROGRESS = 'IN_PROGRESS'
    ONCHAIN_EXECUTION_SUBMITTED = 'SUBMITTED'
    ONCHAIN_EXECUTION_SUCCESS = 'SUCCESS'
    ONCHAIN_EXECUTION_FAILED = 'FAILED'
    ONCHAIN_EXECUTION_REQUIRES_REVIEW = 'REQUIRES_REVIEW'
    ONCHAIN_EXECUTION_SKIPPED = 'SKIPPED'
    ONCHAIN_EXECUTION_STATUS_CHOICES = (
        (ONCHAIN_EXECUTION_NOT_REQUIRED, 'No execution required'),
        (ONCHAIN_EXECUTION_PENDING, 'Pending execution'),
        (ONCHAIN_EXECUTION_IN_PROGRESS, 'Execution in progress'),
        (ONCHAIN_EXECUTION_SUBMITTED, 'Transaction submitted'),
        (ONCHAIN_EXECUTION_SUCCESS, 'Execution succeeded'),
        (ONCHAIN_EXECUTION_FAILED, 'Execution failed'),
        (ONCHAIN_EXECUTION_REQUIRES_REVIEW, 'Execution requires review'),
        (ONCHAIN_EXECUTION_SKIPPED, 'Execution skipped'),
    )

    proposed_by = models.CharField(max_length=56)
    title = models.CharField(max_length=256)
    text = QuillField()
    version = models.PositiveSmallIntegerField(default=1)

    vote_for_issuer = models.CharField(max_length=56)
    vote_against_issuer = models.CharField(max_length=56)
    abstain_issuer = models.CharField(max_length=56, null=True)

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
    vote_abstain_result = models.DecimalField(decimal_places=7, max_digits=20, default=0, blank=True, null=True)

    transaction_hash = models.CharField(max_length=64, unique=True, null=True)
    envelope_xdr = models.TextField(null=True, blank=True)

    aqua_circulating_supply = models.DecimalField(decimal_places=7, max_digits=20, default=0, blank=True)
    ice_circulating_supply = models.DecimalField(decimal_places=7, max_digits=20, default=0, blank=True)
    percent_for_quorum = models.PositiveSmallIntegerField(blank=True, default=10)

    discord_channel_url = models.URLField(blank=True, null=True, default=settings.DEFAULT_DISCORD_URL)
    discord_channel_name = models.CharField(max_length=64, blank=True, null=True)
    discord_username = models.CharField(max_length=64, blank=True, null=True)

    new_title = models.CharField(max_length=256, null=True)
    new_text = QuillField(null=True)
    new_transaction_hash = models.CharField(max_length=64, unique=True, null=True)
    new_envelope_xdr = models.TextField(null=True, blank=True)
    new_start_at = models.DateTimeField(null=True, blank=True)
    new_end_at = models.DateTimeField(null=True, blank=True)

    proposal_type = models.CharField(
        choices=PROPOSAL_TYPE_CHOICES,
        max_length=32,
        default=PROPOSAL_TYPE_GENERAL,
        db_index=True,
    )
    action = models.CharField(choices=PROPOSAL_ACTION_CHOICES, max_length=64, default=NONE)

    # Asset proposal payload (section 5). Mandatory only for proposal_type=ASSET.
    asset_code = models.CharField(max_length=64, null=True, blank=True)
    asset_issuer = models.CharField(max_length=56, null=True, blank=True)
    asset_contract_address = models.CharField(max_length=128, null=True, blank=True)
    asset_issuer_information = models.TextField(null=True, blank=True)
    asset_token_description = models.TextField(null=True, blank=True)
    asset_holder_distribution = models.TextField(null=True, blank=True)
    asset_liquidity = models.TextField(null=True, blank=True)
    asset_trading_volume = models.TextField(null=True, blank=True)
    asset_audit_info = models.TextField(null=True, blank=True)
    asset_stellar_flags = models.TextField(null=True, blank=True)
    asset_related_projects = models.TextField(null=True, blank=True)
    asset_community_references = models.TextField(null=True, blank=True)
    asset_aquarius_traction = models.TextField(null=True, blank=True)
    asset_issuer_commitments = models.TextField(null=True, blank=True)
    onchain_execution_status = models.CharField(
        choices=ONCHAIN_EXECUTION_STATUS_CHOICES,
        max_length=32,
        default=ONCHAIN_EXECUTION_NOT_REQUIRED,
    )
    onchain_execution_tx_hash = models.CharField(max_length=128, null=True, blank=True)
    onchain_execution_started_at = models.DateTimeField(null=True, blank=True)
    onchain_execution_submitted_at = models.DateTimeField(null=True, blank=True)
    onchain_execution_poll_count = models.PositiveIntegerField(default=0)

    voting_time_tracker = FieldTracker(fields=['end_at'])

    def __str__(self):
        return str(self.id)

    @classmethod
    def is_asset_proposal_type(cls, proposal_type: str) -> bool:
        return proposal_type in cls.ASSET_PROPOSAL_TYPES

    @property
    def is_asset_proposal(self) -> bool:
        return self.is_asset_proposal_type(self.proposal_type)

    @classmethod
    def has_active_asset_proposal_conflict(cls, current_proposal_id=None) -> bool:
        queryset = cls.objects.filter(
            proposal_type__in=cls.ASSET_PROPOSAL_TYPES,
            hide=False,
            draft=False,
        )
        if current_proposal_id is not None:
            queryset = queryset.exclude(id=current_proposal_id)
        return queryset.filter(
            models.Q(proposal_status__in=(cls.DISCUSSION, cls.VOTING)) | models.Q(action=cls.TO_SUBMIT),
        ).exists()

    @property
    def onchain_action_type(self) -> str:
        if self.proposal_type == self.PROPOSAL_TYPE_ADD_ASSET:
            return self.ONCHAIN_ACTION_ADD_ASSET
        if self.proposal_type == self.PROPOSAL_TYPE_REMOVE_ASSET:
            return self.ONCHAIN_ACTION_REMOVE_ASSET
        return self.ONCHAIN_ACTION_NONE

    @property
    def onchain_action_args(self) -> list[str]:
        if not self.is_asset_proposal:
            return []

        from aqua_governance.governance.onchain_hooks.validators import derive_onchain_action_args

        return derive_onchain_action_args(
            asset_code=self.asset_code,
            asset_issuer=self.asset_issuer,
            asset_contract_address=self.asset_contract_address,
        )

    def check_transaction(self):
        from aqua_governance.utils.payments import check_proposal_status

        if self.action == self.TO_UPDATE:
            status = check_proposal_status(self.new_transaction_hash, self.new_text.html,
                                           settings.PROPOSAL_CREATE_OR_UPDATE_COST)
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
                self.last_updated_at = timezone.now()
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
                self.last_updated_at = timezone.now()
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
            status = check_proposal_status(self.transaction_hash, self.text.html,
                                           settings.PROPOSAL_CREATE_OR_UPDATE_COST)
            if not (status == self.HORIZON_ERROR and self.status == self.HORIZON_ERROR):
                if status != self.HORIZON_ERROR:
                    has_asset_conflict = (
                        self.is_asset_proposal
                        and self.has_active_asset_proposal_conflict(current_proposal_id=self.id)
                    )
                    if not has_asset_conflict:
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
        if not self.abstain_issuer:
            keypair = Keypair.random()
            self.abstain_issuer = keypair.public_key

        self._validate_execution_source_fields_immutable(update_fields=update_fields)

        if self.onchain_action_type == self.ONCHAIN_ACTION_NONE:
            if (
                self.onchain_execution_status != self.ONCHAIN_EXECUTION_NOT_REQUIRED
                or self.onchain_execution_tx_hash
                or self.onchain_execution_started_at
                or self.onchain_execution_submitted_at
                or self.onchain_execution_poll_count
            ):
                self.onchain_execution_status = self.ONCHAIN_EXECUTION_NOT_REQUIRED
                self.onchain_execution_tx_hash = None
                self.onchain_execution_started_at = None
                self.onchain_execution_submitted_at = None
                self.onchain_execution_poll_count = 0
        elif (
            self.onchain_execution_status == self.ONCHAIN_EXECUTION_NOT_REQUIRED
            and not self.onchain_execution_tx_hash
        ):
            self.onchain_execution_status = self.ONCHAIN_EXECUTION_PENDING
            self.onchain_execution_started_at = None
            self.onchain_execution_submitted_at = None
            self.onchain_execution_poll_count = 0

        if not self.pk:
            # AQUA voting is deprecated: keep denominator based on ICE only for new proposals.
            self.aqua_circulating_supply = 0
            response = requests.get(settings.ICE_CIRCULATING_URL)
            if response.status_code == 200:
                self.ice_circulating_supply = float(response.json()['ice_supply_amount'])

        super(Proposal, self).save(force_insert, force_update, using, update_fields)

    def _validate_execution_source_fields_immutable(self, update_fields=None):
        if not self.pk:
            return

        fields_to_check = set(self.EXECUTION_SOURCE_FIELDS)
        if update_fields is not None:
            fields_to_check &= set(update_fields)
            if not fields_to_check:
                return

        persisted = type(self).objects.only(*fields_to_check).get(pk=self.pk)
        changed_fields = [
            field_name
            for field_name in fields_to_check
            if getattr(self, field_name) != getattr(persisted, field_name)
        ]
        if changed_fields:
            raise ValidationError({
                field_name: 'Execution source fields are immutable after proposal creation.'
                for field_name in changed_fields
            })


class LogVote(models.Model):
    VOTE_FOR = 'vote_for'
    VOTE_AGAINST = 'vote_against'
    VOTE_ABSTAIN = 'vote_abstain'
    VOTE_TYPES = (
        (VOTE_FOR, 'Vote For'),
        (VOTE_AGAINST, 'Vote Against'),
        (VOTE_ABSTAIN, 'Vote Abstain'),
    )
    ASSET_TYPES = (
        (settings.AQUA_ASSET_CODE, settings.AQUA_ASSET_CODE),
        (settings.GOVERNANCE_ICE_ASSET_CODE, settings.GOVERNANCE_ICE_ASSET_CODE),
        (settings.GDICE_ASSET_CODE, settings.GDICE_ASSET_CODE),
    )

    claimable_balance_id = models.CharField(max_length=72, null=True)
    transaction_link = models.URLField(null=True)
    account_issuer = models.CharField(max_length=56, null=True)
    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, null=True)
    vote_choice = models.CharField(max_length=15, choices=VOTE_TYPES, default=None, null=True)
    asset_code = models.CharField(max_length=15, choices=ASSET_TYPES, default=settings.AQUA_ASSET_CODE)
    created_at = models.DateTimeField(default=None, null=True)
    key = models.CharField(
        max_length=170,
        null=True,
        help_text=(
            ""
        )
    )
    group_index = models.IntegerField(
        default=0,
        help_text=(
            ""
        )
    )
    amount = models.DecimalField(
        decimal_places=7,
        max_digits=20,
        blank=True,
        null=True,
        help_text=(
            ""
        )
    )
    original_amount = models.DecimalField(
        decimal_places=7,
        max_digits=20,
        blank=True,
        null=True,
        help_text=(
            ""
        )
    )
    voted_amount = models.DecimalField(
        decimal_places=7,
        max_digits=20,
        blank=True,
        null=True,
        help_text=(
            ""
        )
    )
    claimed = models.BooleanField(
        default=False,
        help_text=(
            ""
        )
    )
    hide = models.BooleanField(
        default=False,
        help_text=(
            "System managed soft exclusion for this vote. Use cases: votes parsed after proposal end; "
            "duplicates reingested with the same claimable_balance_id; votes invalidated after rule changes or failed "
            "reverification; spam or abuse detection; temporary suppression during reprocessing. When True, the vote "
            "remains stored but is excluded from public endpoints and all counts or quorum. Automatically set by "
            "parser or background tasks; not edited manually. Part of the composite uniqueness with claimable_balance_id "
            "to allow a hidden shadow row without conflicts."
        ),
    )

    class Meta:
        unique_together = [['hide', 'claimable_balance_id']]

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
