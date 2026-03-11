"""
EmailValidator — Robust email extraction and validation.
=========================================================
Replaces naive regex-only extraction with:
  • TLD validation against known-valid TLDs
  • Disposable/temporary email detection
  • URL-in-email detection (catches .comig, .comabc)
  • Numeric-heavy local part filtering
  • Comprehensive bad domain list
"""
import re
from typing import List, Set

# ── Improved email regex ─────────────────────────────────────────────
# Must end at a word boundary or whitespace to catch ".comig" type false positives
EMAIL_RE = re.compile(
    r'[\w.\-+]+@[\w.\-]+\.([a-zA-Z]{2,6})'
    r'(?=[\s,;:!?\'")\]}<>]|$)'   # lookahead: must be followed by delimiter or end
)

# ── Known valid TLDs (covers 99%+ of real emails) ───────────────────
VALID_TLDS: Set[str] = {
    # Generic
    'com', 'org', 'net', 'edu', 'gov', 'mil', 'int',
    # Popular
    'io', 'co', 'me', 'tv', 'ai', 'app', 'dev', 'xyz', 'info',
    'biz', 'pro', 'name', 'mobi', 'tel', 'asia', 'cat',
    'jobs', 'museum', 'travel', 'aero', 'coop',
    # New gTLDs
    'agency', 'email', 'online', 'store', 'shop', 'site', 'tech',
    'space', 'fun', 'cloud', 'digital', 'media', 'group',
    'design', 'studio', 'global', 'world', 'zone', 'life',
    'live', 'network', 'systems', 'solutions', 'services',
    'company', 'center', 'team', 'work', 'plus', 'gg',
    # Country codes (most common)
    'uk', 'us', 'ca', 'au', 'de', 'fr', 'it', 'es', 'nl', 'be',
    'ch', 'at', 'se', 'no', 'dk', 'fi', 'ie', 'pt', 'gr', 'pl',
    'cz', 'hu', 'ro', 'bg', 'hr', 'sk', 'si', 'lt', 'lv', 'ee',
    'jp', 'kr', 'cn', 'tw', 'hk', 'sg', 'my', 'th', 'ph', 'id',
    'vn', 'in', 'pk', 'bd', 'lk', 'np',
    'br', 'mx', 'ar', 'cl', 'co', 'pe', 'ec', 'uy', 'py', 've',
    'za', 'ng', 'ke', 'eg', 'ma', 'tn', 'gh', 'tz',
    'ru', 'ua', 'by', 'kz', 'uz', 'ge', 'am', 'az',
    'il', 'ae', 'sa', 'qa', 'kw', 'om', 'bh', 'jo', 'lb', 'tr',
    'nz', 'is', 'lu', 'mt', 'cy',
    # Multi-part ccTLDs (we check only the last part)
    # .co.uk → 'uk', .com.au → 'au', .com.br → 'br' — already covered
}

# ── Bad domains ──────────────────────────────────────────────────────
BAD_DOMAINS: Set[str] = {
    'example.com', 'email.com', 'test.com', 'domain.com',
    'sentry.io', 'wixpress.com', 'schema.org', 'youtube.com',
    'google.com', 'gstatic.com', 'w3.org', 'youtu.be',
    'mozilla.org', 'apple.com', 'microsoft.com', 'android.com',
    'creativecommons.org', 'instagram.com', 'twitter.com',
    'facebook.com', 'tiktok.com', 'snapchat.com',
    'yahoo.com', 'yahoodns.net', 'yimg.com', 'oath.com',
    'verizonmedia.com', 'aol.com',
    # CDN / hosting / platform internals
    'cloudflare.com', 'amazonaws.com', 'herokuapp.com',
    'googleusercontent.com', 'fbcdn.net', 'twimg.com',
    'shopify.com', 'squarespace.com', 'wordpress.com',
    'github.com', 'gitlab.com', 'bitbucket.org',
}

# ── Disposable email domains ─────────────────────────────────────────
DISPOSABLE_DOMAINS: Set[str] = {
    'guerrillamail.com', 'guerrillamailblock.com', 'mailinator.com',
    'tempmail.com', 'throwaway.email', 'yopmail.com',
    'temp-mail.org', '10minutemail.com', 'trashmail.com',
    'fakeinbox.com', 'sharklasers.com', 'guerrillamail.info',
    'mailnesia.com', 'maildrop.cc', 'dispostable.com',
}

# ── File extension false positives ───────────────────────────────────
BAD_EXTENSIONS: Set[str] = {
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico',
    '.css', '.js', '.woff', '.woff2', '.ttf', '.eot',
    '.mp4', '.webm', '.mp3', '.pdf', '.zip', '.gz',
    '.html', '.htm', '.xml', '.json', '.txt',
}


class EmailValidator:
    """Industry-grade email extraction with multi-layer validation."""

    def __init__(self):
        self._seen: Set[str] = set()

    def reset(self):
        """Clear seen cache (for new run)."""
        self._seen.clear()

    def extract(self, text: str) -> List[str]:
        """Extract and validate emails from text.
        Returns deduplicated list of valid emails."""
        if not text or len(text) < 5:
            return []

        valid = []
        for match in EMAIL_RE.finditer(text):
            email = match.group(0).strip().rstrip('.').lower()
            tld = match.group(1).lower()

            if not self._is_valid(email, tld):
                continue

            if email not in self._seen:
                self._seen.add(email)
                valid.append(email)

        return valid

    def extract_fresh(self, text: str) -> List[str]:
        """Extract emails WITHOUT dedup (for snippet extraction where
        the same email from different sources is OK)."""
        if not text or len(text) < 5:
            return []

        valid = []
        local_seen: Set[str] = set()
        for match in EMAIL_RE.finditer(text):
            email = match.group(0).strip().rstrip('.').lower()
            tld = match.group(1).lower()

            if not self._is_valid(email, tld):
                continue

            if email not in local_seen:
                local_seen.add(email)
                valid.append(email)

        return valid

    def _is_valid(self, email: str, tld: str) -> bool:
        """Multi-layer validation."""
        # Length checks
        if len(email) < 6 or len(email) > 100:
            return False

        local_part, _, domain = email.partition('@')
        if not domain:
            return False

        # Domain validation
        if domain in BAD_DOMAINS:
            return False
        if domain in DISPOSABLE_DOMAINS:
            return False

        # TLD validation against known-valid set
        if tld not in VALID_TLDS:
            return False

        # File extension false positives
        if any(email.endswith(ext) for ext in BAD_EXTENSIONS):
            return False

        # Local part too short
        if len(local_part) < 2:
            return False

        # Numeric-heavy local part (likely auto-generated)
        digits = sum(1 for c in local_part if c.isdigit())
        if len(local_part) > 4 and digits / len(local_part) > 0.8:
            return False

        # No dots in domain (invalid)
        if '.' not in domain:
            return False

        return True


# Module-level singleton for backward compatibility
_default_validator = EmailValidator()


def extract_emails(text: str) -> List[str]:
    """Drop-in replacement for the original extract_emails function."""
    return _default_validator.extract_fresh(text)
