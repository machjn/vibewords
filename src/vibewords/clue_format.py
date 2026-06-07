import html as _html
from html.parser import HTMLParser

_ALLOWED = frozenset({'em', 'strong', 'sup', 'sub'})
_NORMALIZE = {'i': 'em', 'b': 'strong'}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._open: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = _NORMALIZE.get(tag, tag)
        if tag in _ALLOWED:
            self._parts.append(f'<{tag}>')
            self._open.append(tag)

    def handle_endtag(self, tag):
        tag = _NORMALIZE.get(tag, tag)
        if tag in _ALLOWED and tag in self._open:
            while self._open and self._open[-1] != tag:
                self._parts.append(f'</{self._open.pop()}>')
            if self._open:
                self._open.pop()
            self._parts.append(f'</{tag}>')

    def handle_data(self, data):
        self._parts.append(_html.escape(data, quote=False))


def sanitize_clue_html(text: str) -> str:
    """Return text with all HTML stripped except em, strong, sup, sub (no attributes).

    i/b are normalised to em/strong. Entities in text nodes are re-encoded.
    Unclosed or misnested tags are closed automatically.
    """
    s = _Sanitizer()
    s.feed(text)
    for tag in reversed(s._open):
        s._parts.append(f'</{tag}>')
    return ''.join(s._parts)
