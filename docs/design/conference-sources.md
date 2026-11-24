# 顶会论文来源调研

调研日期：2026-11-22/23（所有"目前""尚未"都指这个时间点）。范围按 brain 的补充：只要主会论文；重点是每个会议**最新一届怎么拿、下一届出来时怎么接上**，往年规律只做简单确认。

所有结论来自实际请求（地址写在每条后面或第 4 节），样例在 `samples/`，复现脚本在 `scripts/`。没能核实的地方标了"未核实"，并在第 6 节单列。

## 1. 结论

1. **4 个适配器覆盖 14 个会议**：`openalex`（8 个：ICRA、IROS、KDD、ACM MM、AAAI、ACL、EMNLP、RSS）、`cvf`（3 个：CVPR、ICCV、ECCV）、`paper_files`（2 个：NeurIPS、ICLR，两站同一套网站程序）、`pmlr`（1 个：ICML）。
2. **IEEE、ACM 的四个会（ICRA、IROS、KDD、MM）只能走 OpenAlex**：IEEE Xplore 网页的 robots.txt 不允许访问（还点名禁止 `Claude-User`）；官方 API 要申请 key，条款只允许"机构内非商业研究"，并且禁止批量存储内容。ICRA/IROS 的会议程序站 PaperCept 对所有爬虫 `Disallow: /`。ACM DL 从 2026-01-01 起全部开放获取，但请求会被 Cloudflare 验证拦下。OpenAlex 上这四个会的最新一届有 93–100% 带摘要。
3. **OpenReview 现在不能匿名用程序访问**：API、论文页、PDF 都会返回"Challenge verification required"，页面提示"Have an OpenReview account? Sign in to skip this check"。所以 ICLR、NeurIPS、ICML 改走正式论文集。代价是 NeurIPS 的正式论文集比会议晚大约 8 个月。如果想早点拿到并带上 oral/poster 标记，就需要一个 OpenReview 账号（见第 7 节的待决事项）。
4. **PDF 不开放的会议，arXiv 上能找到的比例大约一半**：ICRA 2026 约 68%，IROS 2025、KDD 2026、ACM MM 2025 都是 48–50%（每个会随机抽 40 篇）。另外，对 24 篇没被 OpenAlex 和 S2 关联到 arXiv 的论文直接查了 arXiv，一篇都没找到，说明这个比例没有被明显低估。没有 arXiv 版本的论文只能做简报，做不了全文报告。
5. **两个最新一届目前有缺口**：
   - ECCV 2026（9 月开完）还没出现在 ECVA 的开放获取页，Springer 版在 OpenAlex 上没有摘要；
   - NeurIPS 2025 的论文页和 PDF 已经在 proceedings 站上，但年份索引页现在只列出 64 篇 Creative AI 分轨的论文。完整列表可以从 DBLP 拿（5,824 条，都链接到 proceedings 站）。
6. **DBLP 不能当主数据源**：没有摘要；网页和搜索 API 都有 Anubis 人机验证；收录也有滞后，比如 ICML 2026、RSS 2025/2026 都还没收。它的 SPARQL 端点可以用，适合拿来核对篇数和列 DOI。Semantic Scholar 按会议列出的篇数不全（比如 ICLR 2026 只有 2,682 篇，实际是 5,351 篇），不带 key 时经常返回 429，只适合用来补 arXiv 关联。

## 2. 总表

"最新一届"指截至调研日已经有论文集的那一届。"篇数"是该届主会的实际计数，来源写在括号里。

| 会议 | 最新一届 | 推荐来源（获取方式） | 摘要 | PDF 开放 | 篇数 | 主要限制 |
| --- | --- | --- | --- | --- | --- | --- |
| NeurIPS | 2025（2025-12） | papers.nips.cc 正式论文集（列表页 + 逐篇详情页 HTML） | 有（逐篇页） | 是 | 约 5,800（DBLP 5,824，含 D&B 等分轨） | 会后约 8 个月才出（2025 届 DOI 在 2026-08-06 注册）；2025 年份索引页目前只有 Creative AI 分轨；OpenReview 匿名访问被拦 |
| ICML | 2026（2026-07-06~11） | PMLR 每卷一个 `bibliography.bib`（v306） | 有（bib 里就有） | 是（GitHub raw） | 6,552 | 会后约 2.5 个月；没有 DOI，不分 oral/poster；OpenAlex 不收 PMLR 版 |
| ICLR | 2026（2026-04） | proceedings.iclr.cc（和 NeurIPS 同一套程序） | 有（逐篇页） | 是 | 5,351 | 没有 DOI，不分 oral/poster |
| ICRA | 2026（2026-06） | OpenAlex（底层是 IEEE Xplore） | 99% | 否；arXiv 约 62–68% | 1,874（DBLP） | Xplore 会后约 4 个月才上线；IEEE 官方路线受限（见结论第 2 条） |
| IROS | 2025（2026 届还没上 Xplore） | OpenAlex | 100% | 否；arXiv 约 40–48% | 1,986（DBLP） | 同 ICRA；2025 届会后约 1 个月上线 |
| RSS | 2026（2026-07-13~17） | OpenAlex，或 roboticsproceedings.org 静态页 | 95%（官方页 100%） | 是 | 210 | 会后约 6 周；篇数少 |
| ECCV | 2026（2026-09-08~12） | ECVA 开放获取页（和 CVF 同款 HTML）；2026 届还没上线 | ECVA 有；Springer 版和 OpenAlex 都没有 | ECVA 是；Springer 否 | 约 2,834（初步列表）/ 2,913（DBLP，Springer 版） | 2026 届开放获取版何时出现不确定，见第 7 节 |
| ICCV | 2025（2025-10） | CVF Open Access（列表页 + 逐篇详情页） | 有（逐篇页） | 是 | 2,701 | 摘要要逐篇请求 |
| CVPR | 2026（2026-06） | CVF Open Access | 有（逐篇页） | 是 | 4,042（Findings 另有 941 篇，单独存放） | 同上 |
| ACL | 2026（2026-07） | OpenAlex，或 ACL Anthology GitHub 上的 XML（每届一个文件） | 100% | 是（CC BY 4.0） | long 2,222 + short 74（Findings 2,163 篇另成一卷） | 基本没有 |
| EMNLP | 2026（2026-10） | 同 ACL | 100% | 是 | main 2,704（Findings 2,478 篇另成一卷） | 基本没有 |
| AAAI | AAAI-26（2026-01） | OpenAlex，或 OJS 的 OAI-PMH 接口 | 100% | 是 | 4,921（DBLP，含特别分轨、学生摘要等）；技术分轨共 43 期，每期约 94 篇 | 会后 6–8 周上线 |
| KDD | 2026（2026-08-09~13） | OpenAlex | 93% | ACM 已经开放获取，但被 Cloudflare 拦；arXiv 约 39–49% | 1,477（DBLP，两轮投稿合计，含 workshop/tutorial 条目） | 主会和 workshop/tutorial 混在同一本论文集里 |
| ACM MM | 2026（2026-11-10~14） | OpenAlex | 98% | 同 KDD；arXiv 约 39–50% | 1,901（OpenAlex，会后一周的计数，可能还在增长）；2025 届 1,621（DBLP） | 同 KDD |

