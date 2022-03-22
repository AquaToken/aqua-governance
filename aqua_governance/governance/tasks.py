import logging
import sys
from datetime import datetime, timedelta
from typing import Optional

import requests
from django.conf import settings

from stellar_sdk import Asset, Server

from aqua_governance.governance.exceptions import ClaimableBalanceParsingError
from aqua_governance.governance.models import LogVote, Proposal
from aqua_governance.governance.parser import parse_balance_info
from aqua_governance.taskapp import app as celery_app
from aqua_governance.utils.requests import load_all_records
from aqua_governance.utils.signals import DisableSignals


logger = logging.getLogger()


AQUA_ASSET_CODE = 'AQUA'
AQUA_ASSET_ISSUER = 'GBNZILSTVQZ4R7IKQDGHYGY2QXL5QOFJYQMXPKWRRM5PAV7Y4M67AQUA'


def _parse_claimable_balance(claimable_balance: dict, proposal: Proposal, log_vote: str) -> Optional[LogVote]:
    balance_id = claimable_balance['id']
    if LogVote.objects.filter(claimable_balance_id=balance_id).exists():
        return

    try:
        return parse_balance_info(claimable_balance, proposal, log_vote)
    except ClaimableBalanceParsingError:
        logger.warning('Balance info skipped.', exc_info=sys.exc_info())


def _update_proposal_final_results(proposal_id):
    proposal = Proposal.objects.get(id=proposal_id)
    vote_for_result = sum(proposal.logvote_set.filter(vote_choice=LogVote.VOTE_FOR).values_list('amount', flat=True))
    vote_against_result = sum(
        proposal.logvote_set.filter(vote_choice=LogVote.VOTE_AGAINST).values_list('amount', flat=True),
    )
    proposal.vote_for_result = vote_for_result
    proposal.vote_against_result = vote_against_result

    response = requests.get(settings.AQUA_CIRCULATING_URL)
    if response.status_code == 200:
        proposal.aqua_circulating_supply = response.json()

    with DisableSignals('aqua_governance.governance.receivers.save_final_result', sender=Proposal):
        proposal.save(update_fields=['vote_for_result', 'vote_against_result', 'aqua_circulating_supply'])


@celery_app.task(ignore_result=True)
def task_update_proposal_result(proposal_id):
    horizon_server = Server(settings.HORIZON_URL)
    proposal = Proposal.objects.get(id=proposal_id)
    new_log_vote_list = []

    request_builders = (
        (
            horizon_server.claimable_balances().for_claimant(proposal.vote_for_issuer).for_asset(
                Asset(AQUA_ASSET_CODE, AQUA_ASSET_ISSUER),
            ).order(desc=False),
            LogVote.VOTE_FOR,
        ),
        (
            horizon_server.claimable_balances().for_claimant(proposal.vote_against_issuer).for_asset(
                Asset(AQUA_ASSET_CODE, AQUA_ASSET_ISSUER),
            ).order(desc=False),
            LogVote.VOTE_AGAINST,
        ),
    )

    for request_builder in request_builders:
        for balance in load_all_records(request_builder[0]):
            claimable_balance = _parse_claimable_balance(balance, proposal, request_builder[1])
            if claimable_balance:
                new_log_vote_list.append(claimable_balance)

    LogVote.objects.bulk_create(new_log_vote_list)
    _update_proposal_final_results(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_proposal_status(proposal_id):
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.end_at <= datetime.now() + timedelta(seconds=5) and proposal.proposal_status == Proposal.VOTING:
        proposal.proposal_status = Proposal.VOTED
        proposal.save()
        task_update_proposal_result.delay(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_active_proposals():
    now = datetime.now()
    active_proposals = Proposal.objects.filter(start_at__lte=now, end_at__gte=now)

    for proposal in active_proposals:
        task_update_proposal_result.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    expired_period = datetime.now() - settings.EXPIRED_TIMEDELTA
    proposals = Proposal.objects.filter(proposal_status=Proposal.DISCUSSION, last_updated__lte=expired_period)
    proposals.update(proposal_status=Proposal.EXPIRED, hide=True)
