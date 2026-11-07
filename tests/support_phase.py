"""All content here is SYNTHETIC; it does not reproduce a real notice."""
import json
from radar.backend import Response

RULES={'schema_version':2,'purpose':'synthetic thesis workflow','audiences':['2027届本科生'],'include':['毕业设计'],'exclude':[],'source_ids':['c']}
class FixtureAdapter:
 synthetic=True;version='synthetic-json-1'
 def __init__(self):self.version_number=1;self.asset=b'form-v1';self.fail_asset=False;self.calls=[];self.cached=False
 def discover(self,fetch=None):return ['https://fixture.invalid/notice/1']
 def identity(self,url):return url.rsplit('/',1)[-1]
 def content(self):
  text='11月20日前由学生提交开题报告，导师签字后交至教学办公室。' if self.version_number==1 else '11月25日前由学生提交开题报告，导师签字后交至教学办公室。'
  return {'schema_version':2,'title':'合成：2027届毕业设计开题通知','published_at_raw':'2026-10-13','published_date':'2026-10-13','blocks':[{'block_id':'b1','type':'paragraph','text':text,'rows':[]}],'attachments':[{'url':'https://fixture.invalid/form.txt','name':'合成申请表'}]}
 def fetch(self,url):
  self.calls.append(url)
  if url.endswith('form.txt'):return Response(403 if self.fail_asset else 200,url,b'' if self.fail_asset else self.asset,{'content-type':'text/plain'})
  return Response(200,url,json.dumps(self.content(),ensure_ascii=False).encode(),{'age':'20' if self.cached else '0'})
 def extract(self,body):return json.loads(body)

def analyze(snapshot,item,subscription):
 mid=item['message_id'];rev=item['message_revision'];content=json.loads(next(r['content'] for r in snapshot['revisions'] if r['revision']==rev));quote=content['blocks'][0]['text'];ev={'message_id':mid,'message_revision':rev,'block_id':'b1','resource_version_id':None,'row':None,'column':None,'quote':quote}
 resources=list({r['url']:r for r in snapshot['resources'] if r['message_revision']==rev}.values())
 return {'schema_version':2,'summary':'合成：学生提交开题材料','audiences':['2027届本科生'],'items':[{'id':'task-1','action':'提交开题报告','actors':['学生'],'materials':[{'name':'开题报告','conditions':['导师签字'],'resource_version_id':None,'evidence':[ev]}],'dates':[{'raw':'11月20日前' if '20' in quote else '11月25日前','date':None,'datetime':None,'interpretation':'本段未明确年份，不据发布日期补造'}],'channels':['教学办公室'],'conditions':['导师签字'],'evidence':[ev]}],'decision':'relevant','reason':'合成关注任务匹配','uncertainties':['日期年份待确认'],'resources':[{'resource_version_id':r['id'],'url':r['url'],'name':r['name'],'status':'read' if r['status']=='read' else 'failed','interpretation':None} for r in resources]}
