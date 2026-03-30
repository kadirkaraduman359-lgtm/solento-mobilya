import os, glob

template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')

# Mapping: double-encoded (UTF-8 bytes read as cp1252, re-encoded as UTF-8) -> correct UTF-8
# Turkish char | UTF-8 bytes | cp1252 read | re-encoded UTF-8 (garbled) | correct UTF-8
replacements = [
    # ğ (U+011F): C4 9F -> Ä(C3 84) + Ÿ(C5 B8) = ÄŸ
    (b'\xc3\x84\xc5\xb8', 'ğ'.encode('utf-8')),
    # Ğ (U+011E): C4 9E -> Ä(C3 84) + ž(C5 BE) = Až
    (b'\xc3\x84\xc5\xbe', 'Ğ'.encode('utf-8')),
    # ş (U+015F): C5 9F -> Å(C3 85) + Ÿ(C5 B8) = ÅŸ
    (b'\xc3\x85\xc5\xb8', 'ş'.encode('utf-8')),
    # Ş (U+015E): C5 9E -> Å(C3 85) + ž(C5 BE) = Až
    (b'\xc3\x85\xc5\xbe', 'Ş'.encode('utf-8')),
    # ı (U+0131): C4 B1 -> Ä(C3 84) + ±(C2 B1) = Ä±
    (b'\xc3\x84\xc2\xb1', 'ı'.encode('utf-8')),
    # İ (U+0130): C4 B0 -> Ä(C3 84) + °(C2 B0) = Ä°
    (b'\xc3\x84\xc2\xb0', 'İ'.encode('utf-8')),
    # ö (U+00F6): C3 B6 -> Ã(C3 83) + ¶(C2 B6) = Ã¶
    (b'\xc3\x83\xc2\xb6', 'ö'.encode('utf-8')),
    # ü (U+00FC): C3 BC -> Ã(C3 83) + ¼(C2 BC) = Ã¼
    (b'\xc3\x83\xc2\xbc', 'ü'.encode('utf-8')),
    # ç (U+00E7): C3 A7 -> Ã(C3 83) + §(C2 A7) = Ã§
    (b'\xc3\x83\xc2\xa7', 'ç'.encode('utf-8')),
    # Ö (U+00D6): C3 96 -> Ã(C3 83) + –(E2 80 93) = Ã–
    (b'\xc3\x83\xe2\x80\x93', 'Ö'.encode('utf-8')),
    # Ü (U+00DC): C3 9C -> Ã(C3 83) + œ(C5 93) = Ãœ
    (b'\xc3\x83\xc5\x93', 'Ü'.encode('utf-8')),
    # Ç (U+00C7): C3 87 -> Ã(C3 83) + ‡(E2 80 A1) = Ã‡
    (b'\xc3\x83\xe2\x80\xa1', 'Ç'.encode('utf-8')),
    # Ğ alt: C4 9E -> Ä(C3 84) + ž(C5 BE)  (already above)
    # ğ alt form if any
]

def fix_file(path):
    with open(path, 'rb') as f:
        raw = f.read()
    fixed = raw
    for bad, good in replacements:
        fixed = fixed.replace(bad, good)
    if fixed != raw:
        with open(path, 'wb') as f:
            f.write(fixed)
        return True
    return False

count = 0
for path in glob.glob(os.path.join(template_dir, '**', '*.html'), recursive=True):
    if fix_file(path):
        rel = os.path.relpath(path, template_dir)
        print(f'Fixed: {rel}')
        count += 1

print(f'\nTotal: {count} files fixed')

# Verify key chars in login.html
login_path = os.path.join(template_dir, 'login.html')
with open(login_path, 'rb') as f:
    d = f.read()
for char in ['ğ','ş','ı','İ','ö','ü','Ğ','Ş']:
    cb = char.encode('utf-8')
    if cb in d:
        print(f'  {char} OK')
    else:
        print(f'  {char} MISSING')
