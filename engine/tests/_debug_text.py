"""Debug: verify lxml.html text_content() works on fixture elements."""
import lxml.html

html = open(
    r"C:\Users\USER\innovlabs\iofast-wt-src-social\engine\tests\fixtures\sources\instagram_post.html",
    encoding="utf-8",
).read()
tree = lxml.html.fromstring(html)

span = tree.xpath('//article//span[@dir="auto"]')[0]
print("span type:", type(span))
print("span text_content():", repr(span.text_content()))
print("span text:", repr(span.text))

a = tree.xpath('//header//a[starts-with(@href, "/")]')[0]
print("a text_content():", repr(a.text_content()))
print("a text:", repr(a.text))
print("a href:", repr(a.get("href")))

# Also test the _FakeElement wrapper from the test file
import sys, importlib.util
spec = importlib.util.spec_from_file_location(
    "test_mod",
    r"C:\Users\USER\innovlabs\iofast-wt-src-social\engine\tests\test_sources_social.py",
)
mod = importlib.util.module_from_spec(spec)
sys.modules["pytest"] = type(sys)("pytest")  # stub pytest so import works
sys.modules["pytest"].mark = type(sys)("mark")
sys.modules["pytest"].mark.asyncio = lambda f: f
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print("Module load error (expected):", e)

# Manually construct a FakePage and test inner_text
from pathlib import Path
fp = mod.FakePage(Path(r"C:\Users\USER\innovlabs\iofast-wt-src-social\engine\tests\fixtures\sources\instagram_post.html"))

import asyncio

async def test():
    el = await fp.query_selector("article span[dir='auto']")
    print("\nFakeElement found:", el is not None)
    if el:
        txt = await el.inner_text()
        print("FakeElement inner_text():", repr(txt))
        print("FakeElement _el type:", type(el._el))
        print("FakeElement _el.text_content():", repr(el._el.text_content()))

    author = await fp.query_selector('header a[href^="/"]')
    print("\nAuthor element found:", author is not None)
    if author:
        href = await author.get_attribute("href")
        txt = await author.inner_text()
        print("Author href:", repr(href))
        print("Author inner_text():", repr(txt))

asyncio.run(test())