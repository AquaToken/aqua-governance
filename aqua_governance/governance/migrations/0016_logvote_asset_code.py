# Generated by Django 3.2.12 on 2022-08-10 11:14

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('governance', '0015_auto_20220511_1702'),
    ]

    operations = [
        migrations.AddField(
            model_name='logvote',
            name='asset_code',
            field=models.CharField(choices=[('AQUA', 'AQUA'), ('governICE', 'governICE')], default='AQUA', max_length=15),
        ),
    ]