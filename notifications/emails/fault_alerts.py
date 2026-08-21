# notifications/emails/fault_alerts.py
"""
Email templates for the Raven Realtime Fault Alert System.

Kept separate from notifications/tasks.py (which only handles generic
in-app-notification-to-email delivery) since these are purpose-built,
richer branded templates for a specific real-time alert feature — not
routed through the generic Notification model's email path.

Two templates:
  - render_fault_occurred_email()  — sent once, the moment a watched
    feeder transitions from clear to faulted.
  - render_fault_restored_email()  — sent once, the moment a watched
    feeder transitions from faulted back to clear.

Load Shedding is deliberately NOT worded as a fault (it's a planned grid
action, not a failure) — see LOAD_SHEDDING_CATEGORY below.
"""
from django.conf import settings

# Must exactly match the category string categorize_fault_code() returns
# for LS/L-S/LOADSHED-style codes — see tmo/sheet_sync.py FAULT_CODE_CATEGORIES.
LOAD_SHEDDING_CATEGORY = 'Load Shedding'

_KEDCO_LOGO_URL = f"{settings.BASE_URL.rstrip('/')}/static/reports/images/kedco_logo.png"
_FOOTER_LOGO_URL = f"{settings.BASE_URL.rstrip('/')}/static/reports/images/footer_logo.png"

_NAVY = '#001634'
_GOLD = '#D9A400'
_RED = '#B3261E'
_GREEN = '#1E7B3C'
_BLUE = '#1565C0'


def _status_badge(label: str, color: str) -> str:
    """Text-based status pill — no emoji, matches the report system's design language."""
    return (
        f'<span style="display:inline-block;background:{color};color:#ffffff;'
        f'font-size:12px;font-weight:700;letter-spacing:0.5px;padding:5px 14px;'
        f'border-radius:999px;text-transform:uppercase;">{label}</span>'
    )


def _info_row(label: str, value: str) -> str:
    return f"""
    <tr>
      <td style="padding:8px 0;border-bottom:1px solid #eef0f2;color:#6b7280;
                 font-size:13px;width:140px;">{label}</td>
      <td style="padding:8px 0;border-bottom:1px solid #eef0f2;color:#111827;
                 font-size:14px;font-weight:600;">{value}</td>
    </tr>
    """


