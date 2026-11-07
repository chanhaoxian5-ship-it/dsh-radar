"""Conservative source probe, not yet suitable for authoritative observations."""
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

HOST='cast1.cau.edu.cn'
ARTICLE=re.compile(r'^/art/\d{4}/\d{1,2}/\d{1,2}/art_\d+_(\d+)\.html$')
MAX_BYTES=2_000_000

class Page(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True); self.url=url; self.title=None; self.published_raw=None; self.depth=0; self.text=[]; self.links=[]; self.found=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and a.get('name')=='ArticleTitle': self.title=a.get('content')
        if tag=='meta' and a.get('name')=='PubDate': self.published_raw=a.get('content')
        if self.depth and tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'): self.depth+=1
        elif not self.depth and tag=='div' and a.get('id')=='zoom': self.depth=1; self.found=True
        if self.depth and tag=='a' and a.get('href'):
            target=urljoin(self.url,a['href'])
            if urlsplit(target).scheme in ('http','https'): self.links.append(target)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'): self.handle_endtag(tag)
    def handle_endtag(self,tag):
        if self.depth: self.depth-=1
    def handle_data(self,data):
        if self.depth and data.strip(): self.text.append(data.strip())

def parse(url, html):
    parts=urlsplit(url); match=ARTICLE.fullmatch(parts.path)
    if parts.scheme!='https' or parts.hostname!=HOST or not match or parts.username or parts.password: raise ValueError('unsupported article URL')
    parser=Page(url); parser.feed(html)
    return {'external_key':match.group(1),'url':url,'title':parser.title,'published_raw':parser.published_raw,'text_fragments':parser.text,'links':parser.links,'body_status':'partial','freshness':'unknown','extractor_version':'cau-probe-0.1','warnings':['结构及附件完整性尚未验证，不能提交为权威快照']}

def fetch(url):
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.hostname!=HOST or not ARTICLE.fullmatch(parts.path) or parts.username or parts.password: raise ValueError('unsupported article URL')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    try:
        with urllib.request.build_opener(NoRedirect).open(url,timeout=15) as response:
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES: return {'http_status':response.status,'body_status':'failed','error':'body_too_large'}
            content_type=response.headers.get_content_type()
            if content_type!='text/html': return {'http_status':response.status,'body_status':'failed','error':'unexpected_content_type'}
            result=parse(url,raw.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')); result['http_status']=response.status
            return result
    except urllib.error.HTTPError as e: return {'http_status':e.code,'body_status':'failed','error':'http_error'}
    except (OSError,TimeoutError) as e: return {'http_status':None,'body_status':'failed','error':type(e).__name__}