## 3. 推荐方案

### 3.1 适配器划分

| 适配器 | 覆盖 | 怎么做 | 工作量（估） | 最容易失效的地方 |
| --- | --- | --- | --- | --- |
| `openalex` | ICRA、IROS、KDD、MM、AAAI、ACL、EMNLP、RSS | `GET /works?filter=primary_location.source.id:<S>,publication_year:<Y>&per_page=100&cursor=*` 分页取。从 `abstract_inverted_index` 还原摘要；从 `locations` 找 arXiv 版本，拿到 arXiv ID 后交给现有去重。每个会议在配置里写 source ID、年份和主会过滤规则 | 约 150 行，外加每个会议一段配置 | source ID 被合并或改动；某一届的收录有滞后；OpenAlex 的计费规则变化；"主会"过滤（见 3.2） |
| `cvf` | CVPR、ICCV、ECCV | 先取一页 `?day=all` 列表（标题、作者、PDF、arXiv 链接、bibtex），再逐篇取详情页里的 `<div id="abstract">`。ECVA 页面是同款模板，但引号和链接写法略有不同 | 约 150 行 | HTML 模板改版；每届要发几千次请求（CVPR 约 4,000 次，按 2 秒一次大约 2.2 小时） |
| `paper_files` | NeurIPS、ICLR | 先取 `/paper_files/paper/<年>` 列表页，每条 `<li class="<分轨>">` 是一篇；再逐篇取 `-Abstract-<分轨>.html` 拿摘要（详情页里有 `citation_*` meta） | 约 100 行 | 年份索引页不完整（NeurIPS 2025 现在就是这样，可以改从 DBLP 拿列表兜底）；模板改版；每届 5,000 多次请求 |
| `pmlr` | ICML | 从 PMLR 首页找到"Proceedings of ICML <年>"对应的卷号，取 `v<卷>/assets/bib/bibliography.bib`。一个文件就包含整届的标题、作者、摘要、PDF 和 OpenReview 链接 | 约 60 行（bib 是机器生成的，格式规整，用正则解析就够，不加依赖） | 几乎不会失效；唯一的问题是卷号事先不知道 |

可选的第 5 个适配器是 `openreview`，前提是有账号。它能覆盖 ICLR、NeurIPS、ICML：录用结果一公布就能拿，还带 oral/spotlight/poster 标记。代价是脚本要登录，账号开了 MFA 的话还要处理 TOTP。

另外两条可以替代的路线：
- AAAI 也可以不用 OpenAlex，改用 OJS 的 OAI-PMH 接口。它和现有 `arxiv.py` 用的是同一种协议，`dc:source` 里直接写着期名（比如"AAAI-26 Technical Tracks 1"），按分轨过滤更稳。
- ACL/EMNLP 也可以直接读 Anthology 的 XML，主会和 Findings 天然分卷，比 OpenAlex 早几天到几周可用。

这两条都各自要多写一个适配器，所以默认不用。

### 3.2 每个来源里怎么区分主会、Findings 和 workshop

