"""Bounded public CAU HTML adapter. No login, script challenge or Reader fallback."""
import re,urllib.request,urllib.error,http.client,xml.etree.ElementTree as ET
from urllib.parse import urlencode
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit
from ..backend import Response

class Node:
 def __init__(self,tag,attrs):self.tag=tag;self.attrs=dict(attrs);self.children=[];self.closed=False
 def walk(self):
  yield self
  for child in self.children:
   if isinstance(child,Node):yield from child.walk()
 def text(self):return ''.join(c.text() if isinstance(c,Node) else c for c in self.children if not isinstance(c,Node) or c.tag not in ('script','style'))
class DOM(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.root=Node('root',[]);self.stack=[self.root]
 def handle_starttag(self,tag,attrs):
  node=Node(tag,attrs);self.stack[-1].children.append(node)
  if tag not in ('img','br','meta','link','hr','input','source','area','wbr','embed','param','col','base'):self.stack.append(node)
 def handle_startendtag(self,tag,attrs):
  self.handle_starttag(tag,attrs)
  if self.stack[-1].tag==tag:self.stack.pop()
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==tag:self.stack[i].closed=True;self.stack=self.stack[:i];break
 def handle_data(self,data):self.stack[-1].children.append(data)

class CauAdapter:
 synthetic=False;version='cau-zoom-3';supports_base_url=True
 def __init__(self,listing_url=None,max_pages=2,transport='curl'):
  self.url='https://cast1.cau.edu.cn';self.listing=listing_url or self.url+'/col/col44372/index.html';self.max_pages=max_pages;self.transport_name=transport+'-default-tls';self._post_requests={}
  parsed=urlsplit(self.listing)
  if parsed.scheme!='https' or parsed.netloc!='cast1.cau.edu.cn' or not re.fullmatch(r'/col/col\d+/index.html',parsed.path) or parsed.query or parsed.fragment:raise ValueError('unsupported public listing URL')
  if type(max_pages) is not int or not 1<=max_pages<=10 or transport not in ('curl','urllib'):raise ValueError('invalid bounded source options')
 def identity(self,url):
  m=re.fullmatch(r'/art/\d{4}/\d{1,2}/\d{1,2}/art_\d+_(\d+)\.html',urlsplit(url).path)
  if not m:raise ValueError('unsupported CAU article identity')
  return m[1]
 def fetch(self,url):
  parts=urlsplit(url)
  if parts.scheme!='https' or parts.netloc!='cast1.cau.edu.cn' or parts.username:raise ValueError('unsupported source origin')
  if self.transport_name.startswith('curl'):
   from ..transport import curl_fetch
   return curl_fetch(url,data=self._post_requests.get(url))
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args):return None
  try:
   response=urllib.request.build_opener(NoRedirect).open(url,data=self._post_requests.get(url),timeout=20)
  except urllib.error.HTTPError as error:response=error
  with response:
   complete=True
   try:body=response.read(10*1024*1024+1)
   except http.client.IncompleteRead as error:body=error.partial;complete=False
   if len(body)>10*1024*1024:body=body[:10*1024*1024];complete=False
   declared=response.headers.get('content-length')
   if declared and (not declared.isdigit() or int(declared)!=len(body)):complete=False
   return Response(response.code,response.geturl(),body,{k.lower():v for k,v in response.headers.items()},complete)
 def discover(self,fetch=None):
  self.last_discovery_response=None;self.discovery_responses=[];self.last_pages=[];self._post_requests={};fetch=fetch or self.fetch
  def read(url):
   response=fetch(url);self.discovery_responses.append((url,response));self.last_discovery_response=response
   if not response.complete or response.status!=200:raise OSError('CAU listing unconfirmed HTTP '+str(response.status))
   return response.body.decode('utf-8')
  html=read(self.listing)
  xml_match=re.search(r'<script[^>]+type=["\']text/xml["\'][^>]*>(.*?)</script>',html,re.S|re.I)
  if not xml_match:raise ValueError('public pagination datastore absent')
  def records(xml):
   if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():raise ValueError('unsafe XML declaration')
   root=ET.fromstring(xml)
   if root.tag!='datastore':raise ValueError('unrecognized public datastore')
   return [n.text or '' for n in root.findall('.//record')]
  initial=records(xml_match[1]);per_match=re.search(r'perPage\s*:\s*(\d+)',html);total_match=re.search(r'totalRecord\s*:\s*(\d+)',html)
  group_match=re.search(r'groupSize\s*:\s*(\d+)',html)
  if not per_match or not total_match or not group_match:raise ValueError('pagination bounds absent')
  per_page=int(per_match[1]);total=int(total_match[1]);group_size=int(group_match[1])
  if not 1<=per_page<=100 or not 1<=group_size<=10:raise ValueError('pagination bounds invalid')
  wanted=min(total,self.max_pages*per_page);items=initial[:wanted]
  param_match=re.search(r'var\s+param_\d+\s*=\s*\{([^}]+)\}',html)
  params={}
  if param_match:
   for match in re.finditer(r"(\w+)\s*:\s*(?:'([^']*)'|(\d+))",param_match[1]):params[match[1]]=match[2] if match[2] is not None else match[3]
  if params.get('permissiontype') not in ('0',0):raise ValueError('column requires authorization or permission is unknown')
  while len(items)<wanted:
   start=len(items)+1;end=min(total,start+per_page*group_size-1)
   if not all(k in params for k in ('appid','webid','columnid','unitid','path')):raise ValueError('public paging parameters incomplete')
   # This is the public POST used by the site's own declared paginator.
   query=urlencode({'startrecord':start,'endrecord':end,'perpage':per_page,'columnid':params['columnid'],'unitid':params['unitid']})
   url=self.url+'/module/web/jpage/dataproxy.jsp?'+query;self._post_requests[url]=urlencode(params).encode('ascii')
   chunk=records(read(url))[:end-start+1]
   if not chunk:raise ValueError('unexpected empty pagination group')
   items.extend(chunk)
  urls=[];external=0
  for page_start in range(0,min(len(items),wanted),per_page):
   page_urls=[]
   for record in items[page_start:page_start+per_page]:
    dom=DOM();dom.feed(record)
    for node in dom.root.walk():
     if node.tag!='a' or not node.attrs.get('href'):continue
     url=urljoin(self.url,node.attrs['href'])
     if urlsplit(url).netloc!='cast1.cau.edu.cn':external+=1;continue
     try:self.identity(url)
     except ValueError:continue
     page_urls.append(url)
   self.last_pages.append(list(dict.fromkeys(page_urls)));urls.extend(page_urls)
  if wanted and not urls:raise ValueError('no recognized article identities in declared listing')
  self.coverage_summary='public listing '+self.listing+'; pages='+str(len(self.last_pages))+'; per_page='+str(per_page)+'; declared_total='+str(total)+'; unique_articles='+str(len(set(urls)))+'; external_links_unread='+str(external)+'; not full-site coverage'
  return list(dict.fromkeys(urls))
 def extract(self,body,base_url=None):
  dom=DOM();dom.feed(body.decode('utf-8'))
  meta={n.attrs.get('name','').lower():n.attrs.get('content') for n in dom.root.walk() if n.tag=='meta'}
  zoom=next((n for n in dom.root.walk() if n.attrs.get('id')=='zoom'),None)
  if not zoom or not zoom.closed or not meta.get('articletitle'):raise ValueError('recognized article template absent')
  base_url=base_url or self.url
  blocks=[];assets=[]
  def append(kind,text,rows=None):blocks.append({'block_id':'b'+str(len(blocks)+1),'type':kind,'text':text.strip(),'rows':rows or []})
  def collect(node):
   if node.tag in ('script','style'):return
   if node.tag=='table':
    rows=[]
    for tr in node.walk():
     if tr.tag=='tr':rows.append([c.text().strip() for c in tr.children if isinstance(c,Node) and c.tag in ('td','th')])
    append('table','\n'.join('\t'.join(r) for r in rows),rows);return
   if node.tag in ('p','h1','h2','h3','h4','li') and node.text().strip():append('heading' if node.tag.startswith('h') else 'paragraph',node.text());return
   if node.tag=='img':append('image',node.attrs.get('alt','')+' '+urljoin(base_url,node.attrs.get('src','')));return
   for child in node.children:
    if isinstance(child,Node):collect(child)
    elif child.strip():append('paragraph',child)
  collect(zoom)
  for node in zoom.walk():
   target=node.attrs.get('href') if node.tag=='a' else node.attrs.get('src') if node.tag=='img' else None
   if target:
    url=urljoin(base_url,target)
    if '/module/download/' in url or node.tag=='img' or re.search(r'\.(docx?|xlsx?|pdf|zip|rar|pptx?|txt)$',urlsplit(url).path,re.I):assets.append({'url':url,'name':node.text().strip() or node.attrs.get('alt') or url.rsplit('/',1)[-1]})
  if not blocks:raise ValueError('empty article body')
  raw=meta.get('pubdate');date=raw[:10] if raw and re.match(r'^\d{4}-\d{2}-\d{2}',raw) else None
  return {'schema_version':2,'title':meta['articletitle'],'published_at_raw':raw,'published_date':date,'blocks':blocks,'attachments':list({a['url']:a for a in assets}.values())}
