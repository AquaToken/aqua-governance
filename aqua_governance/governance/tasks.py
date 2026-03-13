from datetime import datetime, timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone
from stellar_sdk import Server

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.task_logic.proposal_finalization import (
    update_proposal_final_results,
)
from aqua_governance.governance.task_logic.vote_indexing import (
    update_proposal_votes_snapshot,
)
from aqua_governance.taskapp import app as celery_app


@celery_app.task(ignore_result=True)
def task_update_proposal_status(proposal_id):
    """
    Update proposal status, votes and results before the end of voting.
    """
    proposal = Proposal.objects.get(id=proposal_id)
    if proposal.end_at <= timezone.now() + timedelta(seconds=5) and proposal.proposal_status == Proposal.VOTING:
        proposal.proposal_status = Proposal.VOTED
        proposal.save()
        task_update_proposal_results.delay(proposal.id, True)


@celery_app.task(ignore_result=True)
def task_update_active_proposals():
    """
    Update active proposals.
    """
    now = datetime.now()
    active_proposals = Proposal.objects.filter(proposal_status=Proposal.VOTING, start_at__lte=now, end_at__gte=now)

    for proposal in active_proposals:
        task_update_proposal_results.delay(proposal.id)


@celery_app.task(ignore_result=True)
def task_check_expired_proposals():
    """
    Check expired proposals.
    """
    expired_period = datetime.now() - settings.EXPIRED_TIME
    proposals = Proposal.objects.filter(proposal_status=Proposal.DISCUSSION, last_updated_at__lte=expired_period)
    proposals.update(proposal_status=Proposal.EXPIRED)


@celery_app.task(ignore_result=True)
def task_update_proposal_results(proposal_id: int, freezing_amount: bool = False):
    task_update_votes(proposal_id, freezing_amount)
    update_proposal_final_results(proposal_id)


@celery_app.task(ignore_result=True)
def task_update_votes(proposal_id: Optional[int] = None, freezing_amount: bool = False):
    """
    Update votes for proposal.
    """
    should_refresh_final_results = proposal_id is None
    if proposal_id is None:
        proposals = Proposal.objects.filter(proposal_status__in=[Proposal.VOTED]).order_by("-id")
    else:
        proposals = Proposal.objects.filter(id=proposal_id)

    horizon_server = Server(settings.HORIZON_URL)

    for proposal in proposals:
        update_proposal_votes_snapshot(
            proposal=proposal,
            horizon_server=horizon_server,
            freezing_amount=freezing_amount,
        )
        if should_refresh_final_results and proposal.proposal_status == Proposal.VOTED:
            update_proposal_final_results(proposal.id)


@celery_app.task(ignore_result=True)
def check_proposals_with_bad_horizon_error():
    failed_proposals = Proposal.objects.filter(hide=False, payment_status=Proposal.HORIZON_ERROR)
    for proposal in failed_proposals:
        proposal.check_transaction()
