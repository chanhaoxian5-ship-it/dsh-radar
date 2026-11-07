"""Closed JSON request schemas and validation helpers for Radar operations."""
from jsonschema import Draft202012Validator, FormatChecker

def obj(properties, required=None): return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}
def string(limit=2000): return {'type':'string','minLength':1,'maxLength':limit}
def enum(*values): return {'enum':list(values)}
integer={'type':'integer','minimum':0}; key=string(200); ident=string(200)
lease={'run_id':ident,'epoch':{'type':'integer','minimum':1}}
evidence=obj({'block_id':ident,'quote':string(10000)})
item=obj({'action':string(10000),'evidence':{'type':'array','minItems':1,'maxItems':100,'items':evidence}})
result=obj({'summary':string(20000),'items':{'type':'array','maxItems':200,'items':item},'decision':enum('relevant','irrelevant','uncertain'),'reason':string(10000),'uncertainties':{'type':'array','maxItems':100,'items':string(10000)}})
SCHEMAS={
 'contract':obj({}), 'subscriptions':obj({}),
 'receipt':obj({'operation_key':key}),
 'messages':obj({'after':ident,'limit':{'type':'integer','minimum':1,'maximum':100}},[]),
 'message':obj({'message_id':ident}), 'run':obj({'run_id':ident}),
 'changes':obj({'service_epoch':ident,'after':integer}),
 'subscription_save':obj({'operation_key':key,'id':ident,'name':string(500),'rules':{'type':'object','maxProperties':50},'status':enum('active','paused','archived'),'expected_revision':integer}),
 'source_save':obj({'operation_key':key,'id':ident,'name':string(500),'root_url':{'type':'string','format':'uri','maxLength':2000}}),
 'run_start':obj({'operation_key':key,'subscription_id':ident,'trigger':enum('manual','scheduled','event')}),
 'claim':obj({'run_id':ident,'seconds':{'type':'integer','minimum':1,'maximum':3600}},['run_id']),
 'heartbeat':obj({**lease,'seconds':{'type':'integer','minimum':1,'maximum':3600}},list(lease)),
 'discover':obj({**lease,'source_id':ident,'targets':{'type':'array','maxItems':500,'items':string(2000)}}),
 'cancel':obj({'operation_key':key,'run_id':ident}),
 'finish':obj({'operation_key':key,**lease,'status':enum('succeeded','partial','failed')}),
 'analysis':obj({'operation_key':key,**lease,'item_id':ident,'owner':ident,'message_id':ident,'message_revision':{'type':'integer','minimum':1},'expected_revision':integer,'result':result}),
 'review':obj({'operation_key':key,'subscription_id':ident,'message_id':ident,'expected_revision':integer,'authority':enum('agent','user'),'state':enum('active','paused','ended'),'interval_seconds':{'type':['integer','null'],'minimum':3600,'maximum':31536000},'next_check_at':{'type':['string','null'],'format':'date-time'},'reason':string(10000)}),
 'observation':obj({'operation_key':key,**lease,'item_id':ident,'owner':ident,'external_key':ident,'freshness':enum('fresh','cached','unknown'),'body_status':enum('complete','partial','failed'),'asset_status':enum('complete','partial','unknown'),'http_status':{'type':['integer','null'],'minimum':100,'maximum':599},'content':{'oneOf':[{'type':'null'},obj({'title':string(2000),'blocks':{'type':'array','minItems':1,'maxItems':2000,'items':obj({'block_id':ident,'type':enum('paragraph','heading','table','image'),'text':{'type':'string','maxLength':100000}})},'attachments':{'type':'array','maxItems':100,'items':obj({'url':{'type':'string','format':'uri','maxLength':2000},'name':string(1000)})}})]},'extractor_version':string(100),'error':{'type':['string','null'],'maxLength':2000}})
}

def validate(method,args):
 if method not in SCHEMAS: raise ValueError('unknown method')
 errors=sorted(Draft202012Validator(SCHEMAS[method],format_checker=FormatChecker()).iter_errors(args),key=lambda e:str(e.path))
 if errors: raise ValueError('schema: '+errors[0].message[:500])
