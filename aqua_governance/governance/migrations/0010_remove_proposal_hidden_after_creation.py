# Generated by Django 3.2.10 on 2021-12-08 12:10

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('governance', '0009_proposal_hidden_after_creation'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='proposal',
            name='hidden_after_creation',
        ),
    ]
