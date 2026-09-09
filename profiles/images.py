"""Pillow renderers for the shareable profile card and chat transcript images."""
import io
import re
import textwrap

from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

INDIGO = (79, 70, 229)
INDIGO_LIGHT = (199, 210, 254)
GREEN = (22, 163, 74)
SLATE_900 = (15, 23, 42)
SLATE_500 = (100, 116, 139)
SLATE_200 = (226, 232, 240)
SLATE_50 = (248, 250, 252)
WHITE = (255, 255, 255)

_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def _font(size, bold=False):
    """TrueType when the system has one; Pillow ≥ 10.1 falls back to its own
    scalable font, so rendering works in slim containers and on Windows."""
    for path in (_BOLD_CANDIDATES if bold else _REGULAR_CANDIDATES):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def profile_filename(profile, suffix="") -> str:
    """Download name: <ProfileNumber>_<CustomerName>[<suffix>].png"""
    name = profile.full_name or profile.customer.display_name or "customer"
    base = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "customer"
    number = profile.profile_number or "BP-PENDING"
    return f"{number}_{base}{suffix}.png"


def _fit(draw, text, font, max_w) -> str:
    text = str(text)
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _fmt_date(value) -> str:
    return value.strftime("%d %b %Y") if value else ""


def _card_rows(profile):
    name = profile.full_name or profile.customer.display_name or "—"
    if profile.gcc_residence_card is True:
        card = "Yes" + (f" — expires {_fmt_date(profile.residence_card_expiry)}"
                        if profile.residence_card_expiry else "")
    elif profile.gcc_residence_card is False:
        card = "No"
    else:
        card = "—"
    return [
        [("NAME", name), ("WHATSAPP", profile.whatsapp_number or "—")],
        [("NATIONALITY", profile.nationality or "—"),
         ("RESIDENCE", profile.residence_country or "—")],
        [("GCC RESIDENCE CARD", card),
         ("TRAVEL DATE", _fmt_date(profile.travel_date) or "—")],
        [("TRIP LENGTH", f"{profile.trip_days} days" if profile.trip_days else "—"),
         ("ADULTS", str(profile.adults) if profile.adults else "—")],
        [("CHILDREN AGES", profile.children_ages or "—")],
    ]


def render_profile_card(profile) -> bytes:
    """One-page lead card: brand header, profile number, captured details."""
    width, margin, header_h, row_h, footer_h = 1000, 56, 170, 70, 96
    rows = _card_rows(profile)
    img = Image.new("RGB", (width, header_h + row_h * len(rows) + footer_h), WHITE)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, width, header_h], fill=INDIGO)
    name = profile.full_name or profile.customer.display_name or "Unnamed visitor"
    number = profile.profile_number or "BP-PENDING"
    f_tag, f_name, f_number = _font(22, True), _font(42, True), _font(28, True)
    num_w = draw.textlength(number, font=f_number)
    draw.text((margin, 26), "CUSTOMER PROFILE", font=f_tag, fill=INDIGO_LIGHT)
    draw.text((margin, 62), _fit(draw, name, f_name, width - margin * 2 - num_w - 40),
              font=f_name, fill=WHITE)
    draw.text((width - margin - num_w, 74), number, font=f_number, fill=INDIGO_LIGHT)

    f_label, f_value = _font(19, True), _font(26)
    half_w = width // 2 - margin - 24
    y, col2 = header_h + 20, width // 2 + 10
    for row in rows:
        for i, (label, value) in enumerate(row):
            x = margin if i == 0 else col2
            draw.text((x, y), label, font=f_label, fill=SLATE_500)
            draw.text((x, y + 28), _fit(draw, value, f_value, half_w),
                      font=f_value, fill=SLATE_900)
        y += row_h

    fy = img.height - footer_h
    draw.line([(margin, fy + 12), (width - margin, fy + 12)], fill=SLATE_200, width=2)
    channel = (profile.customer.channel.name if profile.customer.channel_id else "chat")
    f_footer = _font(18)
    draw.text((margin, fy + 30),
              f"Collected via {channel} chat • {timezone.now():%d %b %Y %H:%M} UTC",
              font=f_footer, fill=SLATE_500)
    if profile.is_complete:
        draw.text((margin, fy + 56), "Profile complete ✓", font=f_footer, fill=GREEN)
    else:
        draw.text((margin, fy + 56),
                  f"In progress — missing: {', '.join(profile.missing_fields()[:3])}",
                  font=f_footer, fill=SLATE_500)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_transcript_image(messages, title="Chat transcript") -> bytes:
    """The whole conversation rendered widget-style: indigo customer bubbles on
    the right, white bot/agent bubbles on the left."""
    width, margin = 1080, 44
    bubble_pad, line_h, row_gap, label_h = 18, 32, 28, 26
    f_sender, f_text, f_title = _font(17, True), _font(23), _font(26, True)

    prepared = []
    for m in messages:
        lines = []
        for para in (m.content or "").splitlines() or [""]:
            lines.extend(textwrap.wrap(para, 46) or [""])
        prepared.append((m.get_sender_type_display(), lines))

    body_h = sum(label_h + len(lines) * line_h + bubble_pad * 2 + row_gap
                 for _, lines in prepared)
    img = Image.new("RGB", (width, max(64 + margin * 2 + body_h, 400)), SLATE_50)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, 64], fill=INDIGO)
    draw.text((margin, 18), _fit(draw, title, f_title, width - margin * 2),
              font=f_title, fill=WHITE)

    y = 64 + margin
    for who, lines in prepared:
        is_customer = who == "Customer"
        bubble_w = min(
            width - margin * 2,
            int(max(draw.textlength(line, font=f_text) for line in lines))
            + bubble_pad * 2,
        )
        bubble_h = len(lines) * line_h + bubble_pad * 2 - 6
        x1 = width - margin - bubble_w if is_customer else margin
        draw.text((x1, y), who.upper(), font=f_sender, fill=SLATE_500)
        y += label_h
        draw.rounded_rectangle(
            [x1, y, x1 + bubble_w, y + bubble_h], radius=16,
            fill=INDIGO if is_customer else WHITE,
            outline=None if is_customer else SLATE_200, width=2,
        )
        ty = y + bubble_pad - 6
        for line in lines:
            draw.text((x1 + bubble_pad, ty), line, font=f_text,
                      fill=WHITE if is_customer else SLATE_900)
            ty += line_h
        y += bubble_h + row_gap

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()