# 运行与会话接力

run_start：operation_key、subscription_id、trigger（manual/scheduled/event）。运行身份和阶段状态保存在数据库。同订阅活动运行复用；自动计划到期先排队，重叠需求保留到后续tick，不因会话存在就记成功。临时手动请求不修改订阅。

本机服务会领取排队采集；成功后状态awaiting_analysis，缺适配器或来源失败为awaiting_executor且有持久错误。不要把等待、失败或空响应说成“无更新”。新Agent先runs查待处理运行，run查items和errors。

整理接力：先查询 run，awaiting_analysis/awaiting_executor/failed/partial 需 retry_run 重新排队后再 claim；queued 可直接 claim。claim带operation_key、run_id（可指定seconds），保存返回owner/epoch/lease_until；随后读取快照并analysis；耗时工作用heartbeat续租，每次续租有自己的稳定operation_key。只有所有工作项均已整理，才finish succeeded。取消用cancel；awaiting_executor/failed/partial可用retry_run和expected_epoch明确重试。

V0.2待整理接力另见queue-processing：先按订阅读取processing_queue。只有候选的当前message_revision与run item完全一致才继续；领取run后，以owner/epoch调用queue_prepare，再读取该修订原文、证据和资源并提交analysis。should_analyze=false时不得重复整理。提交失败时queue_fail记录固定候选及理由；完成所有工作项后finish，否则结束为partial或等待租约超时恢复。不要重新采集已保存候选。

claim或heartbeat响应丢失，查询原回执/重试原键得到原epoch与到期时间；它们不是永久授权，提交前仍需核对当前run。旧会话不能借用新会话的owner；租约失效后停止提交。

服务器内的tick执行已经持久化的发现/回查计划，开机服务重启后补查到期项。它不是第二套Agent运行时：整理由外部DSH会话承担。真实DSH模型执行与自动创建会话仍待验收，不宣称已自动安排模型。需要原生DSH自动化时，先发现当前宿主真实工具及契约，绑定确切subscription_id、来源、权限和运行回查规则；没有实际可调用接口时报告限制，不编造命令、不启用真实定时任务。


V0.2发现与回查：`run_start(operation_key,subscription_id,trigger)`只发现新消息；manual/scheduled/event均跳过已保存正文和附件，已有pending/retry候选仍从持久证据整理。用户明确指定旧消息重查用`recheck(operation_key,subscription_id,message_id)`；到期review同样形成显式目标。无变化不增加来源修订、不重复语义分析，策略复评仍独立执行。排队但尚无工作项时可合并发现/回查；已有执行或分析重试时显式重查返回冲突，须待该运行结束再重查，不能将其当已执行。

`retry_run(operation_key,run_id,expected_epoch)`支持queued、awaiting_analysis、awaiting_executor、failed、partial；会保留已采集工作项，重新领取租约后只接续未完成阶段。运行中不能抢占；响应不确定先查回执。retry_run只排队，服务不会自动调用模型。候选为not_included时只有user可reopen，随后发现运行会复用原件生成整理工作。页面“重试整理”同样只排队；需外部会话按queue-processing完成分析。


## 调整频率与解释当前状态

用户问“多久抓一次/能否修改/为什么没整理”时，先查 subscriptions、plans、runs，报告该订阅实际状态、发现间隔、next_at、规则来源、最近运行和阻塞原因。没有发现计划时不能声称存在默认周期。服务每分钟 tick 只检查到期计划；采集线程的 .25 秒轮询和本机 30 秒健康检查均不是抓站频率。

发现间隔可通过 discovery_plan 修改（3600–31536000 秒）。expected_revision 来自当前计划，无计划为 0；完整字段见 contract.for_method。Agent 自主策略用 authority=agent，已有 authority=user 时不可覆盖。用户明确要求成为受保护人工规则时走 rule_proposal_create → 页面确认，或本人直接在“关注设置”表单保存。提交后读回 plans，不能只报告写入成功。改变频率不改变原文、队列和已排运行。

当前是按成功完成后顺延的间隔调度，不是固定每天某时刻；finish succeeded 后 next_at 才推进。awaiting_analysis/awaiting_executor 等活动运行会阻挡同订阅新的定时运行；应明确告知用户，接续处理或根据用户意图取消，不能宣称爬虫仍按计划持续发现。取消保留已存证据；下一次 tick 可重新排队。

当前没有已验收的自动模型整理桥。run_start/retry_run 和页面重试均不代表调用模型；要让 DSH 自动化会话接手，需在宿主实际支持的自动化工具中绑定订阅和接力流程；宿主接口未确认前不能编造定时命令或宣称启用。
