# Agent 操作手册：功能选择与组合

本手册用于通过聊天完成雷达业务操作。以下方法均指 `radar_call({"method":方法名,"args":参数对象})`，不是 shell 命令。每个写操作先读取该方法的实时 Schema；本文说明用途和顺序，不复制一份可能过时的完整字段定义。

## 开始与结束

1. `contract({include_schemas:false})` 确认实际版本、可见 methods 和 service_epoch；`contract({for_method:目标方法})` 获取该操作的 Schema。若方法不可见，停止该分支，报告缺失能力，不换 token 绕过。
2. `subscriptions({})` 定位用户指的关注；有多个同名或范围不同的结果时按来源/对象消歧。已有关注优先复用，不因换会话重复创建。按任务读取 `sources`、`plans`、`runs`、`processing_queue`。
3. 对所有写入保存 operation_key 和原 args；网络响应不确定时先 `receipt`，找不到回执再用原键原参数重试。版本冲突先重新读取；基于新版本重新决定的写入用新键，不能把不同内容塞进旧键。租约 epoch、实体 revision、message_revision、service_epoch 是四种不同身份，不互相替代。
4. 用对应查询确认实际效果。输出“完成了什么、仍等待什么、下次何时/下一步是什么”，必要时给出订阅或运行 id。排队只报告已排队；提交分析后才可报告已整理；规则提案未确认只报告待确认。

## 功能索引

“前读”获得身份/修订，“后读”确认结果。完整可用参数以实时 contract 为准。

| 功能/方法 | 用途、前读与后读 | 边界 |
|---|---|---|
| contract | 查询能力和目标方法 Schema | 不改变状态 |
| sources / source_save | 读来源，创建或修改来源登记后再读 sources | 登记 URL 不会生成、安装或授权可执行适配器 |
| subscriptions / subscription_save | 读关注，创建/修改规则、暂停、归档，再读 subscriptions | 新建 revision=0；已有对象需当前 revision；归档保留历史 |
| plans / discovery_plan | 读当前发现计划，改周期/下一次/状态，再读 plans | Agent 用 authority=agent；人工规则不能覆盖 |
| run_start | 对确切订阅发现新通知；后读 run/runs | 不改计划；跳过已有正文和附件；不等于自动调用模型 |
| recheck | 指定旧 message_id 强制读取来源；后读 run、message、diff | 用于确认旧消息改稿，不拿 run_start 代替 |
| runs / run | 找到任务、工作项、错误与 epoch | 运行状态和单条候选状态不能混用 |
| processing_queue | 按订阅读当前版本的候选和运行锚点 | 默认视图不是全部状态；需全部用 state=all；after 使用稳定 message_id |
| retry_run | 读 run，用当前 expected_epoch 接续未完成阶段；后读 run | 已保存原文复用；不能抢占运行；只重新排队 |
| claim / heartbeat | 领取运行，保存 owner/epoch/lease_until；续租 | seconds 是租约时长，不是采集频率；新会话重新领取，不能借旧 owner |
| queue_prepare | 有效租约下为确切 item/候选进入分析；检查 should_analyze | false 时跳过分析；不能直接改队列数据库 |
| messages / message | 全局原文列表与固定消息的版本、资源、分析 | 全局原文不是某订阅的正式消息；只在当前授权范围查询 |
| evidence_read | 用 run_id/observation_id/可空 resource_version_id 分页读取证据 | 固定版本；不接收本机路径；原始字节不等于已理解 |
| analysis | 读取原文、资源和当前分析 revision 后提交 Schema 2 结果；读回 message/队列 | owner 来自 claim；不能把 quote、附件内容或读取状态编造出来 |
| queue_fail | 确定分析失败时记录固定 item/候选与原因；读回队列 | 写入结果未知先查回执，不能直接标失败覆盖可能成功的 analysis |
| finish | 所有工作项结束后按实际结果结束运行；后读 run | 全部完成才 succeeded；partial/failed 不冒称成功 |
| cancel | 用户要停止该运行时取消，读回 run | 不删原文、不等于暂停关注或永久停采 |
| formal_messages | 按订阅读 relevant/uncertain 的正式消息 | irrelevant 不在正式列表；uncertain 必须保留不确定性 |
| diff | 指定 message_id 与 before/after 正文修订，查看正文/资源差异 | 无变化与抓取失败不是同一结论 |
| review | 读 plans 和同订阅 analysis，设指定消息回查/复评/截止，再读 plans | 有固定 analysis_id 才能建立；是否关注不等于每篇都回查 |
| rule_proposal_create / rule_proposals | 为受保护人工规则创建提案，再读提案状态 | 不含伪造的 user 权限；提案不是已生效规则 |
| rule_proposal_decide | 本人页面确认/拒绝提案 | owner 功能；Agent 不从 shell 代调或读取 owner.token |
| categories / category_save | 读分类，再创建层级或修改父分类；读回 categories | 同订阅；parent_id 可空；禁止父子环 |
| events / event_save / event | 读事件，建/改事件，再读事件详情/时间线 | 暂停事件不是暂停订阅采集或所有回查；暂停/归档后恢复需本人 |
| event_message_save | 读事件和当前分析，将通知以角色和理由关联；读回 event | 同订阅、固定 analysis_id；active=false 撤销关联不删原文 |
| queue_decide | 有明确依据时暂不收录，读回队列 | 用候选当前 revision；reopen 只限本人，不靠改订阅绕过 |
| receipt / changes | 查原写回执；用 service_epoch+游标同步变化 | receipt 属于原认证主体；epoch 改变需重建消费者视图 |
| runtime | 检查服务运行信息（若当前契约可见） | 不证明网站采集/模型整理成功 |

