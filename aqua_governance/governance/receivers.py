from django.db.models.signals import post_save
from django.dispatch import receiver

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.tasks import task_update_proposal_status


@receiver(post_save, sender=Proposal)
def save_final_result(sender, instance, created, **kwargs):
    if instance.voting_time_tracker.has_changed('end_at'):
        task_update_proposal_status.apply_async((instance.id,), eta=instance.end_at)
