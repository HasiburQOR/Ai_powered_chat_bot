import uuid

from django.db import models, transaction
from django.utils import timezone

from conversations.models import Customer


class ProfileNumberCounter(models.Model):
    """Single row whose last_number backs the human-friendly BP-000123 codes."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    last_number = models.PositiveIntegerField(default=0)

    @classmethod
    def next_profile_number(cls) -> str:
        """Race-safe increment: row lock inside a transaction guarantees two
        concurrent extractions can never receive the same number."""
        with transaction.atomic():
            counter, _ = cls.objects.select_for_update().get_or_create(id=1)
            counter.last_number += 1
            counter.save(update_fields=["last_number"])
        return f"BP-{counter.last_number:06d}"


class TravelProfile(models.Model):
    """Travel-lead details the bot captures from free-text conversation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(
        Customer, on_delete=models.CASCADE, related_name="travel_profile")
    profile_number = models.CharField(
        max_length=20, unique=True, blank=True,
        help_text="BP-000123; assigned automatically when the first detail is captured.")

    full_name = models.CharField(max_length=120, blank=True)
    whatsapp_number = models.CharField(max_length=32, blank=True)
    nationality = models.CharField(max_length=80, blank=True)
    residence_country = models.CharField(max_length=80, blank=True)
    gcc_residence_card = models.BooleanField(
        null=True, blank=True, help_text="Unknown until the visitor answers.")
    residence_card_expiry = models.DateField(null=True, blank=True)
    travel_date = models.DateField(null=True, blank=True)
    trip_days = models.PositiveIntegerField(null=True, blank=True)
    adults = models.PositiveIntegerField(null=True, blank=True,
                                         help_text="Number of adult travellers.")
    children_ages = models.CharField(
        max_length=120, blank=True, help_text="Comma-separated ages, e.g. '5, 8'.")

    travel_intent_detected = models.BooleanField(
        default=False,
        help_text="Set once the visitor's messages show travel interest (keyword "
                  "match or LLM detection); gates the scripted profile questions.")
    questions_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the scripted profile questions were sent — they are asked only once.")

    is_complete = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    REQUIRED_FIELDS = [
        ("full_name", "Name"),
        ("whatsapp_number", "WhatsApp number"),
        ("nationality", "Nationality"),
        ("residence_country", "Country of residence"),
        ("travel_date", "Travel date"),
        ("trip_days", "Trip length (days)"),
        ("adults", "Number of travellers"),
    ]

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        who = self.full_name or self.customer.display_name or str(self.customer_id)
        return f"{self.profile_number or '(unnumbered)'} — {who}"

    @property
    def total_travellers(self):
        count = self.adults or 0
        if self.children_ages:
            count += len([p for p in self.children_ages.replace(" ", "").split(",") if p])
        return count or None

    def missing_fields(self):
        return [label for name, label in self.REQUIRED_FIELDS
                if getattr(self, name) in (None, "")]

    def known_fields_summary(self):
        """'Label: value' strings for everything captured (LLM context + card)."""
        rows = []
        for name, label in self.REQUIRED_FIELDS:
            value = getattr(self, name)
            if value not in (None, ""):
                rows.append(f"{label}: {value}")
        if self.gcc_residence_card is True and self.residence_card_expiry:
            rows.append(f"GCC card expiry: {self.residence_card_expiry}")
        if self.children_ages:
            rows.append(f"Children ages: {self.children_ages}")
        return rows

    def refresh_completion(self, when=None) -> bool:
        """Update is_complete; stamp completed_at the first time it fills up."""
        was_complete = self.is_complete
        self.is_complete = not self.missing_fields()
        if self.is_complete and not was_complete:
            self.completed_at = when or timezone.now()
        return self.is_complete