`capture`、`discover` 是可信采集执行链路；普通聊天 Agent 不伪造网站观测或自行提交 observation。`tick`、`session_dispatch` 属于调度/宿主桥接，不是聊天端要循环调用的接口。页面 backend_diagnose/backend_repair 是 owner RPC，不在 radar_call 的业务方法中；工具里不可见就说明需要本人页面自检，不虚构 radar_call 同名能力。

## 组合任务 A：建立“关注毕设”并首次采集整理

1. sources + subscriptions 查已有来源/关注。来源未适配先走 source-onboarding → crawl4ai-engine；只登记来源不能宣布可抓。
2. 需要新关注时 subscription_save，规则包含 schema_version=2、purpose、audiences、include、exclude、source_ids，id 稳定且不复用别的关注；读回 subscriptions。
3. 需要自动发现时查 plans，用 discovery_plan 建立 Agent 自主计划；用户要求受保护人工频率时走下方 C。只要“现在查一次”则不创建周期。
4. run_start(trigger=manual)，保存返回 run_id，后查 run。服务异步采集；读到 queued/running 时等待后再次查询，不重复创建新操作键启动一串任务。
5. 到 awaiting_analysis 时按 B 接力；到 awaiting_executor 时读 errors，解决可解决的采集问题再 retry_run。不得把失败说成没有通知。
6. 整理完成后按需求建分类/事件并关联（D），再决定每条通知是否需要 review；不需要回查就不建计划。

## 组合任务 B：把待整理变成正式消息

1. processing_queue 查目标订阅的 pending/retry，必要时按 after 分页；对每条读取运行锚点对应的 run，确认 item.message_id/message_revision 匹配。没有可续接运行时可 run_start 复用已有候选建立工作；不要只凭旧锚点假定能 claim。
2. awaiting_analysis/awaiting_executor/failed/partial 需要续接时，先 retry_run(当前 expected_epoch)，再查 run 并 claim。有效租约仍被占用时等待或报告，不抢占。
3. claim 后对 collected item 调用 queue_prepare；should_analyze=false 跳过该项。精确读取对应修订的原文和 resource_versions，读取 content-processing；analysis.result 必须有 schema_version=2 和契约要求的全部字段。
4. 提交 analysis 用 claim.owner、epoch、item_id、来源 message_revision、当前分析 expected_revision。读回 message/processing_queue：relevant/uncertain → processed 并出现在 formal_messages；irrelevant → not_included，不进入正式列表。
5. 确定失败用 queue_fail，保留原件；有效租约内以实际 partial/failed 结束。全部 item 完成才 finish succeeded，再读 run。中断后重新查询并领取，旧 epoch 不再提交。