| 来源 | 区分方式 |
| --- | --- |
| CVF | 路径不同：`/CVPR2026` 是主会，`/CVPR2026_findings`（内容在 `content/CVPR2026F/`，bibtex 的 booktitle 末尾有 "Findings"）是 Findings，`/CVPR2026_workshops/` 是 workshop。只取主会路径即可。不标 oral/highlight |
| ECVA | `papers.php` 只收主会，workshop 不在这里 |
| NeurIPS proceedings | 看 `<li class>`：`conference`（主会）、`datasets_and_benchmarks_track`、`creative_ai_track`。详情页 URL 的后缀相同，比如 `-Abstract-Conference.html` |
| ICLR proceedings | 只有 `conference` 一类，不分 oral/poster |
| PMLR | 主会单独一卷，workshop 是别的卷；卷内不能区分 position paper |
| ACL Anthology | 按卷区分：`2026.acl-long`、`acl-short` 是主会；`2026.findings-acl` 在 `2026.findings` 集合里；industry、demo、srw、tutorials 各自一卷。OpenAlex 里可以按 DOI 前缀过滤（`10.18653/v1/2026.acl-long.` 等） |
| OJS（AAAI） | 期名："AAAI-26 Technical Tracks 1–43" 是技术分轨，44–46 期是特别分轨（AI Alignment、AI for Social Impact），47–48 期是 IAAI、EAAI、学生摘要等。OpenAlex 里对应 `biblio.issue` 字段 |
| OpenAlex（IEEE） | ICRA、IROS 的 Xplore 论文集只收主会论文。CVPRW、ICCVW 是独立的 source |
| OpenAlex（ACM） | KDD、MM 的论文集里混着 workshop 综述、tutorial、demo 等条目，要按标题、页数或 ACM 的 article type 过滤。具体规则需要实现时再核对（第 6 节） |
| DBLP | 按目录页区分：`acl2026-1…6` 和 `acl2026f`、`cvpr2026` 和 `cvprw2025`、`iccv2025` 和 `iccv2025w` |

### 3.3 下一届什么时候能接上

下表里"可抓时间"的规律来自实际观察（DOI 注册时间、ingest-date、PDF 的 Last-Modified、卷的发布日期）。会议日期标"未核实"的是凭记忆或来自第三方页面。

| 会议 | 最新一届 → 可抓时间 | 规律 | 下一届 |
| --- | --- | --- | --- |
| NeurIPS | 2025-12 → 2026-08（DOI 注册 2026-08-06） | 会后约 8 个月；2024 届的 DOI 在 2025-11-03 注册，更晚 | 2026-12（未核实），预计 2027 年中到年底 |
| ICML | 2026-07 → 2026-09-29（PMLR v306 发布） | 会后约 2.5 个月；2025 届 v267 在 2025-10-06 发布 | 2027-07（未核实） |
| ICLR | 2026-04 → 2026-04-20（页面上的发布日期） | 会议开始前后 | 2027-04（未核实） |
| ICRA | 2026-06 → 2026-10-07（Xplore DOI） | 会后 3.5–4 个月；2025 届 DOI 在 2025-09-02 注册 | 2027（未核实） |
| IROS | 2025-10-19 → 2025-11-27（Xplore DOI） | 会后约 1 个月；但 2026 届（09-27~10-01，来源为第三方页面）到 11-23 还没有出现 | 2026 届预计 2026-12 |
| RSS | 2026-07-13~17 → 2026-08-31（DOI；PDF 在 08-26 上传） | 会后约 6 周 | 2027-07-06~11，雅典（来自 RSS 官网说明） |
| ECCV | 2026-09-08~12 → Springer 版 2026-09-13；ECVA 版还没出 | 2024 届的 ECVA PDF 是 2024-10-01 上传的，正值会期 | 2028 |
| ICCV | 2025-10 → CVF 版 PDF 2025-09-27 已上传；Xplore 版 2026-04-29 | CVF 版在会前后上线 | 2027 |
| CVPR | 2026-06 → CVF 版 PDF 2026-05-16 已上传 | CVF 版在会前后上线；Xplore 版晚约 2 个月（2025 届是 2025-08-13） | 2027-06 |
| ACL | 2026-07 → 2026-06-22（ingest-date） | 会前 1–4 周 | 2027 |
| EMNLP | 2026-10 → 2026-10-21（ingest-date）；2025 届是 2025-10-29 | 会前约 1 周 | 2027 |
| AAAI | AAAI-26（2026-01，未核实）→ 2026-03-17 | 会后 6–8 周；AAAI-25 在 2025-04-11 上线 | AAAI-27（2027 年初，未核实） |
| KDD | 2026-08-09~13 → 2026-07-31（第一轮 DOI）/ 2026-08-06（第二轮 DOI） | 会议开始前后 | 2027 |
| ACM MM | 2026-11-10~14 → 2026-11-05（DOI） | 会前约 1 周；OpenAlex 两周内已收 1,901 篇 | 2027，香港 |

OpenAlex 本身的滞后是几天到几周：EMNLP 2026 在 10-21 入库 Anthology，到 11-23 时 OpenAlex 已有 2,921 篇；ICRA 2026 的 DOI 在 10-07 注册，现在 OpenAlex 已有 1,770 篇。

**接下一届的建议做法**：每个会议在配置里写上"会议 + 年份"。导入任务每周跑一次，查对应来源有没有出现这一届：PMLR 首页有没有新卷，CVF/NeurIPS/ICLR 的年份页是否返回 200，OpenAlex 里这一届的篇数是否超过一个阈值。出现了就导入，之后再跑几次补齐，因为 OpenAlex 的计数会持续增长。

### 3.4 对现有入库逻辑的影响（供 brain 设计时参考）

- `papers` 表现在没有会议字段（`0001` 迁移和 `db.py` 里都没有 venue），需要加会议、年份、分轨。
- CVF 列表里约 65% 的论文带作者自己填的 arXiv 链接，OpenAlex 的 `locations` 里也有 arXiv 版本。把 arXiv ID 带进记录后，`_find` 会按 arXiv ID 和已有的 arXiv 记录合并。但 `upsert_papers` 会用会议版覆盖 `title`、`abstract`、`pdf_url`，而 CVF 等来源的 PDF 链接没有版本号，这和"报告页码对应某个固定版本"的约定冲突。需要决定合并时哪些字段以哪一方为准。
- 会议源的请求不该占用 `arxiv_lock`（只有回落到 arXiv 下载 PDF 时才需要）。CVF 和 proceedings 站每届要几千次请求，适合在 `fetch` 队列里慢慢跑。DBLP 的 SPARQL 端点在 robots.txt 里要求 `Crawl-delay: 10`。

