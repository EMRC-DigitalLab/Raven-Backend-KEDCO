from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('technical', '0010_datasynclog'),
    ]

    operations = [
        migrations.AddField(
            model_name='datasynclog',
            name='records_deleted',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