先看 run/items，不把每条队列项当独立新采集任务。已处理项不重复分析；new message revision 与队列 revision 不同含义。

## 组合任务 C：修改频率、暂停、恢复、复查

- “以后每两天找一次新通知”：plans → discovery_plan(interval_seconds=172800，其他参数按契约) → plans。Agent 自主计划 authority=agent；受保护人工规则用 rule_proposal_create(target_method=discovery_plan，payload 不含 authority/operation_key) → rule_proposals，等待本人页面确认后再查 plans。已有 user 规则不能直接改写。
- “先别自动抓，偶尔我叫你查”：subscription_save(status=paused，保留原规则) → subscriptions。仍允许明确 manual 运行。仅停发现计划不等于停止旧消息 review；要停某条回查修改该 review。暂停不会自动撤销已在执行的全部工作，要停止某次执行另用 cancel 并读回。
- “恢复关注”：读当前订阅和 plans，再决定恢复 subscription 或 discovery/review 的哪一层，不能只改一个状态后声称所有自动任务恢复。
- “看看这份公示改没改”：recheck 指定通知 → run → message/diff；有新证据再整理。无需改长期计划；抓取未确认就保留未知。
- “这份公示接下来三天持续留意”：analysis 固定依据 → review，设置间隔、下次、reevaluate_at、end_at；到期后停止该计划。复评请求需要 Agent 判断，不代表系统已自动调用模型。
- 所有调度时间按 UTC Z 格式提交，向用户报告时换算其时区。间隔计划从成功完成后顺延，不承诺固定每天几点；30 秒自检、每分钟 tick、工作线程轮询都不是爬站周期。当前活动运行等待分析可能挡住同关注下一次发现，须报告实际阻塞。

## 组合任务 D：同一通知加入奖学金事件和时间线

categories → category_save 建“奖学金/某奖学金”层级；events → event_save 建具体年度评选事件。读消息当前同订阅 analysis_id，再 event_message_save，role 仅用 application/supplement/correction/announcement/other，附依据与理由；后读 event 检查 timeline/current_requirements。

原文只有一份，关联可多处复用；不同订阅需要各自的分析与状态，不能跨订阅借 analysis_id。更正、补充不自动抹去旧要求：冲突并列，检查过期依据；不据通知推断用户已经提交或完成。撤销关联 active=false 保留证据。

## 组合任务 E：失败与未知结果

- 写响应丢失：先 receipt，原键原内容重试，不创建新键重复写。换会话后查实体/run 恢复上下文，不读取旧会话凭据。
- 分析失败：retry_run → claim → queue_prepare → analysis；不因模型失败重抓已保存正文。
- 抓取失败：读 run.errors，按 troubleshooting 查来源/网络；未确认恢复前不循环触发站点。
- 字段校验失败：重新获取 contract.for_method；修正缺字段、类型/UTC 格式和证据位置。业务对象/分析/队列的 revision 分别从对应查询取值，不猜测。
- 版本冲突、租约过期、人工规则保护：重新读状态并决定；不可通过提高 expected_revision、修改 authority 或借 owner 来绕过。

## 能力边界要告诉用户

持久待整理不会自动过期删除，retry 不是无限自动重试；当前没有可配置的队列 TTL/模型并发度/自动模型桥。服务自愈只恢复后端运行，不重放业务写入，不证明原站无更新或模型已整理。新能力缺接口时明确说需要开发，不能靠描述一个参数声称实现。
