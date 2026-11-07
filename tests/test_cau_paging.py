import unittest
from radar.sources.cau import CauAdapter
from radar.backend import Response
class PublicPagingTest(unittest.TestCase):
 def listing(self,permission=0):
  rows=''.join('<record><![CDATA[<a href="/art/2026/1/1/art_1_'+str(i)+'.html">notice</a>]]></record>' for i in range(1,31))
  return ('<script type="text/xml"><datastore>'+rows+'</datastore></script><script>perPage:10,totalRecord:80,groupSize:3;var param_1={appid:1,webid:5,columnid:44372,unitid:72984,path:\'/\',permissiontype:'+str(permission)+'}</script>').encode()
 def test_public_post_group_deduplicates_sentinel_and_bounds_coverage(self):
  a=CauAdapter(max_pages=4);calls=[]
  def fetch(url):
   calls.append(url)
   if len(calls)==1:return Response(200,url,self.listing(),{})
   self.assertIn('startrecord=31',url);self.assertIn('permissiontype=0',a._post_requests[url].decode())
   xml='<datastore>'+''.join('<record><![CDATA[<a href="/art/2026/1/1/art_1_'+str(i)+'.html">notice</a>]]></record>' for i in range(31,62))+'</datastore>'
   return Response(200,url,xml.encode(),{})
  urls=a.discover(fetch);self.assertEqual(len(urls),40);self.assertEqual(len(set(urls)),40);self.assertEqual(len(calls),2);self.assertEqual(len(a.last_pages),4)
 def test_permissionful_column_not_collected(self):
  a=CauAdapter()
  with self.assertRaises(ValueError):a.discover(lambda url:Response(200,url,self.listing(1),{}))
 def test_relative_asset_resolves_against_article_not_root(self):
  body=b'<meta name="ArticleTitle" content="test"><div id="zoom"><p>text</p><a href="form.doc">form</a></div>'
  result=CauAdapter().extract(body,base_url='https://cast1.cau.edu.cn/art/2026/1/1/art_1_2.html')
  self.assertEqual(result['attachments'][0]['url'],'https://cast1.cau.edu.cn/art/2026/1/1/form.doc')
