import os

from django.conf import settings

from celery import Celery
from celery.schedules import crontab


if not settings.configured:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

app = Celery('aqua_governance')

app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
app.conf.timezone = 'UTC'


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    app.conf.beat_schedule.update({
        'aqua_governance.governance.tasks.task_update_active_proposals': {
            'task': 'aqua_governance.governance.tasks.task_update_active_proposals',
            'schedule': crontab(minute='*/5'),
            'args': (),
        },
        'aqua_governance.governance.tasks.task_update_hidden_ice_votes_in_voted_proposals': {
            'task': 'aqua_governance.governance.tasks.task_update_hidden_ice_votes_in_voted_proposals',
            'schedule': crontab(minute='*/5'),
            'args': (),
        },
        'aqua_governance.governance.tasks.task_check_expired_proposals': {
            'task': 'aqua_governance.governance.tasks.task_check_expired_proposals',
            'schedule': crontab(minute='0', hour='*/24'),
            'args': (),
        },
        'aqua_governance.governance.tasks.check_proposals_with_bad_horizon_error': {
            'task': 'aqua_governance.governance.tasks.check_proposals_with_bad_horizon_error',
            'schedule': crontab(minute='*/10'),
            'args': (),
        },
    })
