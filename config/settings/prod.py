import sentry_sdk
from kombu import Exchange, Queue  # NOQA
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from config.settings.base import *  # noqa: F403


environ.Env.read_env()


DEBUG = False

ADMINS = env.json('ADMINS')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

SECRET_KEY = env('SECRET_KEY')


# Database
# https://docs.djangoproject.com/en/1.9/ref/settings/#databases
# --------------------------------------------------------------------------

DATABASES = {
    'default': env.db(),
}


# Template
# --------------------------------------------------------------------------

TEMPLATES[0]['OPTIONS']['loaders'] = [
    ('django.template.loaders.cached.Loader', [
        'django.template.loaders.filesystem.Loader',
        'django.template.loaders.app_directories.Loader',
    ]),
]


# Storage configurations
# --------------------------------------------------------------------------

AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME')
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY')
AWS_AUTO_CREATE_BUCKET = True

AWS_QUERYSTRING_AUTH = False

AWS_S3_CUSTOM_DOMAIN = '{0}.s3.amazonaws.com'.format(AWS_STORAGE_BUCKET_NAME)

STATIC_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/static/'
MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/media/'

DEFAULT_FILE_STORAGE = 'config.settings.s3utils.MediaRootS3BotoStorage'
STATICFILES_STORAGE = 'config.settings.s3utils.StaticRootS3BotoStorage'


# Email settings
# --------------------------------------------------------------------------

EMAIL_CONFIG = env.email()
vars().update(EMAIL_CONFIG)

SERVER_EMAIL_SIGNATURE = env('SERVER_EMAIL_SIGNATURE', default='aqua_governance'.capitalize())
DEFAULT_FROM_EMAIL = SERVER_EMAIL = SERVER_EMAIL_SIGNATURE + ' <{0}>'.format(env('SERVER_EMAIL'))


# Celery configurations
# http://docs.celeryproject.org/en/latest/configuration.html
# --------------------------------------------------------------------------

if CELERY_ENABLED:
    CELERY_BROKER_URL = env('CELERY_BROKER_URL')

    CELERY_TASK_DEFAULT_QUEUE = 'aqua_governance-celery-queue'
    CELERY_TASK_DEFAULT_EXCHANGE = 'aqua_governance-exchange'
    CELERY_TASK_DEFAULT_ROUTING_KEY = 'celery.aqua_governance'
    CELERY_TASK_QUEUES = (
        Queue(
            CELERY_TASK_DEFAULT_QUEUE,
            Exchange(CELERY_TASK_DEFAULT_EXCHANGE),
            routing_key=CELERY_TASK_DEFAULT_ROUTING_KEY,
        ),
    )


# Sentry config
# -------------

SENTRY_DSN = env('SENTRY_DSN', default='')
SENTRY_ENABLED = True if SENTRY_DSN else False

if SENTRY_ENABLED:
    sentry_sdk.init(
        SENTRY_DSN,
        traces_sample_rate=0.2,
        integrations=[DjangoIntegration(), CeleryIntegration()],
    )
