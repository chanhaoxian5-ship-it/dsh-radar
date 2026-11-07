# DSH 消息雷达

独立的 DeepSeek Harness 插件：关注公开通知，保存原文与附件版本，由 Agent 按 Skill 整理事项、材料和日期，安排回查，并在 DSH 内查看消息与变化。

当前版本 **0.1.0-alpha.8**；验证宿主 **DSH 0.2.2-rc.1 / Windows 10 / Python 3.12**。这是用于上线联测的预发布版；真实模型自主操作和来源长期稳定性仍待验证。

## 使用 DSH 官方入口安装

在 DSH 的「插件 → 添加插件 → 包名或地址」中填入：

```text
https://github.com/chanhaoxian5-ship-it/dsh-radar/releases/download/v0.1.0-alpha.8/chanhaoxian5-dsh-radar-0.1.0-alpha.8.tgz
```

也可使用固定标签的源码地址：

```text
github:chanhaoxian5-ship-it/dsh-radar#v0.1.0-alpha.8
```

推荐 Release 包。以上地址需要本机可访问 GitHub；尚未发布 npm，不要只填 `@chanhaoxian5/dsh-radar` 并期望从 npm 下载。

安装后还需准备 Python 依赖并配置 `pythonPath`、独立 `dataDir`，然后重启/启用插件。[完整安装步骤](docs/install.md)。安装不会附带模型凭据或自动启用真实任务。

## 包含什么

- 独立 SQLite 数据库和 SHA 对象库；版本化订阅、原文、附件及整理结果。
- JSON 工具 `radar_call`、`radar_manual` 和 Skill `radar`，使用宿主真实 Agent 身份，不能自称 owner。
- 发现与旧文回查计划、幂等回执、租约、失败续跑、重启恢复及一致备份。
- 原生「消息雷达」会话页签和设置页：消息、详情、变化、关注、人工规则提案及运行状态。
- 首个来源：中国农业大学动物科学技术学院公开通知。有界公开分页与附件读取，保持证书校验，不绕过访问控制。

聊天中的明确人工规则由 Agent 提案，用户在页面确认后成为受保护规则。雷达不会自动修改项目计划，也不访问 Workbench 数据库。

## 验证与开发

[验收范围与限制](docs/verification.md) · [正式请求 Schema](docs/api/request-schemas-v2.json)

```powershell
$env:PYTHONUTF8='1'
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
npm ci --ignore-scripts --no-audit --no-fund
node --test tests/controller.test.mjs tests/controller-lifecycle.test.mjs
```

测试使用临时合成数据。公开仓库不含本机数据库、凭据、会话、实验归档或虚拟环境。alpha.8延续alpha.7业务实现，调整公开发布信息、文档和包内文件范围。
