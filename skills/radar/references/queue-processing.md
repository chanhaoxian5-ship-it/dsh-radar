# 待整理队列与恢复

`processing_queue`按订阅返回当前消息版本的持久处理项，状态包括pending、processing、retry、processed、not_included，并返回标题、原发布时间、来源修订、重试数、策略版本、理由和最新运行锚点。默认最多50条；用稳定message_id作为after，limit最大100，并可按state过滤。候选按订阅隔离；同一通知在不同订阅有独立整理状态。

新采集正文成功后，候选与消息修订在同一数据库事务创建。分析提交同时将队列变为processed；分析decision=irrelevant时保留理由并进入not_included，不影响其他订阅。抓取失败不建立伪造正文候选；部分/缓存响应不会替换当前可靠修订。

Agent整理流程：

1. 查询 `runs`，再查目标 `run` 与其items/errors；将该工作项确切message_id、message_revision与队列候选对齐。
2. awaiting_analysis/awaiting_executor/failed/partial 的 run 先用当前 expected_epoch 调用 retry_run，确认重新排队后再 claim；queued 可直接 claim，有效运行租约不能抢占。保存返回的owner、epoch和lease_until，响应不确定时复用原operation_key。
3. 对每个采集完成的工作项调用 `queue_prepare`。服务端校验订阅、来源快照、当前修订和执行租约；`should_analyze=false`表示已有当前分析或已暂不收录，不要重复生成。
4. 读取确切message快照和附件版本，按content-processing处理；分析提交要用claim返回owner与同一epoch。
5. 如果分析确定失败，可用 `queue_fail` 记录失败理由，再以partial结束该run。工具/进程中断后租约恢复会把processing转回retry；只重试整理，不重复采集。完成所有run items后才finish succeeded。

每次进入processing时，服务记录内置Skill与references文件集的哈希，作为提示版本来源标识；这只能证明插件包内的指纹，不证明模型实际加载或遵循Skill。整理结果仍需独立复核。

`queue_decide`的 `not_included` 需要理由，并固定订阅/修订/策略版本；Agent不能自行reopen。本人确认后才能reopen，OCC修订冲突须先重新读取再提交。归档与暂不收录均不删除原文、分析或历史；新来源修订生成独立待整理项，旧总结仍可读但必须标为过期。


## 生命周期与可调整边界

采集成功生成 pending（持久待整理）；Agent claim 后 queue_prepare 才进入 processing；analysis 成功进入 processed，判为 irrelevant 进入 not_included；明确失败或中断恢复进入 retry。原文新修订会产生新的待整理项并标记旧总结过期。无正文变化不应重复分析，附件变化按已实现资源变更逻辑重新待整理。

pending/retry 没有自动到期删除时间，重启继续保留。retry 是等待明确重试，不是后台无限重试；not_included 只能由本人重新纳入。队列的最大保留天数、自动丢弃、固定次数后删除、自动调用模型及整理并发度，当前没有设置接口，不得假装可调。要改变它们需要开发能力，而非写一个不存在的参数。

可修改的是发现周期/暂停（discovery_plan）、指定通知的回查周期/复评/截止（review，需固定 analysis_id），以及用户重新纳入；租约 seconds 是运行占用时间，不是爬站周期。用户问队列为何停住时，给出运行 id、状态、错误和实际下一步；成功与否以持久状态读回为准。
