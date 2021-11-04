from django.db.models.signals import post_save
from django.dispatch import receiver

from aqua_governance.governance.models import Proposal
from aqua_governance.governance.tasks import task_update_proposal_result


@receiver(post_save, sender=Proposal)
def save_final_result(instance, created, **kwargs):
    if created:
        task_update_proposal_result.apply_async((instance.id,), eta=instance.end_at)
