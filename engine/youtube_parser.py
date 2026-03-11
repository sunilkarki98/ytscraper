"""
YouTube data parsing: extracting structured data from YouTube HTML/JSON responses.
Includes subscriber parsing, social link extraction, ytInitialData extraction, etc.
"""
import re
import json
import html as html_lib
from typing import Any, List, Dict, Optional

from engine.constants import (
    DESC_PATTERN_META, DESC_PATTERN_JSON, SUBS_PATTERN, NAME_PATTERN_JSON,
)


# ─── Helper Functions ────────────────────────────────────────────────

def _fast_extract_yt_data(html: str) -> Optional[str]:
    """Extract ytInitialData JSON string using fast string operations.
    ~100x faster than DOTALL regex on large HTML."""
    marker = 'var ytInitialData = '
    start = html.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = html.find(';</script>', start)
    if end == -1:
        return None
    return html[start:end]


def _parse_subscribers(text: str) -> int:
    """Parse '1.2M subscribers' → 1200000."""
    if not text:
        return 0
    text = text.lower().replace(',', '').replace(' subscribers', '').replace(' subscriber', '').strip()
    try:
        if 'k' in text:
            return int(float(text.replace('k', '')) * 1000)
        elif 'm' in text:
            return int(float(text.replace('m', '')) * 1_000_000)
        elif 'b' in text:
            return int(float(text.replace('b', '')) * 1_000_000_000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def _parse_view_count(text: str) -> int:
    """Parse '1,234 views' → 1234 or '1.2M views' → 1200000."""
    if not text:
        return 0
    text = text.lower().replace(',', '').replace(' views', '').replace(' view', '').strip()
    if 'no' in text:
        return 0
    try:
        if 'k' in text:
            return int(float(text.replace('k', '')) * 1000)
        elif 'm' in text:
            return int(float(text.replace('m', '')) * 1_000_000)
        elif 'b' in text:
            return int(float(text.replace('b', '')) * 1_000_000_000)
        else:
            return int(float(text))
    except (ValueError, TypeError):
        return 0


def _parse_duration_text(text: str) -> int:
    """Parse '12:34' → 754 seconds, or '1:02:30' → 3750 seconds."""
    if not text:
        return 0
    parts = text.strip().split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 1:
            return int(parts[0])
    except (ValueError, TypeError):
        pass
    return 0


def _unescape_json_string(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s.replace('\\n', '\n').replace('\\\\', '\\')


# ─── Social Link Extraction ─────────────────────────────────────────

def extract_social_links(text: str) -> Dict[str, str]:
    """Extract social media URLs from text (description or links)."""
    socials = {}
    m = re.search(r'(?:https?://)?(?:www\.)?instagram\.com/([\w.]+)', text, re.I)
    if m:
        socials['instagram'] = f"https://instagram.com/{m.group(1)}"
    m = re.search(r'(?:https?://)?(?:www\.)?(?:twitter|x)\.com/([\w]+)', text, re.I)
    if m:
        socials['twitter'] = f"https://x.com/{m.group(1)}"
    m = re.search(r'(?:https?://)?(?:www\.)?tiktok\.com/@?([\w.]+)', text, re.I)
    if m:
        socials['tiktok'] = f"https://tiktok.com/@{m.group(1)}"
    m = re.search(r'(?:https?://)?(?:www\.)?facebook\.com/([\w.]+)', text, re.I)
    if m:
        socials['facebook'] = f"https://facebook.com/{m.group(1)}"
    m = re.search(r'(?:https?://)?(?:www\.)?linkedin\.com/(?:in|company)/([\w-]+)', text, re.I)
    if m:
        socials['linkedin'] = f"https://linkedin.com/in/{m.group(1)}"
    m = re.search(r'(?:https?://)(?!(?:www\.)?(?:youtube|google|instagram|twitter|x|tiktok|facebook|linkedin|bit\.ly))[\w.-]+\.[a-z]{2,}(?:/[\w./-]*)?', text, re.I)
    if m:
        socials['website'] = m.group(0)
    return socials


# ─── Recursive JSON Search ───────────────────────────────────────────

def _find_keys(node: Any, target_keys: set) -> List[Any]:
    """Recursively search for specific keys in a nested dictionary or list."""
    results = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in target_keys:
                results.append(v)
            if isinstance(v, (dict, list)):
                results.extend(_find_keys(v, target_keys))
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                results.extend(_find_keys(item, target_keys))
    return results


def _find_first_val(node: Any, target_key: str) -> Any:
    """Find the first occurrence of a key in a nested structure."""
    if isinstance(node, dict):
        if target_key in node:
            return node[target_key]
        for v in node.values():
            if isinstance(v, (dict, list)):
                res = _find_first_val(v, target_key)
                if res is not None:
                    return res
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                res = _find_first_val(item, target_key)
                if res is not None:
                    return res
    return None


# ─── ytInitialData Extraction ────────────────────────────────────────

def _extract_from_ytInitialData(data: dict) -> Optional[Dict]:
    """Extract description, name, subs, and social links from parsed ytInitialData."""
    try:
        desc = ''
        name = ''

        metadata = data.get('metadata', {}).get('channelMetadataRenderer', {})
        desc = metadata.get('description', '')
        name = metadata.get('title', '')

        if not desc:
            micro = data.get('microformat', {}).get('microformatDataRenderer', {})
            desc = micro.get('description', '')
            if not name:
                name = micro.get('title', '')

        if not desc:
            metadatas = _find_keys(data, {'channelAboutFullMetadataRenderer'})
            for meta in metadatas:
                d = meta.get('description', '')
                if isinstance(d, dict): d = d.get('simpleText', '')
                if d and isinstance(d, str):
                    desc = d
                    break
            if not name:
                for meta in metadatas:
                    n = meta.get('title', '')
                    if isinstance(n, dict): n = n.get('simpleText', '')
                    if n and isinstance(n, str):
                        name = n
                        break

        if not desc:
            return None

        subs = 0
        try:
            header = data.get('header', {})
            c4 = header.get('c4TabbedHeaderRenderer', {})
            subs_text = c4.get('subscriberCountText', {}).get('simpleText', '')
            if subs_text:
                subs = _parse_subscribers(subs_text)

            if subs == 0:
                page_header = header.get('pageHeaderRenderer', {})
                content = page_header.get('content', {}).get('pageHeaderViewModel', {})
                metadata_row = content.get('metadata', {}).get('contentMetadataViewModel', {})
                rows = metadata_row.get('metadataRows', [])
                for row in rows:
                    parts = row.get('metadataParts', [])
                    for part in parts:
                        text_content = part.get('text', {}).get('content', '')
                        if 'subscriber' in text_content.lower():
                            subs = _parse_subscribers(text_content)
                            break
        except Exception:
            pass

        social_links = {}
        try:
            header = data.get('header', {})
            c4 = header.get('c4TabbedHeaderRenderer', {})
            for link_group in c4.get('headerLinks', {}).get('channelHeaderLinksViewModel', {}).get('firstLinks', []):
                try:
                    url = link_group.get('channelExternalLinkViewModel', {}).get('link', {}).get('commandRuns', [{}])[0].get('onTap', {}).get('innertubeCommand', {}).get('urlEndpoint', {}).get('url', '')
                    title = link_group.get('channelExternalLinkViewModel', {}).get('title', {}).get('content', '').lower()
                    if url:
                        if 'instagram' in title or 'instagram.com' in url:
                            social_links['instagram'] = url
                        elif 'twitter' in title or 'x.com' in url or 'twitter.com' in url:
                            social_links['twitter'] = url
                        elif 'tiktok' in title or 'tiktok.com' in url:
                            social_links['tiktok'] = url
                        elif 'facebook' in title or 'facebook.com' in url:
                            social_links['facebook'] = url
                        elif 'linkedin' in title or 'linkedin.com' in url:
                            social_links['linkedin'] = url
                        elif not social_links.get('website'):
                            social_links['website'] = url
                except Exception:
                    pass
        except Exception:
            pass

        try:
            tabs = data.get('contents', {}).get('twoColumnBrowseResultsRenderer', {}).get('tabs', [])
            for tab in tabs:
                tab_content = tab.get('tabRenderer', {}).get('content', {})
                section_list = tab_content.get('sectionListRenderer', {}).get('contents', [])
                for section in section_list:
                    about = section.get('itemSectionRenderer', {}).get('contents', [{}])[0]
                    about_renderer = about.get('channelAboutFullMetadataRenderer', {})
                    if about_renderer:
                        for link in about_renderer.get('primaryLinks', []):
                            link_url = link.get('navigationEndpoint', {}).get('urlEndpoint', {}).get('url', '')
                            link_title = link.get('title', {}).get('simpleText', '').lower()
                            if link_url and 'mailto:' in link_url:
                                email = link_url.replace('mailto:', '').strip()
                                if email and '@' in email:
                                    desc += f'\n{email}'
                            elif link_url and link_url not in desc:
                                if 'email' in link_title or 'business' in link_title or 'contact' in link_title:
                                    desc += f'\n{link_url}'
                        biz_email = about_renderer.get('businessEmailLabel', '')
                        if biz_email and '@' in str(biz_email):
                            desc += f'\n{biz_email}'
        except Exception:
            pass

        desc_socials = extract_social_links(desc)
        for k, v in desc_socials.items():
            if k not in social_links:
                social_links[k] = v

        return {'name': name or 'Unknown', 'description': desc, 'subscribers': subs, 'social_links': social_links}
    except Exception:
        return None


def _extract_from_html_fallback(page_html: str) -> Optional[Dict]:
    """Fallback: extract from raw HTML when ytInitialData parse fails."""
    desc = None
    m = DESC_PATTERN_JSON.search(page_html)
    if m:
        desc = _unescape_json_string(m.group(1))
    if not desc:
        m = DESC_PATTERN_META.search(page_html)
        if m:
            desc = html_lib.unescape(m.group(1))
    if not desc:
        return None

    subs = 0
    m = SUBS_PATTERN.search(page_html)
    if m:
        subs = _parse_subscribers(m.group(1))

    name = 'Unknown'
    m = NAME_PATTERN_JSON.search(page_html)
    if m:
        name = _unescape_json_string(m.group(1))

    social_links = extract_social_links(desc)
    return {'name': name, 'description': desc, 'subscribers': subs, 'social_links': social_links}