## 4. 每个会议的详细记录

每条注明访问过的地址；没写日期的请求都发生在 2026-11-22/23。

### NeurIPS

- 正式论文集：`https://papers.nips.cc/paper_files/paper/<年>`（另一个域名 `proceedings.neurips.cc` 内容相同）。
  - 2024 届：`/paper_files/paper/2024` 有 4,034 篇 `conference` 加 459 篇 `datasets_and_benchmarks_track`，样例 `samples/nips-2024.excerpt.html`。
  - 2025 届：`/paper_files/paper/2025` 只有 64 篇 `creative_ai_track`（两个域名结果一样，样例 `samples/nips-2025.html`）。但详情页已经存在，比如 `/paper_files/paper/2025/hash/99fda93fb2c8f60fbbf10a7df6cf1c06-Abstract-Conference.html`，页面写着"Advances in Neural Information Processing Systems 38 Main Conference (NeurIPS 2025)"，DOI 是 `10.52202/085713-3570`（样例 `samples/nips-2025-paper.html`）。这个链接是从 DBLP 拿到的。
- 详情页字段：标题、作者、分轨、DOI（2024 年起有，前缀 10.52202）、摘要、PDF（`citation_pdf_url`）、bibtex。不标 oral/poster。样例 `samples/nips-2024-paper.html`。
- 时间：2024 届第一篇的 DOI 在 2025-11-03 注册（`https://api.crossref.org/works/10.52202/079017-0001`）；2025 届样例 DOI 在 2026-08-06 注册。
- robots.txt：papers.nips.cc 返回 404，即没有限制。
- 会议站 neurips.cc 的 robots.txt 写了 `Disallow: /static`。我在读 robots 之前请求过一次 `https://neurips.cc/static/virtual/data/neurips-2025-orals-posters.json`（6,002 条，带摘要和 OpenReview 链接，按"Accept (poster/spotlight/oral)"区分），文件已经删除，这条路**不能用**。HTML 列表 `https://neurips.cc/virtual/2025/papers.html` 是允许的，但只有标题（5,858 个 poster 链接）；摘要在逐篇的 `/virtual/2025/poster/<id>` 页上（样例 `samples/neurips-2025-poster.html`）。
- OpenReview（`https://api2.openreview.net/notes?content.venueid=...`、`https://openreview.net/forum?id=...`、`https://openreview.net/pdf?id=...`）：匿名访问分别返回 403 `ChallengeRequiredError`、307 跳到 `/challenge`、403。改用浏览器 UA 结果一样。验证页上写着"Have an OpenReview account? Sign in to skip this check"（样例 `samples/or-forum.html`、`samples/or-pdf403.html`）。OpenReview 文档说明，用个人邮箱注册的账号要经过人工审核；脚本登录时如果账号开了 MFA，可以通过 `mfa_code_provider` 提供 TOTP 验证码（`https://docs.openreview.net/llms-full.txt`）。
- 篇数：DBLP `neurips2025` 有 5,824 条；OpenAlex source `S4363606243` 的 2025 年有 5,817 条，其中带摘要 71%、有 arXiv 版本 66%。

### ICML

- `https://proceedings.mlr.press/` 首页能看到："Volume 306 Proceedings of ICML 2026"、"Volume 267 Proceedings of ICML 2025"、v235 是 2024、v202 是 2023（样例 `samples/pmlr-index.html`）。卷号不连续，要从首页按标题查。
- `https://proceedings.mlr.press/v306/` 有 6,552 篇，页面写着"Published … on 29 September 2026"；v267 有 3,330 篇，"Published … on 06 October 2025"。
- `https://proceedings.mlr.press/v306/assets/bib/bibliography.bib`（14 MB）有 6,553 条，即 1 条卷信息加 6,552 篇论文。每篇都有 `abstract`、`pdf`（`raw.githubusercontent.com/mlresearch/v306/...`）和 `url`，样例 `samples/pmlr-v306-bibliography.excerpt.bib`。列表页上另有 OpenReview 链接。没有 DOI，不分 oral/poster。
- robots.txt 返回 404（站点在 GitHub Pages 上）。
- OpenAlex：ICML 的 source `S4306419644` 在 2025、2026 年都是 0 条。按标题搜 ICML 2026、2025 的论文，只找到 arXiv 版或机构仓储版（`scripts/oa_title.py`）。所以**通用路线覆盖不了 ICML**。

### ICLR

- `https://proceedings.iclr.cc/` 有 2024、2025、2026 三届。`/paper_files/paper/2026` 有 5,351 篇，`/2025` 有 3,703 篇，全部是 `conference`。
- 详情页（比如 `/paper_files/paper/2026/hash/0021c2cb1b9b6a71ac478ea52a93b25a-Abstract-Conference.html`）字段：标题、作者、摘要、`citation_pdf_url`、`citation_publication_date`=2026-04-20。没有 DOI，PDF 返回 200。样例 `samples/iclr-proc-2026-paper.html`。
- robots.txt 返回 404。
- OpenAlex `S4306419637` 的 2026 年有 4,535 条（占 85%），摘要 100%。

