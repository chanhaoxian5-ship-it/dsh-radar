# 数据操作（业务Schema 2）

先 contract。读取：sources、subscriptions、messages、message、runs、run、plans、changes、receipt。evidence_read以run_id、observation_id及可空resource_version_id读取固定证据，offset/length有上限，不接受本机文件路径。changes绑定service_epoch，恢复后epoch改变必须重建消费者视图。

source_save：operation_key、id、name、root_url、expected_revision。新来源expected_revision=0；已有来源先读配置/修订，发生冲突重新读取再用新操作键提交修正内容。有消息历史的来源不允许静默更换origin。只有获准的来源才能采集；新增可执行适配器不通过JSON安装。

subscription_save 的完整关注规则示例（示例不是已创建任务）：

```json
{"operation_key":"example-create-subscription","id":"thesis-example","name":"本科毕设通知","rules":{"schema_version":2,"purpose":"关注本人适用的毕设安排，分别列出学生、班级与负责人的要求","audiences":["2027届本科生"],"include":["毕业论文","选题","开题"],"exclude":[],"source_ids":["cau-cast"]},"status":"active","expected_revision":0}
```

先创建并核实source_ids中的来源。修改与归档需当前revision；暂停阻止自动运行，仍允许手动检查。临时运行不改订阅规则。

写成功后查询对应实体和确切版本。receipt只返回当前认证主体的原回执；同会话重启可恢复原回执，新会话通过runs和工作项接续，不能冒充旧会话。归档保留来源修订、对象和历史；永久删除未实现。


V0.2读模型：formal_messages(subscription_id,limit?)只返回本订阅relevant/uncertain投影；processing_queue(subscription_id,after?,limit?,state?)返回持久候选。正式列表原发布时间降序、未知最后，同日期按message_id稳定排序。发现更新时间为本地观察，不冒称网站修改时间。plans包含最近持久发现范围、范围保存时间及最近成功运行时间，不保证全站或全部历史更新。

分类categories/category_save、事件events/event/event_save均按subscription_id隔离；父分类禁止环。event_message_save固定event_id、message_id、analysis_id、role、reason、active、expected_revision及operation_key。analysis_id必须属于同消息同订阅；撤销active=false保留共享原文。事件暂停/归档后只有user可恢复。事件timeline按原发布时间正序、未知最后；current_requirements保留各来源分析依据，冲突并列、过期标注，来源进展无法推定时unknown，personal_completion=null。
