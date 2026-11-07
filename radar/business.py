"""Version 2 business contracts, shared by API and persistence boundary."""
from .schemas import obj, string, enum, ident, integer
from jsonschema import Draft202012Validator, FormatChecker

def array(item, maximum=200): return {'type':'array','items':item,'maxItems':maximum}
def nullable(schema): return {'anyOf':[schema,{'type':'null'}]}
DATE=obj({'raw':nullable(string()),'date':nullable({'type':'string','format':'date'}),'datetime':nullable({'type':'string','format':'date-time'}),'interpretation':nullable(string())})
DATE['allOf']=[{'if':{'anyOf':[{'properties':{'date':{'type':'string'}}},{'properties':{'datetime':{'type':'string'}}}]},'then':{'properties':{'raw':string()}}}]
DATE['not']={'properties':{'date':{'type':'string'},'datetime':{'type':'string'}},'required':['date','datetime']}
EVIDENCE=obj({'message_id':ident,'message_revision':{'type':'integer','minimum':1},'block_id':nullable(ident),'resource_version_id':nullable(ident),'row':nullable(integer),'column':nullable(integer),'quote':string(10000)})
EVIDENCE['oneOf']=[{'properties':{'block_id':ident,'resource_version_id':{'type':'null'}}},{'properties':{'block_id':{'type':'null'},'resource_version_id':ident,'row':{'type':'null'},'column':{'type':'null'}}}]
MATERIAL=obj({'name':string(),'conditions':array(string()),'resource_version_id':nullable(ident),'evidence':{**array(EVIDENCE),'minItems':1}})
ITEM=obj({'id':ident,'action':string(10000),'actors':array(string()),'materials':array(MATERIAL),'dates':array(DATE),'channels':array(string()),'conditions':array(string()),'evidence':{**array(EVIDENCE),'minItems':1}})
RESULT=obj({'schema_version':{'const':2},'summary':string(20000),'audiences':array(string()),'items':array(ITEM),'decision':enum('relevant','irrelevant','uncertain'),'reason':string(10000),'uncertainties':array(string(10000)),'resources':array(obj({'resource_version_id':nullable(ident),'url':{'type':'string','format':'uri','maxLength':2000},'name':string(1000),'status':enum('read','unread','failed'),'interpretation':nullable(string())}))})
RESULT['properties']['resources']['items']['allOf']=[{'if':{'properties':{'resource_version_id':{'type':'null'}}},'then':{'properties':{'status':{'const':'unread'}}}}]
RULES=obj({'schema_version':{'const':2},'purpose':string(10000),'audiences':array(string()),'include':array(string()),'exclude':array(string()),'source_ids':{'type':'array','uniqueItems':True,'maxItems':50,'items':ident}})
BLOCK=obj({'block_id':ident,'type':enum('paragraph','heading','table','image'),'text':{'type':'string','maxLength':100000},'rows':array(array({'type':'string','maxLength':10000},100),500)})
CONTENT=obj({'schema_version':{'const':2},'title':string(2000),'published_at_raw':nullable(string()),'published_date':nullable({'type':'string','format':'date'}),'blocks':{**array(BLOCK,2000),'minItems':1},'attachments':array(obj({'url':{'type':'string','format':'uri','maxLength':2000},'name':string(1000)}),100)})

def validate_business(schema,value):
 errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(value))
 if errors: raise ValueError('business schema: '+errors[0].message[:500])
