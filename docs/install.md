# 安装、配置与升级

目标：DSH0.2.2-rc.1，Windows10；Python3.12及jsonschema4.26.0；院网采集需要curl>=8.4。其他宿主/系统尚未列为通过。

## 1. 准备独立Python

在PowerShell执行以下命令，不复制日常模型密钥：

```powershell
$radarRoot=Join-Path $env:LOCALAPPDATA 'DSH-Radar'
$radarPython=Join-Path $radarRoot 'venv/Scripts/python.exe'
python -m venv (Join-Path $radarRoot 'venv')
& $radarPython -m pip install jsonschema==4.26.0
Write-Output $radarPython
Write-Output (Join-Path $radarRoot 'data')
curl.exe --version
```

保留输出的两个绝对路径：Python可执行文件和数据目录。数据目录使用空目录或既有雷达目录；Windows服务设置当前账户与SYSTEM访问权限，拒绝无关非空目录和重解析点。

## 2. 官方插件入口

DSH「插件 → 添加插件 → 包名或地址」填入README中的Release `.tgz` 链接。安装后在插件配置中填写：

- `pythonPath`：上一步输出的python.exe绝对路径。
- `dataDir`：上一步输出的数据目录绝对路径。

保存后重新启用/重启插件。配置界面不可用时可在当前profile的cordis.patch.yml使用id=dsh-radar配置这两个字段；不要修改其他profile或全局配置。

命令行同样走官方入口：

```powershell
dsh plugin --profile YOUR_PROFILE add 'https://github.com/chanhaoxian5-ship-it/dsh-radar/releases/download/v0.2.0/chanhaoxian5-dsh-radar-0.2.0.tgz'
```

先使用独立DSH_HOME/profile试装；YOUR_PROFILE替换为确切目标，不要误装到日常环境。本仓库不发布npm包，private=true只阻止误发布npm，不影响GitHub仓库/Release安装。

## 3. 检查与使用

设置→消息雷达可查看本机服务状态和错误、启停自有后台。非空会话在轨迹右侧提供消息雷达页签；窄屏时可能折叠到宿主更多菜单。工具radar_call、radar_manual与Skill radar应可发现。缺Python或jsonschema时，修正配置/依赖后在页面重试启动。

通过Skill创建关注和选择来源，手动采集后等待会话整理。下载与入库由受校验工具执行；相关性、内容整理和回查策略由Agent判断。需要实际模型时，在所用DSH环境配置供应商/兼容接口、模型ID和独立凭证；插件不包含模型运行时或密钥。

人工要求先形成提案，由owner页面确认后保护。未知、未读附件、403和TLS失败均不能报告成可靠的新鲜完整采集。院网使用忽略个人curl配置的系统curl、系统证书验证及有界重试，无Reader实时回退。

## 4. 升级、停用、备份

第三方插件按当前DSH的官方更新/重新安装入口更新。更新前停止本插件自有后台，备份独立数据目录；版本冲突时不杀未知进程。停用保留数据，不自动删除证据或创建真实任务。

`radar.maintenance.backup(source_db,destination_db)`生成SQLite备份及对应`.objects`目录，两者必须同时保存。`restore(source_db,new_directory)`仅恢复到新目录、验证对象、更换service_epoch并撤销凭证。恢复后需重新签发凭证，不能复制旧凭据冒用旧worker。

当前业务Schema2；院网算法cau-zoom-3。旧提取算法变化需要单独建立可比基线，不能冒充网站改稿。真实历史迁移和Workbench联调不在本轮范围。


## v0.2.0 正式版升级

v0.2.0可使用README中的Release包地址，也可用下载后tgz绝对路径通过 `dsh plugin --profile YOUR_ISOLATED_PROFILE add ABSOLUTE_TGZ_PATH` 安装。先停止旧插件自有后台，备份SQLite及对象库；保留旧版包与一致备份。保持独立dataDir及Python路径，不复制模型凭据。

迁移是业务Schema2内的追加建表：product_categories、product_events、event_messages、product_audit、curation_items、curation_history。按本订阅当前来源修订和已有分析回填：当前分析相关→processed，不相关→not_included，无当前分析→pending；不将旧原文统一签收，事件与订阅不自动猜测对应。资源是否过期仍按不可变资源依据查询。新表和关联随一致备份保留，恢复仍换service_epoch并撤销凭证。旧版不能理解新页面投影，不建议直接降级写入同一在线库；恢复备份到新目录再使用对应旧版本。

`run_start`为发现新消息（manual也不重读旧通知）；`recheck`为指定通知重查；`retry_run`接续已有运行阶段。新工具以contract.for_method读取确切Schema。真实模型整理继续留到授权上线联测，不把后台采集完成视为整理完成。
