# Minify a KiCad schematic SVG (merge stroked-text paths, 2 decimals, drop hidden text) and crop it:
#     python3 crop_svg.py IN.svg OUT.svg [x0,y0,x1,y1]
import re, sys
t = open(sys.argv[1]).read()
t = re.sub(r'<text[^>]*>[^<]*</text>\s*', '', t)          # hidden copies of stroked text
t = re.sub(r'<desc>[^<]*</desc>', '', t)
t = re.sub(r'<title>[^<]*</title>\s*', '', t)             # carries the export date
num = lambda m: ('%.2f' % float(m.group(0))).rstrip('0').rstrip('.')
def merge(m):
    ds = re.findall(r'd="([^"]*)"', m.group(0))
    out, last = [], None
    for d in ds:
        pts = re.findall(r'([ML])\s*([-\d.]+)[ ,]([-\d.]+)', d)
        for i, (op, x, y) in enumerate(pts):
            p = (x, y)
            if op == 'M' and p == last:
                continue
            out.append(f'{op}{x} {y}')
            last = p
        if not pts or re.search(r'[^MLml\s\d.,-]', d):
            return m.group(0)
    return '<path d="' + ''.join(out) + '"/>\n'
t = re.sub(r'(?:<path d="[ML\d\s.,-]*"\s*/>\s*){2,}', merge, t)
t = re.sub(r'(?<![#\d.])-?\d+\.\d{3,}', num, t)
t = re.sub(r'\n\s*\n', '\n', t)
open(sys.argv[2], 'w').write(t)

# crop to CROP = x0,y0,x1,y1 (sheet mm): drop subpaths / shapes entirely outside it
if len(sys.argv) > 3:
    x0, y0, x1, y1 = map(float, sys.argv[3].split(','))
    inside = lambda xs, ys: max(xs) >= x0 and min(xs) <= x1 and max(ys) >= y0 and min(ys) <= y1
    def crop_d(m):
        subs = re.split(r'(?=M)', m.group(2))
        keep = []
        for s in subs:
            nums = list(map(float, re.findall(r'-?\d+(?:\.\d+)?', s)))
            if not nums:
                continue
            if inside(nums[0::2], nums[1::2]):
                keep.append(s.strip())
        return f'{m.group(1)}d="{" ".join(keep)}"' if keep else f'{m.group(1)}d=""'
    t = re.sub(r'(<path[^>]*?)d="([^"]*)"', crop_d, t, flags=re.S)
    t = re.sub(r'<path[^>]*?d=""\s*/>\s*', '', t, flags=re.S)
    def crop_shape(m):
        a = dict(re.findall(r'(\w+)="([-\d.]+)"', m.group(0)))
        if m.group(1) == 'circle':
            cx, cy, r = float(a['cx']), float(a['cy']), float(a['r'])
            ok = inside([cx - r, cx + r], [cy - r, cy + r])
        elif m.group(1) == 'rect':
            x, y, w, h = (float(a[k]) for k in ('x', 'y', 'width', 'height'))
            ok = inside([x, x + w], [y, y + h])
        else:
            ok = inside([float(a['x1']), float(a['x2'])], [float(a['y1']), float(a['y2'])])
        return m.group(0) if ok else ''
    t = re.sub(r'<(circle|rect|line)\b[^>]*/>\s*', crop_shape, t, flags=re.S)
    t = re.sub(r'<g\b[^>]*>\s*</g>\s*', '', t, flags=re.S)
    t = re.sub(r'<g\b[^>]*>\s*</g>\s*', '', t, flags=re.S)
    t = re.sub(r'width="[^"]*mm" height="[^"]*mm" viewBox="[^"]*"', f'width="{x1-x0:g}mm" height="{y1-y0:g}mm" viewBox="{x0:g} {y0:g} {x1-x0:g} {y1-y0:g}"', t, count=1)
    open(sys.argv[2], 'w').write(t)
