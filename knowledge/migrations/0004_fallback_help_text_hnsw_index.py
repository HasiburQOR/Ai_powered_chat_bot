"""Refreshed fallback_message help_text + a pgvector HNSW index.

The HNSW index (pgvector >= 0.5, cosine ops — matching the <=> operator the
retriever uses) turns nearest-neighbour lookup on KnowledgeChunk.embedding
from a full-table scan into logarithmic search, which matters once the
knowledge base grows past a few hundred chunks.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0003_alter_botsettings_profile_collection_enabled_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='botsettings',
            name='fallback_message',
            field=models.TextField(help_text='Sent only when the LLM could not answer even after retries (provider outage, timeout...). Never word it as "I\'m not sure / let me get a team member" — that reads as the bot being oblivious and kills travel leads. Leave the default; it invites the visitor to re-send and keeps gathering trip details.'),
        ),
        migrations.RunSQL(
            sql='CREATE INDEX IF NOT EXISTS knowledgechunk_embedding_hnsw '
                'ON knowledge_knowledgechunk USING hnsw (embedding vector_cosine_ops);',
            reverse_sql='DROP INDEX IF EXISTS knowledgechunk_embedding_hnsw;',
        ),
    ]