### ICRA

- 官方论文集在 IEEE Xplore。`https://ieeexplore.ieee.org/robots.txt` 写了 `Disallow: /rest` 和 `/ielx*`，并对 `Claude-User`、`ClaudeBot`、`GPTBot` 等 `Disallow: /`，所以没有访问 Xplore 的页面。
- IEEE API：不带 key 请求 `https://ieeexploreapi.ieee.org/api/v1/search/articles?...` 返回 403 "Developer Inactive"。条款页 `https://developer.ieee.org/API_Terms_of_Use2` 的相关原文：
  - 用途限于"non-commercial educational, research, or scientific activities within the Licensee's educational institution"；
  - "you shall not retain or store any Content for any reason once the License or these Terms are terminated"；
  - 要让终端用户承诺"not to retain or store the Content in bulk format, not to use any robot, spider…"；
  - 限额"set forth on the Rate Limits displayed during the user registration process"，要注册后才能看到。第三方资料说是每秒 10 次、每天 200 次，未核实。
- PaperCept（`https://ras.papercept.net/robots.txt`）：对 `*` 和 ClaudeBot、anthropic-ai 都是 `Disallow: /`。我在读 robots 之前取过一页 ICRA26 程序页，文件已删除，**不能用**。
- 推荐 OpenAlex：source `S4210217939` 的 2026 年有 1,770 条，摘要 99%，arXiv 版本 62%。样例 DOI `10.1109/ICRA57385.2026.11697074` 在 Crossref 上于 2026-10-07 注册，没有摘要，参考文献数为 0。
- 篇数：DBLP `icra2026` 1,874 条，`icra2025` 1,605 条。

### IROS

- 来源和限制同 ICRA。IROS 2025 的 Xplore DOI（`10.1109/IROS60139.2025.11245815`）在 2025-11-27 注册。
- OpenAlex `S4393918188` 的 2025 年有 1,988 条，摘要 100%，arXiv 40%；2026 年 0 条。Crossref 按刊名搜"2026 IEEE/RSJ International Conference on Intelligent Robots and Systems"，搜出来的是别的会。结论是 **IROS 2026 还没出版**。会议日期 2026-09-27~10-01（匹兹堡）来自第三方页面，未查官网。
- 篇数：DBLP `iros2025` 1,986 条。

### RSS

- `https://www.roboticsproceedings.org/`，robots.txt 是 `Allow: /`。`rss22/index.html` 是 RSS 2026，210 篇；`rss21` 是 2025，163 篇。
- 详情页 `rss22/p001.html` 有标题、作者、摘要、PDF（`p001.pdf`）、bibtex 和 DOI（`10.15607/RSS.2026.XXII.001`）。PDF 的 Last-Modified 是 2026-08-26，DOI 在 2026-08-31 注册。
- OpenAlex `S4306420803` 的 2026 年有 208 条，摘要 95%，arXiv 88%。
- 会议日期 2026-07-13~17（悉尼），下一届 2027-07-06~11（雅典），都来自 RSS 官网页面的搜索摘要，没有直接打开官网。

### ECCV

- `https://www.ecva.net/papers.php` 收了 2018、2020、2022、2024 四届；2024 届 2,387 篇，**没有 2026 届**。条目格式和 CVF 一样（`dt.ptitle`），有 PDF、补充材料和 Springer DOI 链接。详情页 `papers/eccv_2024/papers_ECCV/html/4_ECCV_2024_paper.php` 有摘要。2024 届 PDF 的 Last-Modified 是 2024-10-01。ecva.net 的 robots.txt 只写了 `User-agent: Googlebot / Allow: /`，没有禁止项。
- ECCV 2026 会议站 `https://eccv.ecva.net/Conferences/2026`：会期 9 月 8–12 日，在马尔默。
  - `…/AcceptedPapers` 写着"This paper list is preliminary pending publisher checks"，只有标题、作者、分区和海报位置。
  - `https://eccv.ecva.net/virtual/2026/papers.html` 有 2,834 个 poster 链接；逐篇 `/virtual/2026/poster/<id>` 页上有摘要（样例 `samples/eccv2026-poster.html`）。
  - robots.txt 只禁止 `/static`、`/search` 等，这些逐篇页是允许的。
- Springer 版：DBLP 有 `eccv2026-*` 共 78 个目录、2,913 条。样例 DOI `10.1007/978-3-032-36839-3_20` 在 Crossref 上于 2026-09-13 注册，没有摘要。OpenAlex 里 3 篇样例都在 LNCS 丛书的 source 下，**没有摘要**，但都关联到了 arXiv。S2 按 venue=ECCV、2026 查到 2,125 篇，第一页 1,000 篇里 71% 有摘要，几乎都是有 arXiv 版本的那些。

### ICCV

- `https://openaccess.thecvf.com/menu` 有 CVPR2026、CVPR2026_findings、CVPR2026_workshops、WACV2026、ICCV2025 等。`/ICCV2025?day=all` 有 2,701 篇，其中 1,682 篇带 arXiv 链接（62%）。PDF 的 Last-Modified 是 2025-09-27。
- Xplore 版 DOI（`10.1109/ICCV51701.2025.01630`）在 2026-04-29 注册。OpenAlex `S4363607764` 的 2025 年有 2,703 条，摘要 79%。
- CVF 站的 robots.txt 返回 404。

### CVPR

