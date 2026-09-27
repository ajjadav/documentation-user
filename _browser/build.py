#!/usr/bin/env python3
"""Bundle the documentation into a single-page reader app.

Runs Sphinx's JSON builder over the repository, rewrites each page's HTML
for in-app navigation, and writes:

    <out>/index.html         the reader, with the text of every page inlined
    <out>/img/<page>.json    one screenshot bundle per page, as WebP data URIs

Requirements: Python 3 with Sphinx (>= 7) and Pillow.

    pip install sphinx pillow
    python3 _browser/build.py [OUTPUT_DIR]      # default: _build/browser
"""
import base64
import datetime
import html
import io
import json
import os
import pickle
import posixpath
import re
import shutil
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, '_extensions'))

REPO = 'ajjadav/documentation-user'
BRANCH = '12.0'
BLOB = 'https://github.com/%s/blob/%s/' % (REPO, BRANCH)
MAX_WIDTH = 1200
QUALITY = 75

HEADERLINK = re.compile(r'<a class="headerlink"[^>]*>.*?</a>')
SCRIPT = re.compile(r'<script\b.*?</script>', re.S)
IFRAME = re.compile(r'<iframe\b[^>]*\bsrc="([^"]*)"[^>]*>\s*</iframe>', re.S)
MENU = re.compile(r'<span class="menuselection">(.*?)</span>', re.S)
TAG_A = re.compile(r'<a\b([^>]*)>')
TAG_IMG = re.compile(r'<img\b([^>]*?)\s*/?>')
TAG = re.compile(r'<[^>]+>')
SCHEME = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.-]*:')
# Absolute links to the published docs, e.g. from the Business Mementoes toctree.
PUBLISHED = re.compile(r'^https?://(?:www\.)?odoo\.com/documentation/user/(?:[\d.]+/)?(.+?)\.html(?:#(.*))?$')


def doc_id(docname):
    return docname.replace('/', '.')


def posix(path):
    return os.fspath(path).replace(os.sep, '/')


def attr(attrs, name):
    m = re.search(r'\s%s="([^"]*)"' % name, ' ' + attrs)
    return html.unescape(m.group(1)) if m else None


def run_sphinx(work):
    out = os.path.join(work, 'json')
    doctrees = os.path.join(work, 'doctrees')
    subprocess.check_call([sys.executable, '-m', 'sphinx', '-q', '-b', 'json',
                           '-c', HERE, '-d', doctrees, ROOT, out])
    with open(os.path.join(doctrees, 'environment.pickle'), 'rb') as f:
        return out, pickle.load(f)


def nav_tree(env, docname, seen):
    children = []
    for child in env.toctree_includes.get(docname, []):
        if child in seen:
            continue
        seen.add(child)
        children.append([doc_id(child), nav_tree(env, child, seen)])
    return children


