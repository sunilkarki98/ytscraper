"""
YouTube constants: SP parameter encoding, consent cookies, regex patterns.
"""
import re

# ─── YouTube SP Parameter Encoding ──────────────────────────────────
SP_PARAMS = {
    "sort_upload_date": "CAI%3D",
    "sort_view_count":  "CAM%3D",
    "sort_rating":      "CAE%3D",
    "date_hour":   "EgIIAQ%3D%3D",
    "date_today":  "EgIIAg%3D%3D",
    "date_week":   "EgIIAw%3D%3D",
    "date_month":  "EgIIBA%3D%3D",
    "date_year":   "EgIIBQ%3D%3D",
    "dur_short":   "EgIYAQ%3D%3D",
    "dur_medium":  "EgIYAw%3D%3D",
    "dur_long":    "EgIYAg%3D%3D",
    "type_video":    "EgIQAQ%3D%3D",
    "type_channel":  "EgIQAg%3D%3D",
    "type_playlist": "EgIQAw%3D%3D",
    "feat_live":      "EgJAAQ%3D%3D",
    "feat_4k":        "EgJwAQ%3D%3D",
    "feat_hd":        "EgIgAQ%3D%3D",
    "feat_subtitles": "EgIoAQ%3D%3D",
    "feat_cc":        "EgIwAQ%3D%3D",
    "feat_360":       "EgJ4AQ%3D%3D",
    "feat_vr180":     "EgPQAQE%3D",
    "feat_hdr":       "EgPIAQE%3D",
    "exact_match": "QgIIAQ%3D%3D",
}

# ─── Consent Cookies ─────────────────────────────────────────────────
CONSENT_COOKIES = {
    'CONSENT': 'YES+cb.20210328-17-p0.en-GB+FX+999',
    'SOCS': 'CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjMwODI5LjA3X3AxGgJlbiACGgYIgJnPpwY',
}

# ─── Regex Patterns ──────────────────────────────────────────────────
DESC_PATTERN_META = re.compile(
    r'<meta\s+(?:property|name)="(?:og:description|description)"\s+content="([^"]*)"',
    re.IGNORECASE,
)
DESC_PATTERN_JSON = re.compile(
    r'"description":\s*\{"simpleText":\s*"((?:[^"\\\\]|\\\\.)*)"\}'
)
SUBS_PATTERN = re.compile(
    r'"subscriberCountText":\s*\{[^}]*"simpleText":\s*"([^"]+)"'
)
NAME_PATTERN_JSON = re.compile(
    r'"channelMetadataRenderer"[^}]*?"title":\s*"((?:[^"\\\\]|\\\\.)*)\"'
)

# ─── Autocomplete URL ───────────────────────────────────────────────
AUTOCOMPLETE_URL = "https://suggestqueries-clients6.youtube.com/complete/search"