- `/CVPR2026?day=all`（12 MB）有 4,042 篇。每条有 pdf、supp（3,607 篇）、arXiv（2,653 篇，66%）、bibtex。详情页 `content/CVPR2026/html/…_CVPR_2026_paper.html` 的 `<div id="abstract">` 里是摘要（样例 `samples/cvf-cvpr2026-paper.html`）。PDF 的 Last-Modified 是 2026-05-16。
- `/CVPR2026_findings?day=all` 有 941 篇，路径是 `content/CVPR2026F/`。Crossref 上已经有 Xplore 版的"CVPR- FINDINGS Track"（`10.1109/cvprf71909.2026.*`，2026-10-08 注册）。
- OpenAlex：CVPR 的 source `S4210176548` 在 2026 年是 0 条；2025 年有 2,873 条，摘要 100%（Xplore 版，DOI 在 2025-08-13 注册）。按标题搜 CVPR 2026 论文只找到 arXiv 版，或者找不到。所以 **CVPR 只能走 CVF**。

### ACL / EMNLP

- Anthology 的数据说明页（`https://aclanthology.org/faq/api/`）写明：元数据在 GitHub 仓库的 `/data/xml` 下，PDF 在它们自己的服务器上，也可以用 `pip install acl-anthology`（项目环境里没有这个包，也没装）。
- `https://raw.githubusercontent.com/acl-org/acl-anthology/master/data/xml/2026.acl.xml` 有 7.4 MB，每篇都有 title、author（带 ORCID 和 OpenReview id）、abstract、pages、doi、pdf hash；卷上有 `ingest-date="2026-06-22"`。样例 `samples/acl-2026-xml.head.xml`。
- `2026.emnlp.xml`（7.9 MB）的 ingest-date 是 2026-10-21，地点布达佩斯；`2025.emnlp.xml` 的 ingest-date 是 2025-10-29，苏州。Findings 统一放在 `2026.findings.xml`（13.9 MB）里。
- 每卷的 bib（`/volumes/2026.acl-short.bib`）**没有摘要**。PDF `https://aclanthology.org/2026.acl-long.1.pdf` 返回 200。站点 robots.txt 返回 404。
- 篇数从事件页统计（已扣掉编号为 0 的前言）：
  - ACL 2026：acl-long 2,222，acl-short 74，findings-acl 2,163，industry 154，srw 109，demo 85；
  - EMNLP 2026：emnlp-main 2,704，findings-emnlp 2,478，industry 266，demos 86。
- OpenAlex：ACL 的 source `S4393915740` 在 2026 年有 2,641 条（和主会各卷合计 2,644 篇基本对上，Findings 不在里面），摘要 100%；EMNLP 的 source `S4363608991` 在 2026 年有 2,921 条，摘要 100%。

### AAAI

- `https://ojs.aaai.org/index.php/AAAI/oai?verb=Identify` 正常返回，robots.txt 只禁止 `/cache/`。OAI 的 set 是按分轨划分的，不是按年份。
- `GetRecord`（`oai_dc`）的样例：`dc:description` 是摘要；`dc:identifier` 是论文页和 DOI `10.1609/aaai.v40i1.36958`；`dc:source` 是"Vol. 40 No. 1: AAAI-26 Technical Tracks 1"；`dc:relation` 是 PDF 链接；`dc:date` 是 2026-03-17。样例 `samples/aaai-oai-record.xml`。
- 期目录 `issue/archive` 和 `issue/archive/2`：第 40 卷第 1–43 期是技术分轨，44–46 期是特别分轨，47、48 期是 New Faculty、Journal Track、IAAI、EAAI、学生摘要等。第 1 期有 94 篇。
- Crossref 上 AAAI 的记录**有摘要**，是抽查里唯一有摘要的出版方。OpenAlex `S4210191458` 的 2026 年有 4,914 条，摘要 100%，arXiv 27%。AAAI-25 的论文在 2025-04-11 上线。

### KDD

- ACM DL：robots.txt 允许 `/doi/`（`Crawl-delay: 1`），但请求 `https://dl.acm.org/doi/pdf/10.1145/3746027.3755317` 返回 403，带 `cf-mitigated: challenge`。所以**程序拿不到 PDF**，虽然搜索结果（ACM 官网和多所大学图书馆的公告）都说 ACM DL 从 2026-01-01 起全部开放获取，免费的 Basic 版就提供 PDF。
- DBLP：`kdd2026-1` 257 条、`kdd2026-2` 1,220 条（两轮投稿）。抽样里有"25th International Workshop on Data Mining in Bioinformatics (BIOKDD 2026)"这类 workshop 条目。Crossref 上的 DOI 分别在 2026-07-31 和 2026-08-06 注册，没有摘要。
- OpenAlex `S4363608767` 的 2026 年有 1,472 条，摘要 93%，arXiv 39%。

### ACM MM

- Crossref 上已经有"Proceedings of the 34th ACM International Conference on Multimedia"（`10.1145/3767308.*`，2026-11-05 注册）。OpenAlex `S4363608757` 的 2026 年有 1,901 条，摘要 98%，arXiv 43%。2025 届在 OpenAlex 有 1,573 条（摘要 94%），DBLP 有 1,621 条。DBLP 还没收 2026 届。
- 会期 2026-11-10~14（里约热内卢），下一届 2027 年在香港，来自 2026.acmmm.org 的搜索摘要。

## 5. 通用路线评估

### DBLP