class Page(object):
    """Rewrites one Sphinx JSON page body for the reader."""

    def __init__(self, docname, body, ctx):
        self.docname = docname
        self.ctx = ctx
        self.images = set()
        self.body = body

    def resolve(self, href):
        """Map a Sphinx href to (new href, opens outside the reader)."""
        published = PUBLISHED.match(href)
        if published and published.group(1) in self.ctx['docs']:
            frag = published.group(2)
            return '#' + doc_id(published.group(1)) + ('~' + frag if frag else ''), False
        if SCHEME.match(href):
            return href, True
        if href.startswith('#'):
            return '#' + doc_id(self.docname) + ('~' + href[1:] if len(href) > 1 else ''), False
        path, _, frag = href.partition('#')
        name = path.rstrip('/').rsplit('/', 1)[-1]
        if '_images/' in path and name in self.ctx['images']:
            return BLOB + self.ctx['images'][name], True
        if '_downloads/' in path and name in self.ctx['downloads']:
            return BLOB + self.ctx['downloads'][name], True
        if '_static/' in path:
            return BLOB + '_static/' + path.split('_static/', 1)[1], True
        # Sphinx emits directory-style links; hand-written links in raw HTML
        # or rST assume the plain HTML builder's page-relative layout.
        page_dir = '/' if self.docname == 'index' else '/' + self.docname + '/'
        for base in (page_dir, posixpath.dirname('/' + self.docname) + '/'):
            target = posixpath.normpath(posixpath.join(base, path)).strip('/')
            target = re.sub(r'\.html$', '', target) or 'index'
            if target in self.ctx['docs']:
                return '#' + doc_id(target) + ('~' + frag if frag else ''), False
        self.ctx['unresolved'].append((self.docname, href))
        return None, False

    def fix_anchor(self, m):
        attrs = m.group(1)
        href = re.search(r'\shref="([^"]*)"', attrs)
        if not href:
            return m.group(0)
        new, external = self.resolve(html.unescape(href.group(1)))
        if new is None:
            # Targets that only exist in the full Sphinx/LaTeX build (e.g. PDFs).
            attrs = attrs[:href.start()] + ' title="Not included in this reader"' + attrs[href.end():]
        else:
            attrs = attrs[:href.start()] + ' href="%s"' % html.escape(new) + attrs[href.end():]
        attrs = attrs.replace(' download=""', '')
        if external:
            attrs += ' target="_blank" rel="noopener noreferrer"'
        return '<a%s>' % attrs

    def fix_img(self, m):
        attrs = m.group(1)
        src = attr(attrs, 'src') or ''
        name = src.rsplit('/', 1)[-1]
        if '_images/' not in src or name not in self.ctx['sizes']:
            return m.group(0)
        self.images.add(name)
        alt = attr(attrs, 'alt') or ''
        if not alt or alt == src or re.search(r'\.(png|jpe?g|gif|svg)$', alt, re.I):
            alt = 'Screenshot: ' + re.sub(r'[_-]+', ' ', name.rsplit('.', 1)[0])
        out = ['<img data-img="%s" alt="%s"' % (html.escape(name), html.escape(alt))]
        if attr(attrs, 'width') or attr(attrs, 'height'):
            for key in ('width', 'height'):
                if attr(attrs, key):
                    out.append(' %s="%s"' % (key, html.escape(attr(attrs, key))))
        else:
            out.append(' width="%d" height="%d"' % self.ctx['sizes'][name])
        for key in ('class', 'style'):
            if attr(attrs, key):
                out.append(' %s="%s"' % (key, html.escape(attr(attrs, key))))
        out.append(' loading="lazy">')
        return ''.join(out)

    @staticmethod
    def fix_video(m):
        src = m.group(1)
        vid = src.rstrip('/').rsplit('/', 1)[-1]
        if 'youtube' in src:
            url, host = 'https://www.youtube.com/watch?v=' + vid, 'YouTube'
        else:
            url, host = 'https://vimeo.com/' + vid, 'Vimeo'
        return ('<a class="video-link" href="%s" target="_blank" rel="noopener noreferrer">'
                '<span class="video-play" aria-hidden="true"></span>'
                '<span>Watch the video on %s</span></a>' % (url, host))

    @staticmethod
    def fix_menu(m):
        steps = [s.strip() for s in m.group(1).split(u'‣')]
        sep = '<span class="ms-sep" aria-hidden="true">›</span>'
        return '<span class="menuselection">%s</span>' % sep.join(
            '<span class="ms-step">%s</span>' % s for s in steps)

    def render(self):
        body = HEADERLINK.sub('', self.body)
        body = SCRIPT.sub('', body)
        body = IFRAME.sub(self.fix_video, body)
        body = MENU.sub(self.fix_menu, body)
        body = TAG_A.sub(self.fix_anchor, body)
        body = TAG_IMG.sub(self.fix_img, body)
        body = body.replace('<table', '<div class="table-wrap"><table').replace('</table>', '</table></div>')
        return body.strip()


