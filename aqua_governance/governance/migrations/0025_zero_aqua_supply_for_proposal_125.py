from django.db import migrations


def zero_aqua_supply_for_proposal_125(apps, schema_editor):
    Proposal = apps.get_model('governance', 'Proposal')
    Proposal.objects.filter(id=125).update(aqua_circulating_supply=0)


class Migration(migrations.Migration):

    dependencies = [
        ('governance', '0024_auto_20260127_1152'),
    ]

    operations = [
        migrations.RunPython(zero_aqua_supply_for_proposal_125, reverse_code=migrations.RunPython.noop),
    ]
