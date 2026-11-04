# 律证 · 法律文献证据工作台

面向律师内部研究的第一阶段原型：官方法律原文入库、条文级精确检索、DeepSeek 证据约束回答、引用校验和原文定位。

## 已实现

- 国家法律法规数据库《中华人民共和国民法典》官方 DOCX 电子文本入库，共 1260 条；
- 保存官方来源、公布／施行日期、效力状态、原始 DOCX 快照和 SHA-256；
- 第 N 条／中文条号精确定位，支持一次检索多个条文；
- 自然语言条文级 BM25 检索，低相关度时明确拒答；
- DeepSeek 官方 API 支持解释、比较、归纳和改写等任务；
- 回答后独立检查证据编号、实质性段落引用、长引文原文匹配和证据外文献名称；
- React 工作台：问答、文库、引用跳转、原文复制、元数据与校验记录；
- 桌面与移动端布局，无密钥时仍可完成检索和原文核验。

## 启动

要求：Node.js 20+、Python 3.11+。首次使用执行：

```bash
npm install
npm run dev
```

开发脚本会同时启动 Vite 前端和本地 Python API，并将命令行的 host/port 参数转发给 Vite：

```bash
npm run dev -- --host 0.0.0.0 --port 7100
```

## 配置 DeepSeek

复制 `.env.example` 为 `.env`，填写：

```bash
DEEPSEEK_API_KEY=你的官方API密钥
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

重新执行 `npm run dev`。密钥只由本地 Python 后端读取，不进入前端代码或构建产物。没有密钥时，系统明确显示“原文检索模式”，不会模拟模型回答。

## 法律数据

项目已包含入库后的条文数据和官方 DOCX 快照。需要重新从官方来源更新时：

```bash
npm run ingest
```

入库器会下载官方详情和 DOCX，验证 DOCX 条文数与官方目录一致后才写入本地文库。当前仅启用《中华人民共和国民法典》；其他文种、历史版本和自动更新机制尚未纳入本原型。

## 验证

```bash
npm run test:backend
npm run lint
npm run build
```

如本机 Chrome 位于 `C:\Program Files\Google\Chrome\Application\chrome.exe`，还可执行：

```bash
node scripts/validate-browser.mjs
```

浏览器脚本会冷启动服务，验证精确问答、文库检索、复制、弹窗与 375px 移动端布局，并在结束后关闭临时服务。

## 精度边界

引用校验是机械性防错措施，不等于对法律结论正确性的保证。当前自然语言检索采用偏向精确率的本地条文检索，尚未接入向量模型、商业数据库、历史时点版本选择和完整法律回归题库。正式法律意见仍需律师核对标准文本与适用版本。

本项目包含运行时 API 和密钥，因此仅作为本地应用预览，不按纯静态网站发布。