def encode_image(path):
    """Return a data URI for one screenshot, re-encoded as WebP when smaller."""
    with open(path, 'rb') as f:
        raw = f.read()
    ext = path.rsplit('.', 1)[-1].lower()
    mimes = {'svg': 'image/svg+xml', 'gif': 'image/gif', 'png': 'image/png',
             'jpg': 'image/jpeg', 'jpeg': 'image/jpeg'}
    data, mime = raw, mimes.get(ext, 'application/octet-stream')
    if ext in ('png', 'jpg', 'jpeg'):
        im = Image.open(io.BytesIO(raw))
        im = im.convert('RGBA' if im.mode in ('P', 'LA', 'RGBA', 'PA') else 'RGB')
        if im.width > MAX_WIDTH:
            im = im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, 'WEBP', quality=QUALITY, method=4)
        if buf.tell() < len(raw):
            data, mime = buf.getvalue(), 'image/webp'
    return 'data:%s;base64,%s' % (mime, base64.b64encode(data).decode('ascii'))


def image_size(path):
    if path.lower().endswith('.svg'):
        with open(path, encoding='utf-8', errors='replace') as f:
            head = f.read(2000)
        w = re.search(r'\bwidth="([\d.]+)', head)
        h = re.search(r'\bheight="([\d.]+)', head)
        return (int(float(w.group(1))), int(float(h.group(1)))) if w and h else (24, 24)
    with Image.open(path) as im:
        return im.size


def main():
    out_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, '_build', 'browser'))
    work = os.path.join(ROOT, '_build', 'browser-work')
    json_dir, env = run_sphinx(work)

    images = {unique: posix(path) for path, (_, unique) in env.images.items()}
    downloads = {posix(unique).rsplit('/', 1)[-1]: posix(path) for path, (_, unique) in env.dlfiles.items()}
    img_dir = os.path.join(json_dir, '_images')
    ctx = {
        'docs': set(env.all_docs),
        'images': images,
        'downloads': downloads,
        'sizes': {name: image_size(os.path.join(img_dir, name))
                  for name in os.listdir(img_dir) if name in images},
        'unresolved': [],
    }

    pages, bundles = {}, {}
    for docname in sorted(env.all_docs):
        with open(os.path.join(json_dir, docname + '.fjson'), encoding='utf-8') as f:
            data = json.load(f)
        page = Page(docname, data.get('body', ''), ctx)
        entry = {
            't': html.unescape(TAG.sub('', data.get('title', ''))) or docname,
            's': docname + '.rst',
            'h': page.render(),
        }
        if page.images:
            entry['i'] = 1
            bundles[doc_id(docname)] = sorted(page.images)
        pages[doc_id(docname)] = entry

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, 'img'))

    encoded = {}
    for pid, names in bundles.items():
        bundle = {}
        for name in names:
            if name not in encoded:
                encoded[name] = encode_image(os.path.join(img_dir, name))
            bundle[name] = encoded[name]
        with open(os.path.join(out_dir, 'img', pid + '.json'), 'w', encoding='utf-8') as f:
            json.dump(bundle, f, separators=(',', ':'))

    commit = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT).decode().strip()
    payload = {
        'meta': {
            'repo': REPO, 'branch': BRANCH, 'commit': commit,
            'built': datetime.date.today().isoformat(), 'version': '12.0',
            'screenshots': len(encoded),
        },
        'nav': nav_tree(env, 'index', {'index'}),
        'pages': pages,
    }

    with open(os.path.join(HERE, 'app.html'), encoding='utf-8') as f:
        template = f.read()
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    with open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(template.replace('__DOCS_DATA__', data))

    for docname, href in ctx['unresolved']:
        print('unresolved link in %s: %s' % (docname, href), file=sys.stderr)
    print('%d pages, %d screenshots in %d bundles -> %s' % (len(pages), len(encoded), len(bundles), out_dir))


if __name__ == '__main__':
    main()
