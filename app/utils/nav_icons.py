"""
app/utils/nav_icons.py
The ONE place that says which icon stands for which part of the portal. The student sidebar, the admin sidebar, the admin
dashboard cards and every page heading read it (Jinja: {{ nav_icon('exams') }}; Python: ICONS["exams"]), so an icon changed
here changes everywhere and a page can never show a different symbol from its menu entry.

All names are Font Awesome 6 (free, solid) classes, without the leading "fas".
"""

ICONS = {
    # shared by the student and admin portals
    "dashboard": "fa-table-cells-large",
    "categories": "fa-folder-tree",
    "analytics": "fa-chart-column",
    "ai": "fa-wand-magic-sparkles",
    "profile": "fa-user-circle",
    "guide": "fa-book-open-reader",
    # student portal
    "results_history": "fa-square-poll-vertical",
    "chat": "fa-message",
    "my_notes": "fa-note-sticky",
    "public_notes": "fa-earth-asia",
    # admin portal
    "exams": "fa-file-pen",
    "subjects": "fa-book-bookmark",
    "questions": "fa-clipboard-question",
    "upload_images": "fa-cloud-arrow-up",
    "image_mapping": "fa-paperclip",
    "latex_editor": "fa-square-root-variable",
    "object_storage": "fa-box-archive",
    "ai_configuration": "fa-microchip",
    "users": "fa-users",
    "attempts": "fa-list-check",
}


def nav_icon(key: str) -> str:
    """Font Awesome class for a portal section; a missing key is a programming error, so it raises instead of drawing a
    random fallback icon."""
    return ICONS[key]