- **收录**：14 个会议都有，每届一个目录页，主会和 workshop/Findings 分开（`samples/dblp-sparql-tocs.csv`）。
- **滞后**：截至 11-23，以下都还没收或只收了一部分：
  - ICML 2026（PMLR 9-29 就发布了）；
  - RSS 2025 和 2026；
  - EMNLP 2026 只收了 680 条，而全届约 5,500 篇，Anthology 10-21 就入库了；
  - ACM MM 2026（DOI 11-05 注册）。
  
  所以滞后从几周到一年以上不等。
- **没有摘要**。
- **接口**：
  - `https://dblp.org/search/publ/api`、`/db/conf/.../index.xml`、`dblp.uni-trier.de` 镜像都返回 202 和 Anubis 的"Making sure you're not a bot!"验证页，没有尝试绕过；
  - `https://sparql.dblp.org/sparql` 可以正常用，robots.txt 是 `Crawl-delay: 10` 加 `Allow: /sparql`；
  - 每日全量 `https://dblp.org/xml/dblp.xml.gz` 有 1.12 GB，Last-Modified 2026-11-21，没有下载；
  - 每月快照发布在 Dagstuhl DROPS 上。
- **用途**：核对篇数、列 DOI、给 NeurIPS 2025 这种索引页不全的情况兜底。不适合做主数据源。

### OpenAlex

- **限额**（实测响应头和 `https://help.openalex.org/access/example-costs.md`）：
  - 不带 key 时，同一出口 IP 共享每天 $0.10 的额度（`x-ratelimit-limit: 1000` 个 credit）；
  - 带筛选的列表请求每次 1 credit（$0.0001），每页最多 100 条；
  - 搜索 10 credit；
  - 按 DOI 或 ID 单查免费；
  - 免费 key 每天 $1。
  
  导入一届 5,000 篇大约 50 次请求，约 $0.005，不带 key 也够用。这次调研一共用了约 190 credit。申请 key 要填用户邮箱；我没有申请（第 7 节）。
- **能不能按会议和年份列出**：11 个可以，用 `primary_location.source.id` 加 `publication_year` 就能筛。以下不行：
  - ICML（PMLR 版没收）；
  - CVPR 2026（CVF 版没收，Xplore 版还没出）；
  - ECCV（混在 LNCS 丛书的 source 里，而且没有摘要）。
- **摘要覆盖和 arXiv 关联**（`samples/openalex-coverage.json`，`scripts/oa_counts.py`）：

| 会议 / 年份 | OpenAlex 条数 | 有摘要 | 有 arXiv 版本 |
| --- | --- | --- | --- |
| NeurIPS 2025 | 5,817 | 71% | 66% |
| ICLR 2026 | 4,535 | 100% | 80% |
| ICRA 2026 | 1,770 | 99% | 62% |
| IROS 2025 | 1,988 | 100% | 40% |
| RSS 2026 | 208 | 95% | 88% |
| ICCV 2025 | 2,703 | 79% | 78% |
| CVPR 2025（Xplore 版） | 2,873 | 100% | 68% |
| ACL 2026 | 2,641 | 100% | 69% |
| EMNLP 2026 | 2,921 | 100% | 71% |
| AAAI 2026 | 4,914 | 100% | 27% |
| KDD 2026 | 1,472 | 93% | 39% |
| ACM MM 2026 | 1,901 | 98% | 43% |
| ACM MM 2025 | 1,573 | 94% | 39% |
| ICML 2025/2026、CVPR 2026、IROS 2026 | 0 | — | — |

- **滞后**：DOI 注册或 Anthology 入库后，几天到几周就能在 OpenAlex 看到。
- **风险**：source 合并或更名；计费规则 2025 年刚改过，以后还可能变；KDD、MM 这类 source 里混有非论文条目；摘要来自出版方或抓取，具体来源未核实。

### Semantic Scholar

- 不带 key 时用的是共享额度：14 次 venue 查询里，NeurIPS 和 ICML 连续 3 次都是 429，ICRA 的第一次也是 429。
- 按 venue 和年份列出的篇数明显不全（`samples/s2-venue-coverage.json`）：
  - ICRA 2026 只有 1,249 篇（实际约 1,874）；
  - CVPR 2026 3,041 篇（实际 4,042）；
  - ICLR 2026 2,682 篇（实际 5,351）；
  - RSS 2026 只有 2 篇。
- CVF 和 OpenReview 系会议的摘要基本只在有 arXiv 版本时才有。
- 用途：`POST /graph/v1/paper/batch` 一次能查 500 个 DOI，适合补 arXiv 关联。

### Crossref

- 可以用 `query.container-title` 加日期筛选列出某届，公共额度 `x-rate-limit-limit: 5 / 1s`。
- 抽查的出版方里只有 AAAI 存了摘要，IEEE、ACM、Springer、ACL、NeurIPS（Curran）、RSS 都没有，所以**不能用来拿摘要**。
- 它最有用的是 `created` 时间，能看出论文集什么时候正式出版。上面的滞后数据大多是用它查的。

## 6. PDF 不开放的会议在 arXiv 上的比例

**方法**：
1. 用 DBLP SPARQL 从 ICRA 2026、IROS 2025、KDD 2026、ACM MM 2025 的目录里各随机抽 40 篇，按记录 IRI 加种子的 MD5 排序后取前 40。KDD 抽到的 1 条 workshop 条目去掉后剩 39 篇。
2. 用 OpenAlex 按 DOI 单查（免费），看 `locations` 里有没有 arXiv；再用 S2 的 batch 接口查 `externalIds.ArXiv`。两者取并集。
3. 对每个会议没被关联上的论文各取 6 篇（共 24 篇），直接在 arXiv API 里按标题逐词 AND 检索，标题相似度 ≥0.8 算命中，每次请求间隔 3.5 秒。