def _base_wrapper(badge_html: str, heading: str, rows_html: str, footnote: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <style>
        @import url('https://fonts.googleapis.com/css2?family=Figtree:wght@400;600;700&display=swap');
      </style>
    </head>
    <body style="margin:0;padding:0;background:#f4f6f8;font-family:'Figtree',Arial,Helvetica,sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:32px 0;">
        <tr><td align="center">
          <table width="560" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border-radius:8px;overflow:hidden;
                        box-shadow:0 2px 8px rgba(0,0,0,0.08);">

            <!-- Header: KEDCO logo on navy -->
            <tr>
              <td style="background:{_NAVY};padding:20px 32px;">
                <img src="{_KEDCO_LOGO_URL}" alt="KEDCO" height="36" style="display:block;" />
              </td>
            </tr>

            <!-- Status strip -->
            <tr>
              <td style="padding:24px 32px 0;">
                {badge_html}
                <h2 style="margin:14px 0 0;color:#111827;font-size:19px;">{heading}</h2>
              </td>
            </tr>

            <!-- Details table -->
            <tr>
              <td style="padding:20px 32px 28px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                  {rows_html}
                </table>
              </td>
            </tr>

            <!-- Footer: Raven / EMRC logo -->
            <tr>
              <td style="background:#f9fafb;padding:18px 32px;border-top:1px solid #e5e7eb;">
                <img src="{_FOOTER_LOGO_URL}" alt="Powered by EMRC" height="22" style="display:block;margin-bottom:8px;" />
                <p style="margin:0;color:#9ca3af;font-size:11px;">{footnote}</p>
              </td>
            </tr>
          </table>
        </td></tr>
      </table>
    </body>
    </html>
    """


def _display_fault_type(fault_category: str, raw_fault_code: str) -> str:
    """
    When the code doesn't match any known category, show the actual raw
    code from the sheet instead of a vague 'Other/Unspecified' label — it's
    still useful information to a technical reader even if Raven can't
    name it. Safe to show as-is: anything reaching HourlyLoad.fault_code
    has already passed _repair_malformed_decimal() at sync time, which
    only lets genuine non-numeric text through — a stray/garbled number
    never makes it this far, so there's nothing "dirty" to filter here.
    Only falls back to a plain 'Unspecified' if the raw code is itself
    missing (shouldn't happen for a real fault, but stay defensive).
    """
    if fault_category and fault_category != 'Other/Unspecified':
        return fault_category
    raw = (raw_fault_code or '').strip()
    return raw if raw else 'Unspecified'


def render_fault_occurred_email(feeder, segment: str, fault_category: str, raw_fault_code: str, detected_at_str: str) -> dict:
    """
    feeder: a common.models.Feeder instance
    segment: 'MDI' | 'MDNI' | 'Regions'
    fault_category: output of categorize_fault_code(), e.g. 'Earth Fault', 'Load Shedding',
                     or 'Other/Unspecified' when the code isn't recognised
    raw_fault_code: the raw HourlyLoad.fault_code text — shown instead of a
                     vague label when fault_category is 'Other/Unspecified'
    detected_at_str: pre-formatted display string, e.g. "20 Aug 2026, 1:14 PM"

    Returns {'subject': str, 'html': str}. Load Shedding is worded as a
    planned event, not a fault — different subject and badge, same layout.
    """
    band = getattr(feeder.band, 'name', '—') if getattr(feeder, 'band', None) else '—'
    fault_type_display = _display_fault_type(fault_category, raw_fault_code)

    if fault_category == LOAD_SHEDDING_CATEGORY:
        subject = f"Feeder on Load Shedding — {feeder.name} ({segment})"
        badge = _status_badge('Load Shedding', _BLUE)
        heading = f"{feeder.name} is currently on load shedding"
    else:
        subject = f"Fault Alert — {feeder.name} ({segment})"
        badge = _status_badge('Fault', _RED)
        heading = f"{feeder.name} has gone out on fault"

    rows = (
        _info_row('Segment', segment)
        + _info_row('Band', band)
        + _info_row('Feeder', feeder.name)
        + _info_row('Voltage', feeder.voltage_level.upper() if feeder.voltage_level else '—')
        + _info_row(
            'Load Shedding' if fault_category == LOAD_SHEDDING_CATEGORY else 'Fault Type',
            fault_type_display,
          )
        + _info_row('Detected', detected_at_str)
    )
    html = _base_wrapper(badge, heading, rows, 'Raven Realtime Fault Alert System — automated, do not reply.')
    return {'subject': subject, 'html': html}


def render_fault_restored_email(feeder, segment: str, duration_hours: float, restored_at_str: str) -> dict:
    """Sent once, the moment a watched feeder transitions back to clear."""
    band = getattr(feeder.band, 'name', '—') if getattr(feeder, 'band', None) else '—'
    subject = f"Feeder Restored — {feeder.name} ({segment})"
    badge = _status_badge('Restored', _GREEN)
    heading = f"{feeder.name} is back online"

    rows = (
        _info_row('Segment', segment)
        + _info_row('Band', band)
        + _info_row('Feeder', feeder.name)
        + _info_row('Voltage', feeder.voltage_level.upper() if feeder.voltage_level else '—')
        + _info_row('Down for', f"{duration_hours:.1f} hrs")
        + _info_row('Restored', restored_at_str)
    )
    html = _base_wrapper(badge, heading, rows, 'Raven Realtime Fault Alert System — automated, do not reply.')
    return {'subject': subject, 'html': html}
