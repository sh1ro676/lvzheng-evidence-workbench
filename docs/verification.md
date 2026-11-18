# 律途验收与审核

自动化执行、实际检索和法律质量分别报告。CI 通过不表示真实检索命中率或法律分析正确。

## GitHub 自动验收

`.github/workflows/verify.yml` 在 PR 更新和 main 提交时运行，也支持手动运行。

- Python 3.11、3.12：后端及验收工具回归；
- Node.js 24：前端逻辑／渲染测试、ESLint、TypeScript 和 Vite 构建；
- Chromium：真实浏览器操作，接口响应使用固定测试资料，不调用付费服务。

测试固定到 PR 的 head SHA。PR 的 Checks 和仓库的 Actions 页面可以查看执行日志。
浏览器任务保存 `.validation/browser/report.json`、桌面／手机截图、失败截图、Vite 日志和 `trace.zip`，作为 Actions artifact 保留 7 天。
在本机运行浏览器验收：

```bash
npm ci
npm run browser:install
npm run test:browser
```

也可以设置 `CHROME_PATH` 使用本机已有 Chrome。跟踪文件可在本机打开：

```bash
node node_modules/playwright-core/cli.js show-trace .validation/browser/trace.zip
```

浏览器验收检查追问上下文、四校校验合并、引用跳转、迟到补充隔离、新任务清理／取消、独立身份来源链接，以及 375px 布局。使用模拟接口，所以不能代替真实服务测试。

## 真实 API 验收

密钥留在本机 `.env`，不要上传 GitHub。先检查配置，此步骤不调用上游：

```bash
python scripts/validate_agent_live.py --check-config
```

未配置所需密钥时，退出码为 2，报告标为 `not_run`，不会冒充成功。
默认只运行一个法条问题；下列命令会调用真实服务并消耗其额度：

```bash
python scripts/validate_agent_live.py --case statute --label new --output .validation/live/new-statute.json
python scripts/validate_agent_live.py --case topic --label new --output .validation/live/new-topic.json
python scripts/validate_agent_live.py --case followup --label new --output .validation/live/new-followup.json
```

`followup` 会先执行前置问题，再将该次实际返回的答案作为历史，不使用手写的“理想答案”。
`--all` 执行 `validation/live_cases.json` 中全部问题，调用更多真实服务。每个问题默认最多 100 次上游 HTTP 请求，可通过 `--max-upstream-requests` 调整，范围 1–500；预算包括模型规划、生成、网页工具与法宝 MCP 握手，不仅是网页搜索次数。

脚本在临时端口启动所选工作树的真实后端，通过 HTTP 调用 `/api/chat/stream`；需要四校补充时，再调用 `/api/schools`。
报告保留提交 SHA、工作树是否有未提交文件、模型、固定问题集合指纹、阶段事件、原始答案、证据、来源分级、身份来源、实际查询、校验结果、请求次数与耗时。密钥会在写报告前移除；报告默认保存在已被 Git 忽略的 `.validation/live/`，不会自动公开或作为 CI artifact 上传。

退出码：0 表示已选问题的程序／机械校验通过；1 表示接口或机械检查失败；2 表示缺少配置。遇到失败会停止后续问题，避免反复调用不可用服务。零证据、零学校命中仍在指标中如实记录，不能由“模型能回答”推断检索成功。

## 旧版与新版配对

可在 Git worktree 中保留旧版本，用当前验收脚本读取它，而不修改旧代码。举例：

```bash
git worktree add ../lvtu-baseline 63e6e251b54bf38c134992084e0fa4ae282690e1
python scripts/validate_agent_live.py --project-root ../lvtu-baseline --env-file .env --case topic --label old --output .validation/live/old-topic.json
python scripts/validate_agent_live.py --env-file .env --case topic --label new --output .validation/live/new-topic.json
python scripts/compare_validation.py .validation/live/old-topic.json .validation/live/new-topic.json
```

比较器要求问题集合、问题正文／历史依赖、模型、服务配置及请求预算一致，且两边都完成了计划内问题。配置预检和浏览器模拟报告不能作为真实检索结果参加比较。输出只比较原始证据数量、学校覆盖、请求数及耗时；新增资料仍须核对是否真正相关。

## 刑法学者名录对照

刑法学者名录试点覆盖四校。每校最多三次搜索、十次正文抓取，首轮仍搜主题，最多一轮改用名录姓名反查观点。身份待核的网页继续作为线索展示。名录记录高校出处与选词标签，只有高校资料中明确列出的文献才用于篇名交叉核验。

可在同一版本、同一配置下关闭和开启名录策略，配对比较。以下命令会使用实际 API 额度：

```bash
python scripts/validate_agent_live.py --case criminal-defense --criminal-directory off --output .validation/live/defense-off.json
python scripts/validate_agent_live.py --case criminal-defense --criminal-directory on --output .validation/live/defense-on.json
python scripts/compare_validation.py .validation/live/defense-off.json .validation/live/defense-on.json
```

`criminal-accomplice` 可用同样方式复测。显式指定名录策略时，两组均关闭网页正文缓存。报告分别给出取得正文署名、取得署名与院校关联、取得可展示观点的学校数。检查原网页能否支持作者和观点归属，再判定增加的命中是否有效。

## 独立质量审核

审核者应逐题打开原始出处，核对：

1. 回答是否完成用户任务；检索资料是否真的支持所述内容；
2. 法律名称、条号、版本与适用情形是否一致；
3. 作者、学校任职和观点是否正确关联，有无同名误归属；
4. 活动页、媒体转述、模型分析是否被误写成直接原话；
5. 旧版未命中新版命中的资料是否确实相关；
6. 新版有无因扩检引入更多误收，或显著增加调用与等待。

程序统一保留 `pending_manual_review`，不把机械检查改写成法律正确性认证。联网资料与模型会波动，单次配对也不证明稳定提升。