数据在 `samples/arxiv-coverage-sample.csv`、`samples/arxiv-direct-check.csv`，脚本是 `scripts/arxiv_cov.py`、`scripts/arxiv_check.py`。

| 会议 | 样本数 | OpenAlex 关联 | S2 关联 | 合计 | 整届口径（OpenAlex，偏低） |
| --- | --- | --- | --- | --- | --- |
| ICRA 2026 | 40 | 25 | 26 | 27（68%） | 62% |
| IROS 2025 | 40 | 16 | 19 | 19（48%） | 40% |
| KDD 2026 | 39 | 15 | 18 | 19（49%） | 39% |
| ACM MM 2025 | 40 | 16 | 20 | 20（50%） | 39% |

- 直接检索的 24 篇里命中 0 篇。有 2 篇找到了相似度约 0.7 的标题，人工判断不是同一篇论文，没有计入。按 95% 置信度估计，两个关联服务漏掉的比例不超过约 12%。
- 每个会只抽了 40 篇，误差大约 ±15 个百分点。
- 结论：这四个会大约一半的论文能拿到 arXiv PDF（ICRA 更高，约 2/3）；其余只能用摘要做简报，做不了全文报告。
- ECCV 的 Springer 版也不开放获取，不过 ECVA 的开放获取版一旦出来就不是问题。S2 上它有 arXiv 版本的比例约 71%。

## 7. 没能查清的地方，以及需要用户决定的事

**没能查清**：
1. NeurIPS 2025 的年份索引页只列出 Creative AI 分轨：是临时故障还是改版了，不清楚。
2. ECCV 2026 的开放获取版会不会、什么时候上 ecva.net，不清楚。2024 届是在会期内上线的，2026 届开完已经 2.5 个月还没有。
3. OpenReview 登录后脚本能不能正常访问、频率限制多少、使用条款允许什么，没有账号，所以没验证，条款也没看。
4. IEEE API 的实际限额要注册后才能看到；条款里"不得批量存储"对我们存摘要的影响需要人来判断。
5. ACM DL 有没有可用的官方元数据接口，没有查到；Cloudflare 拦截也可能只针对部分 UA 或 IP，没有尝试绕过。
6. OpenAlex 里 KDD、MM 混入的 workshop、tutorial、demo 条目有多少，怎样过滤最稳，只看到个别例子，没有统计。
7. OpenAlex 上 IEEE 论文的摘要从哪里来（IEEE 不往 Crossref 存摘要），以及由此带来的授权问题，没有核实。
8. 部分会议日期没有逐个打开官网核对，表里标了"未核实"，包括 NeurIPS 2026、ICML 2027、ICLR 2027、ICRA 2027、AAAI-26/27 等；IROS 2026、RSS 2026/2027、KDD 2026、MM 2026 的日期来自搜索摘要。
9. S2 按 venue 查 NeurIPS 和 ICML 时一直被限流，没拿到数据。

**需要用户决定**：
1. **"主会"的口径**：Findings 和 workshop 已经确定不要。下面这些算不算主会还要定：
   - AAAI 的特别分轨（AI Alignment、AI for Social Impact）；
   - ACL/EMNLP 的 short、industry、demo、SRW；
   - NeurIPS 的 Datasets & Benchmarks 和 position paper 分轨；
   - ICML 的 position paper（PMLR 里区分不出来）；
   - KDD 的 ADS 分轨。
2. **要不要提供 OpenReview 账号**：有账号的话，ICLR、NeurIPS、ICML 在录用公布后就能拿到，并带上 oral/poster 标记；NeurIPS 能提前大约 8 个月。没有账号就只能等正式论文集。
3. **OpenAlex 要不要申请免费 key**：申请要用用户邮箱，每天 $1；不申请时同一 IP 共享每天 $0.10，按现在的用量也够。
4. **没有 arXiv 版本的 IEEE/ACM 论文**（大约一半）：是接受只做简报，还是需要人工补 PDF。
5. **IEEE Xplore API**：鉴于条款限制，建议不用，请确认。
6. **ECCV 2026 怎么拿**：
   - 等 ECVA 的开放获取版；
   - 或者现在逐篇抓 eccv.ecva.net 的海报页，大约 2,800 次请求，有摘要，没有 PDF；
   - 或者先用 OpenAlex/S2 的列表加 arXiv，约 70% 有摘要。
7. **NeurIPS 2025 怎么拿**：
   - 用 DBLP 的列表配合逐篇抓 proceedings 详情页；
   - 或者直接用 OpenAlex，71% 有摘要；
   - 或者等索引页修好。
8. **首批导入哪一届**：建议按总表的"最新一届"来，其中 IROS 用 2025 届（2026 届还没出版），ECCV、NeurIPS 见上面两条。

## 8. 调研过程中的偏差（如实记录）

- 读 robots.txt 之前请求过两次后来发现被禁止的地址：`neurips.cc/static/...json` 一次、PaperCept 程序页一次。两个文件都已删除，报告里也没有采用这两条路线。
- DBLP SPARQL 的 robots.txt 要求间隔 10 秒，我的约 19 次查询中有一部分只间隔了 4 秒。
- arXiv API 一共请求了 50 次，间隔都在 3.5 秒以上。其中第一轮 24 次因为检索写法有误，结果全部作废并重跑了。
- 所有请求的 UA 都是 `ScholarMind-research/0.1`，只有一次 OpenReview 测试用了浏览器 UA，结果同样被拦。
