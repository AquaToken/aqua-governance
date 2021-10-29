import os
from datetime import timedelta

from django.conf import settings

from celery import Celery


if not settings.configured:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

app = Celery('aqua_governance')

app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
app.conf.timezone = 'UTC'


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    from drf_secure_token.tasks import DELETE_OLD_TOKENS

    app.conf.beat_schedule.update({
        'aqua_governance.testapp.tasks.test_task': {
            'task': 'aqua_governance.testapp.tasks.test_task',
            'run_every': timedelta(seconds=10),
            'args': (),
        },
        'drf_secure_token.tasks.delete_old_tokens': DELETE_OLD_TOKENS,
    })
