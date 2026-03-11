"""
YouTube search: autocomplete suggestions, keyword expansion.
"""
import json
from typing import List

import aiohttp

from engine.constants import AUTOCOMPLETE_URL


async def yt_autocomplete(session: aiohttp.ClientSession,
                          keyword: str,
                          timeout_secs: float = 3.0
                         ) -> List[str]:
    """Fetch YouTube autocomplete suggestions for a keyword.
    Returns 10-15 real suggestions from YouTube's suggest API.
    Free, no API key, very fast (~50ms per call).
    """
    params = {'client': 'youtube', 'ds': 'yt', 'q': keyword}
    try:
        async with session.get(
            AUTOCOMPLETE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout_secs),
        ) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()
    except Exception:
        return []

    try:
        start = text.index('[')
        data = json.loads(text[start:-1])
        suggestions = []
        if len(data) > 1 and isinstance(data[1], list):
            for item in data[1]:
                if isinstance(item, list) and len(item) > 0:
                    suggestion = item[0]
                    if isinstance(suggestion, str) and suggestion.lower() != keyword.lower():
                        suggestions.append(suggestion)
        return suggestions
    except (ValueError, json.JSONDecodeError, IndexError):
        return []
