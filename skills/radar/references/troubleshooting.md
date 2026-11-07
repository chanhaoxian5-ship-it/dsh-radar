# 失败与恢复

1. 未收到写回执：先receipt查原operation_key，再原键原内容重试。不要换键猜成功。claim/heartbeat也遵守此规则，但回执中的旧epoch不代表仍有执行权。
2. source或analysis修订冲突：重新读取。需要修改请求时使用新操作键；已成功的采集不回滚。
3. 分析失败：run_items为analysis_failed/collected时复用原文和资源，仅读取并重试analysis。不要为模型失败重抓来源。
4. awaiting_executor：读run.errors；修复适配器/网络后retry_run（当前expected_epoch），再查状态。403、TLS失败、超时、缓存和模板缺失不等于撤回或没有变化。
5. 旧worker/旧会话：不得借用当前owner与epoch；有效租约占用时拒绝抢占，到期后重新claim恢复。
6. 人工覆盖：保留人工规则。权限不足时报告缺的具体权限/来源范围，不自扩权或读取其他角色token。
7. 重启：runs、items、回执、原文和对象均持久化；外部会话以真实身份重新查询接续。不要伪造会话链接。
8. 一致备份：使用radar.maintenance.backup（SQLite备份API及引用对象复制），不可只复制运行中的主db。restore要求新目录、校验对象哈希、更换service_epoch并撤销旧凭证。由本机操作者reissue原principal可恢复其回执；这不是Agent工具。消费者发现epoch改变必须重建视图。

原件和模型理解分开：程序验证结构与证据位置，不证明改写语义正确。永久删除没有开放，归档不清除历史证据。


## 上线联测清单（真实模型尚待验证）

使用独立DSH profile、独立数据目录、明确标注的固定通知。需要模型供应商/兼容接口地址、模型ID及该环境临时凭证；用户在环境自己的配置入口设置，不复制日常密钥、不写入报告。无模型时只验证工具、页面、服务，不把模拟执行器当真实Agent。

逐项保留回执ID与数据库读回：
1. 在明确真实会话读取Skill、精简contract、subscriptions/runs；按需读取目标方法Schema。工具缺失先查插件诊断，不编造结果。
2. 创建关注并读回版本化规则；启动固定样本运行，读取已保存evidence_read与资源状态。归档样本不是新鲜网页，失败附件不能标为已读。
3. 分别整理对象、事项、材料、日期原文/规范值、渠道与条件，每条事实引用准确证据位置；未读Word不能猜其材料要求。
4. 提交analysis，按receipt和message读回；制定review/复评/结束条件，读回plans。
5. 用户明确指定频率时提案，由owner页面确认；验证Agent覆盖被拒绝。Agent自报“已设为人工”不算通过。
6. 修改关注、归档、恢复；丢写响应按原operation_key查receipt及重试。分析失败只重试分析，不重抓原文掩盖失败。
7. 会话切换、超时重领后测试旧worker/旧分析拒绝；重启核对run、修订及回执，分析失败不能回滚原文。

## 依赖、启动与升级

Windows需要Python及requirements.txt，院网默认传输需要curl>=8.4。将依赖安装到配置指定的Python。页面“本机服务”可观察启动失败和重试。数据目录只用空目录或已归属雷达的目录；服务设置当前账户与SYSTEM访问权限。不要修改全局配置、放宽ACL或关闭TLS校验。

升级先停用旧插件的自有后台，再启用新包；backend_version_conflict时不杀未知后台。停用只停止本插件持有的子进程。403、TLS握手失败、超时、截断、正文模板异常及附件读取失败均记录抓取尝试，不能报fresh/complete。
