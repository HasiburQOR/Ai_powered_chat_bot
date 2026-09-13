from django.db import migrations, models


class Migration(migrations.Migration):
    """created_at loses auto_now_add: Message.save() now stamps it with a
    per-conversation strictly-increasing guarantee (see the model). No data
    change - existing rows keep their stored timestamps."""

    dependencies = [
        ('conversations', '0004_customer_memory_summary_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='created_at',
            field=models.DateTimeField(editable=False),
        ),
    ]
