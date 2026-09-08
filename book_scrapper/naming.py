"""Readable, portable book filenames derived conservatively from post titles."""
import re
import unicodedata


def book_filename(title, extension):
    parts = re.split(r'\s+[-–—]\s+', title.strip())
    if len(parts) >= 2 and parts[-1].strip() and parts[0].strip():
        author, book = parts[-1], ' - '.join(parts[:-1])
    else:
        author, book = 'Unknown Author', title or 'Untitled Book'
    name = unicodedata.normalize('NFC', f'{author} - {book}')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip(' .')
    # Linux commonly limits one filename component to 255 bytes, not characters.
    while len(name.encode('utf-8')) > 220:
        name = name[:-1]
    return name.rstrip(' .') + extension
