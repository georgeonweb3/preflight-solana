"""Input resolution: turn whatever the user pasted into a contract address.

Accepts, in order of confidence:
1. a bare 0x address;
2. free text containing an address;
3. a URL that itself contains an address (explorer links, ?ca= links) -
   authoritative enough to skip fetching the page;
4. a project/mint page URL - fetched read-only, candidate addresses
   extracted and ranked: explorer address/token links first, then
   addresses appearing near contract/mint keywords, then the rest in
   page order.

If nothing resolves, the caller reports UNRESOLVED_INPUT. We never guess
an address that is not in the input. Logic adapted from the parent
bsc-mint-precheck skill; explorer domains are per-chain config now.
"""
import re
import urllib.request

ADDR_RE = re.compile(r"0x[a-fA-F0-9]{40}")
_KEYWORDS = ("contract", "mint", "ca:", "ca ", "address")


def resolve_input(target: str, explorer_domains=()):
    """Returns (address_or_None, source_note, all_candidates)."""
    target = target.strip()
    if ADDR_RE.fullmatch(target):
        return target, "input was a bare contract address", [target]
    if not target.startswith("http"):
        found = ADDR_RE.findall(target)
        if found:
            return found[0], "address extracted from text input", found
        return None, "no contract address found in the input text", []
    if target.startswith("http"):
        in_url = ADDR_RE.findall(target)
        if in_url:
            return in_url[0], "address extracted from the URL itself", in_url

    # URL: fetch the page and extract candidates.
    try:
        req = urllib.request.Request(
            target, headers={"User-Agent": "Mozilla/5.0 (preflight)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read(1_500_000).decode("utf-8", "replace")
    except Exception as e:
        return None, f"URL fetch failed: {e}", []

    candidates = []
    if explorer_domains:
        dom = "|".join(re.escape(d) for d in explorer_domains)
        for link in re.findall(rf"(?:{dom})/(?:address|token)/(0x[a-fA-F0-9]{{40}})", html):
            if link not in candidates:
                candidates.append(link)
    lowered = html.lower()
    for match in ADDR_RE.finditer(html):
        addr = match.group(0)
        window = lowered[max(0, match.start() - 120):match.start() + 60]
        if any(k in window for k in _KEYWORDS):
            if addr not in candidates:
                candidates.append(addr)
    for addr in ADDR_RE.findall(html):
        if addr not in candidates:
            candidates.append(addr)
    if not candidates:
        return None, "no contract address found on the page", []
    return candidates[0], f"address extracted from URL ({len(candidates)} candidate(s) on page)", candidates
