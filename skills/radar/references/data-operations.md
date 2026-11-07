# 数据操作（业务Schema 2）

先 contract。读取：sources、subscriptions、messages、message、runs、run、plans、changes、receipt。evidence_read以run_id、observation_id及可空resource_version_id读取固定证据，offset/length有上限，不接受本机文件路径。changes绑定service_epoch，恢复后epoch改变必须重建消费者视图。

source_save：operation_key、id、name、root_url、expected_revision。新来源expected_revision=0；已有来源先读配置/修订，发生冲突重新读取再用新操作键提交修正内容。有消息历史的来源不允许静默更换origin。只有获准的来源才能采集；新增可执行适配器不通过JSON安装。

subscription_save 的完整关注规则示例（示例不是已创建任务）：

```json
{"operation_key":"example-create-subscription","id":"thesis-example","name":"本科毕设通知","rules":{"schema_version":2,"purpose":"关注本人适用的毕设安排，分别列出学生、班级与负责人的要求","audiences":["2027届本科生"],"include":["毕业论文","选题","开题"],"exclude":[],"source_ids":["cau-cast"]},"status":"active","expected_revision":0}
```

先创建并核实source_ids中的来源。修改与归档需当前revision；暂停阻止自动运行，仍允许手动检查。临时运行不改订阅规则。

写成功后查询对应实体和确切版本。receipt只返回当前认证主体的原回执；同会话重启可恢复原回执，新会话通过runs和工作项接续，不能冒充旧会话。归档保留来源修订、对象和历史；永久删除未实现。
