# Crawl4AI来源建设

仅在新来源接入或适配器修复时读取。用户选定Crawl4AI开源引擎，不使用其云服务或第三方社区Skill。当前插件未随包安装引擎、也没有动态注册工具；本文指导Agent建设，不代表运行时已经接通。

## 探索与生成

使用项目隔离Python环境，先核对安装版本与官方API；如需安装依赖，在建设环境安装并记录实测版本，正式依赖锁定，不修改全局Python。官方入口：
- https://github.com/unclecode/crawl4ai
- https://docs.crawl4ai.com/core/installation/
- https://docs.crawl4ai.com/extraction/no-llm-strategies/

当前Agent观察列表和多种正文样本，自行生成CSS/XPath规则及必要适配代码，不在脚本里写死额外模型或API Key。可用AsyncWebCrawler、CrawlerRunConfig、JsonCssExtractionStrategy/JsonXPathExtractionStrategy，参数以目标版本为准。规则提取不调用模型；复杂页面结构探索由Agent完成。现有院网curl适配器保留，静态页面无需强行更换浏览器抓取。

关注稳定文章身份、原发布时间、正文/表格/图片/附件、跨页读取及外链范围。多样本验证选择器，避免依赖偶然位置。动态页面等待明确的内容条件并设超时；提取schema只负责字段定位，不自动解决分页与资源读取。Windows用脚本文件与结构化配置，不临时拼复杂PowerShell/Bash转义。

保留原始响应与渲染后内容的区别，Markdown是派生表示。关键词或相关性过滤不能删掉原文条件再当完整证据保存。Crawl4AI成功标记不等于雷达fresh/complete，需按实际抓取/缓存/模板/资源状态映射。调试缓存不能用来证明远端没有更新。

## 产物与启用

生成一组可维护产物：适配器或受控配置、网站reference、验证样例与可复现入口、版本及试采报告。reference记载入口/范围、身份、分页、正文定位、资源方式、原地修改特性、限制、引擎/提取版本和最近验证时间。

至少验证不同正文样本、列表翻页确有新条目、日期、相对链接、附件、空结果和错误页；验证同URL正文/附件变化。程序按通过验证的规则重复执行，只在结构失效时重新建设；提取算法变化不得伪装为网站改稿。

候选先存独立工作目录。现有来源由维护代码注册，source_save不安装代码；具备授权代码工作区时完成注册与隔离验收，否则交付候选与具体接入要求，不编造注册工具。source-notes用户说明不自动进入radar_manual白名单；新随包reference需同步登记与可读性检查。

生产执行遵循雷达的站点间隔/并发、权限和额度。不自动启用绕过访问控制、指纹伪装或无限爬取。失败保留证据并报告明确限制，不把缺内容当无通知。
