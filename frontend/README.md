# ScholarMind 前端

React + Vite + TypeScript。构建产物是纯静态文件，由后端托管，不需要 Node 服务。

## 开发

先在 `backend/` 起后端（默认 8000 端口）：

```bash
conda run --no-capture-output -n ScholarMind uvicorn scholarmind.api:app --port 8000
```

再起前端：

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173，端口被占时加 -- --port 5180
```

开发服务器把 `/api` 转发到 `http://127.0.0.1:8000`（`vite.config.ts`），和页面同源，后端种的会话 cookie 直接可用。

## 检查与构建

```bash
npm run lint       # ESLint，警告也算失败
npm run typecheck  # tsc
npm run build      # 类型检查后输出到 dist/
npm run preview    # 本地预览 dist/，同样转发 /api
```

## 交给后端托管

- `dist/` 里是 `index.html`、`logo.png` 和带哈希的 `assets/*`，按原路径提供即可；`assets/` 可长期缓存。
- 路由用 History API：`/`（每日新增）、`/library`（论文库）、`/starred`（精选）、`/papers/:id`（概览）、`/papers/:id/read`（阅读：左 PDF、右解读）、`/me`（个人信息）、`/login`。除 `/api`、`/mcp` 和静态文件外，其余路径都返回 `index.html`。
- 筛选条件直接用接口的参数名写在地址栏，页面原样转给 `GET /api/papers`：`q`、`source`、`year`、`domain`、`topic`、`paper_type`、`innovation`、`category`、`keyword`、`date_from`、`date_to`、`page`；每日新增另有 `day`（arXiv 公布日）和 `kind`（新论文 / 更新）。多选的筛选用重复参数（`?domain=a&domain=b`）。按日期筛选时附上浏览器时区 `tz`（不写进地址栏）。
- 后端还没有的接口都有降级，缺了页面照常可用：`/api/papers/facets`（筛选篇数，缺了改为逐项计数）、重复参数（缺了按单选算）、`/api/me/token`（缺了提示还不支持）、`starred_at`（缺了精选页不分段）、`/api/collections`（分组，尚未接入）。

## 调外观

所有设计变量在 `src/index.css` 顶部的 `@theme` 里：颜色、字体、字号与行高、间距刻度（4px）、圆角、阴影、缓动、页面宽度、动效时长、纸张颗粒强度。Tailwind 默认的调色板和刻度已清空，页面和组件只能用这里定义的值。动效时长由 `src/motion.ts` 从同一组变量读取。

版式：页头和所有页面共用 `page` 框架，宽屏下 12 栏（`gap-x-8`）。左栏占 1–3 栏，内容居中只占它的中间一半，手绘竖线（`hand-rule-v`）在左栏 7/8 处；论文列表和正文占 4–10 栏；11–12 栏留空。阅读页铺满视口，左右两栏可拖动调宽。

## 结构

| 文件 | 内容 |
| --- | --- |
| `src/index.css` | 设计变量、基础样式、组件类（`field`、`btn-*`、`tag`、`badge`、`option`、`popover`、`hand-rule(-v)`、解读排版 `.report`） |
| `src/router.ts` | History 路由；站内 `<a href>` 点击由它接管；`listUrl`、`toggleUrl`（多选）生成列表地址；记住上一个列表供返回 |
| `src/api.ts` | 接口类型、`useApi`（内存缓存、同一请求合并）、`usePaper`（生成解读时轮询）、`useVocabulary`、`useFacets`、`useCounts`；401 跳登录页并带回原地址 |
| `src/App.tsx` | 页头（三个栏目、用户名链接到个人信息、退出）、路由分发 |
| `src/Daily.tsx` | 每日新增：左栏看板（全部 / 新论文 / 更新、分析进度、近 30 天柱状图、领域分布），中间是当天的论文 |
| `src/Discover.tsx`、`src/Facets.tsx`、`src/DateRange.tsx` | 论文库：搜索、日期；左栏筛选（来源、领域 → 方向、论文类型、创新性质、年份、arXiv 类别，可多选、带篇数；只有一个来源时不显示"来源"） |
| `src/Starred.tsx` | 精选：个人书架，左栏是收藏数和"我的领域"，论文按收藏时间分段；没有收藏时居中显示插画 |
| `src/PaperCard.tsx` | 论文条目（所有列表共用，高度固定）：标题、作者与日期（会议论文显示"CVPR 2026"这样的来源标记，代替 arXiv 类别）、架构图缩略图（悬停放大）、总结、标签与入口；收藏按钮 |
| `src/PaperView.tsx` | 概览：标题、阅读入口、中英文摘要、解读预览（架构图、创新性质、章节目录）；左栏是返回、标签编辑和元数据 |
| `src/Reader.tsx`、`src/Report.tsx`、`src/Pdf.tsx`、`src/outline.ts` | 阅读页：左 PDF（PDF.js）、右解读（Markdown、图表放大、页码跳到原文、当前小节、阅读进度）；没有解读时提供"生成解读"和进度；论文没有 PDF 时只显示解读 |
| `src/Tags.tsx`、`src/ReportAction.tsx` | 标签显示与编辑（领域、方向、论文类型、关键词，人工添加的带墨色描边）；生成解读的按钮和状态 |
| `src/Profile.tsx`、`src/Password.tsx` | 个人信息：账号与改密码、接入 AI 助手（MCP 地址、个人令牌、Claude Code 命令）、我的数据 |
| `src/Tex.tsx` | 标题、摘要、总结、解读里的 LaTeX：公式交给 Temml，文本命令在这里处理；解析不了的公式原样显示 |
| `src/Illustrations.tsx`、`src/motion.ts`、`src/ui.tsx`、`src/prefs.ts` | 插画；动效预设；提示气泡、空状态、骨架屏、分页、返回按钮；本地记住的偏好 |

## 依赖

| 依赖 | 用途 |
| --- | --- |
| `tailwindcss`、`@tailwindcss/vite` | 设计变量集中在一处，页面只引用变量 |
| `motion` | 页面和列表进入、插画描线；用 `LazyMotion` 按需加载，跟随系统"减少动态效果" |
| `radix-ui` | Popover（日期）、DropdownMenu（标签、目录）、Dialog（图片放大）、HoverCard（缩略图放大）、Tooltip，无样式、可访问 |
| `react-markdown`、`remark-gfm`、`remark-math` | 解读的 Markdown（含表格、公式）。直接生成 React 元素，不渲染原始 HTML；随阅读页按需加载 |
| `pdfjs-dist` | 原文阅读，用它自带的 PDFViewer；只在阅读页加载（另有 worker 文件） |
| `temml` | LaTeX 转 MathML，由浏览器原生排版；单独成块，出现公式时才加载 |
| `react-day-picker` | 日历，用中文 locale，样式由设计变量覆盖 |
| `lucide-react` | 图标 |
| `@fontsource-variable/source-serif-4`、`@fontsource-variable/source-sans-3` | 开源字体（标题衬线、正文无衬线，同一体系），随构建打包；中文回退到系统字体 |
