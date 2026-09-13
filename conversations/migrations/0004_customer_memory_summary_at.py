from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conversations', '0003_alter_customer_email_alter_customer_phone'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='memory_summary_at',
            field=models.DateTimeField(blank=True, help_text='When memory_summary was last regenerated; lets the idle-summarizer beat task skip customers whose summary is already current', null=True),
        ),
    ]
