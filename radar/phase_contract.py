"""Compose the public request contract from core and phase-specific schemas."""
from .schemas import SCHEMAS, obj,ident,key,enum,string,integer,lease
from .business import RESULT,RULES,nullable
SCHEMAS['subscription_save']['properties']['rules']=RULES
SCHEMAS['analysis']['properties']['result']=RESULT
for method in ('claim','heartbeat','discover'):
 SCHEMAS[method]['properties']['operation_key']=key;SCHEMAS[method]['required'].append('operation_key')
SCHEMAS['discover']['properties']['coverage']=string(2000);SCHEMAS['discover']['required'].append('coverage')
SCHEMAS['capture']=obj({'operation_key':key,**lease,'item_id':ident})
SCHEMAS['observation']={'not':{}}
for name in ('reevaluate_at','end_at'):
 SCHEMAS['review']['properties'][name]=nullable({'type':'string','format':'date-time'});SCHEMAS['review']['required'].append(name)
SCHEMAS['review']['properties']['analysis_id']=ident;SCHEMAS['review']['required'].append('analysis_id')
SCHEMAS['review']['allOf']=[{'if':{'properties':{'state':{'const':'active'}}},'then':{'properties':{'interval_seconds':{'type':'integer','minimum':3600,'maximum':31536000},'next_check_at':{'type':'string','format':'date-time'}}}}]
SCHEMAS['discovery_plan']=obj({'operation_key':key,'subscription_id':ident,'expected_revision':integer,'interval_seconds':{'type':'integer','minimum':3600,'maximum':31536000},'next_at':{'type':'string','format':'date-time'},'state':enum('active','paused','ended'),'authority':enum('user','agent'),'reason':string(10000)})
SCHEMAS['tick']=obj({'operation_key':key,'at':{'type':'string','format':'date-time'}})
SCHEMAS['plans']=obj({'subscription_id':ident})
SCHEMAS['diff']=obj({'message_id':ident,'before':{'type':'integer','minimum':1},'after':{'type':'integer','minimum':1}})

# Persisted policy clocks are canonical UTC, so SQLite ordering is chronological.
def utc_clocks(schema):
 if isinstance(schema,dict):
  if schema.get('format')=='date-time':schema['pattern']=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$'
  for value in schema.values():utc_clocks(value)
 elif isinstance(schema,list):
  for value in schema:utc_clocks(value)
for schema in SCHEMAS.values():utc_clocks(schema)

SCHEMAS['retry_run']=obj({'operation_key':key,'run_id':ident,'expected_epoch':integer})

SCHEMAS['evidence_read']=obj({'run_id':ident,'observation_id':ident,'resource_version_id':nullable(ident),'offset':integer,'length':{'type':'integer','minimum':1,'maximum':65536}})

SCHEMAS['source_save']['properties']['expected_revision']=integer
SCHEMAS['source_save']['required'].append('expected_revision')

SCHEMAS['runs']=obj({'subscription_id':ident,'limit':{'type':'integer','minimum':1,'maximum':100}},[])
SCHEMAS['sources']=obj({})

SCHEMAS['session_dispatch']=obj({'session_id':ident,'method':string(100),'args':{'type':'object'}})

import copy
proposal_cases=[]
for method in ('review','discovery_plan'):
    payload=copy.deepcopy(SCHEMAS[method]);payload['properties'].pop('operation_key');payload['properties'].pop('authority');payload['required']=[k for k in payload['required'] if k not in ('operation_key','authority')]
    proposal_cases.append(obj({'operation_key':key,'target_method':{'const':method},'payload':payload,'user_intent':string(10000)}))
SCHEMAS['rule_proposal_create']={'oneOf':proposal_cases}
SCHEMAS['rule_proposals']=obj({'subscription_id':ident})
SCHEMAS['rule_proposal_decide']=obj({'operation_key':{**key,'maxLength':150},'proposal_id':ident,'expected_proposal_revision':integer,'decision':enum('approve','reject')})

SCHEMAS['source_save']['properties']['options']=obj({'listing_url':{'type':'string','format':'uri','maxLength':2000},'max_pages':{'type':'integer','minimum':1,'maximum':10},'transport':enum('curl','urllib')},[])

SCHEMAS['runtime']=obj({})
SCHEMAS['run_start']=obj({'operation_key':key,'subscription_id':ident,'trigger':enum('manual','scheduled','event')})
SCHEMAS['recheck']=obj({'operation_key':key,'subscription_id':ident,'message_id':ident})

SCHEMAS['contract']=obj({'include_schemas':{'type':'boolean'},'for_method':string(100)},[])
