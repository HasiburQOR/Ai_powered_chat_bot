"""Backfill embeddings for knowledge chunks that are missing them."""
from django.core.management.base import BaseCommand

from knowledge.embeddings import embed_text
from knowledge.models import KnowledgeChunk


class Command(BaseCommand):
    help = (
        "Embed active knowledge chunks that have no embedding — e.g. chunks "
        "saved while the Celery worker was down (the post_save signal only "
        "ENQUEUES the embedding task, so an unavailable worker means the "
        "vector is silently never generated and RAG retrieval skips the "
        "chunk). Runs the model inline, no broker needed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true",
            help="Re-embed every active chunk, not only the ones missing an embedding.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be embedded without writing anything.",
        )

    def handle(self, *args, **options):
        chunks = KnowledgeChunk.objects.filter(is_active=True)
        if not options["all"]:
            chunks = chunks.filter(embedding__isnull=True)

        total = chunks.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to do — every active chunk already has an embedding. "
                "Use --all to re-embed everything (e.g. after a model change)."))
            return

        self.stdout.write(f"Embedding {total} chunk(s)...")
        embedded = failed = 0
        for chunk in chunks:
            vector = embed_text(f"{chunk.title}\n{chunk.content}")
            if vector is None:
                failed += 1
                self.stderr.write(self.style.WARNING(
                    f"  ! '{chunk.title}': embedding model unavailable "
                    "(is sentence-transformers installed?)"))
                continue
            if not options["dry_run"]:
                # update() on purpose: a plain save() would fire the post_save
                # signal and enqueue the very same embedding task again.
                KnowledgeChunk.objects.filter(pk=chunk.pk).update(embedding=vector)
            embedded += 1
            self.stdout.write(f"  + {chunk.title}")

        summary = f"Done: {embedded}/{total} embedded"
        if options["dry_run"]:
            summary += " (dry run — nothing written)"
        if failed:
            self.stderr.write(self.style.ERROR(f"{summary}, {failed} failed."))
        else:
            self.stdout.write(self.style.SUCCESS(summary + "."))
