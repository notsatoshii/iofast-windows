"""Debug script: test XPath translation against fixture HTML."""
from lxml import etree
import re


def css_to_xpath(selector: str) -> str:
    """Translate the CSS selector subset our extractors use into lxml XPath.
    Handles: tag, .class, #id, [attr], [attr=val], [attr*=val], [attr^=val],
    descendant combinator (space), child combinator (>), comma unions.
    Always produces an absolute path from document root (leading //).
    """
    selector = selector.strip()
    if not selector:
        return "."
    # Comma-separated unions.
    if "," in selector:
        parts = [css_to_xpath(p.strip()) for p in selector.split(",")]
        return " | ".join(parts)
    # Child combinator: split on " > " and chain with single /.
    if " > " in selector:
        parts = [_simple_to_xpath(p.strip()) for p in selector.split(" > ")]
        return "/" + "/".join(parts)
    # Descendant combinator: split on whitespace (but not inside brackets).
    tokens = re.split(r"\s+(?![^\[]*\])", selector)
    if len(tokens) > 1:
        parts = [_simple_to_xpath(t) for t in tokens]
        return "//" + "//".join(parts)
    # Single simple selector — make it absolute from root.
    return "//" + _simple_to_xpath(selector)


def _simple_to_xpath(s: str) -> str:
    """Convert one simple CSS selector (no combinators) to an XPath step."""
    # Extract tag name. A selector starting with [ or . or # has no tag.
    m = re.match(r"^([a-zA-Z][\w-]*)?", s)
    tag = m.group(1) if m and m.group(1) else "*"
    rest = s[len(tag):]
    predicates: list[str] = []
    # Classes: only match .foo when NOT preceded by a hyphen (avoids card.wrapper).
    for cls in re.findall(r"(?<![-])\.([\w-]+)", rest):
        predicates.append(
            f"contains(concat(' ', normalize-space(@class), ' '), ' {cls} ')"
        )
    # IDs.
    for id_val in re.findall(r"#([\w-]+)", rest):
        predicates.append(f"@id='{id_val}'")
    # Attribute selectors: handle both single and double quoted values.
    attr_pattern = re.compile(
        r'\[([\w-]+)'
        r'(?:'
        r'([*^$~|]?=)'
        r'(?:"([^"]*)"|\'([^\']*)\')'
        r')?'
        r'\]'
    )
    for attr_match in attr_pattern.finditer(rest):
        attr = attr_match.group(1)
        op = attr_match.group(2)
        val = attr_match.group(3) if attr_match.group(3) is not None else attr_match.group(4)
        if op is None:
            predicates.append(f"@{attr}")
        elif op == "=":
            predicates.append(f"@{attr}='{val}'")
        elif op == "*=":
            predicates.append(f"contains(@{attr}, '{val}')")
        elif op == "^=":
            predicates.append(f"starts-with(@{attr}, '{val}')")
        elif op == "$=":
            predicates.append(
                f"substring(@{attr}, string-length(@{attr}) - string-length('{val}') + 1) = '{val}'"
            )
        elif op == "~=":
            predicates.append(
                f"contains(concat(' ', @{attr}, ' '), ' {val} ')"
            )
        elif op == "|=":
            predicates.append(
                f"@{attr}='{val}' or starts-with(@{attr}, '{val}-')"
            )

    pred_str = "".join(f"[{p}]" for p in predicates)
    return f"{tag}{pred_str}"


fixtures_dir = r"C:\Users\USER\innovlabs\iofast-wt-src-social\engine\tests\fixtures\sources"

tests = [
    ("instagram_post.html", [
        'header a[href^="/"]',
        "article span[dir='auto']",
        "time[datetime]",
        "section button span",
        "article img[srcset]",
        "article video source",
        "ul li div[dir='auto']",
    ]),
    ("x_thread.html", [
        'article[data-testid="tweet"]',
        'a[role="link"]',
        '[data-testid="tweetText"]',
        "time[datetime]",
        'a[href*="/status/"]',
        'img[src*="media"]',
        '[data-testid="card.wrapper"]',
    ]),
    ("instagram_profile.html", [
        "div.-vDIg span",
        "header span[dir='auto']",
        "header ul li",
        'a[href*="/p/"], a[href*="/reel/"]',
    ]),
    ("x_profile.html", [
        '[data-testid="UserDescription"]',
        'a[role="link"]',
        'article[data-testid="tweet"]',
    ]),
]

for fname, selectors in tests:
    print(f"\n=== {fname} ===")
    html = open(f"{fixtures_dir}/{fname}", encoding="utf-8").read()
    tree = etree.HTML(html)
    for sel in selectors:
        try:
            xp = css_to_xpath(sel)
            results = tree.xpath(xp)
            txt = ""
            if results:
                el = results[0]
                if hasattr(el, "text_content"):
                    txt = (el.text_content() or "").strip()[:60]
                else:
                    txt = str(el)[:60]
            print(f"  {sel!r:50s} -> {xp!r}")
            print(f"    => {len(results)} matches  text={txt!r}")
        except Exception as e:
            print(f"  {sel!r:50s} -> ERROR: {e}")