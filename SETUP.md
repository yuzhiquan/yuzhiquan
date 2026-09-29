# 部署步骤

仓库 `yuzhiquan/yuzhiquan` 已存在（public，默认分支 **master**），README 会自动显示在主页。

## 1. 开启 Actions 写权限

Settings → Actions → General → Workflow permissions → 选 **Read and write permissions** → Save。

## 2. 手动触发一次

到 Actions 页 → Update profile README → Run workflow，约 30 秒后 README 的动态区块就会被真实数据填充。之后每 30 分钟自动刷新。

## 3. 本机继续改

```bash
git clone https://github.com/yuzhiquan/yuzhiquan.git
# 改完 git add / commit / push，分支是 master 不是 main
```

---

# 文件说明

| 文件 | 作用 |
| --- | --- |
| `README.md` | 主页内容，4 个动态区块用占位符注释包裹 |
| `.github/workflows/update-readme.yml` | 定时任务：跑脚本 → 提交；另含每月空提交保活 |
| `.github/scripts/gen_activity.py` | 拉数据 + 替换占位符，纯标准库无依赖 |
| `.github/scripts/gen_stats.py` | 生成 `assets/` 下三张统计 SVG，纯标准库无依赖 |
| `assets/*.svg` | 统计卡片 / 语言分布 / 贡献热力图，由 workflow 重新生成并提交 |
| `data/articles.yml` | 跨平台文章源（知乎、LinkedIn、公众号等手动维护） |

# 常见问题

**为什么不用现成的 `github-activity-readme` action？**
它的事件源是 `/events/public`，而 GitHub 已经把 `PushEvent` 里的 `commits` 明细剥离掉了（只剩 `ref/head/before`），拿不到 commit message。本脚本改用 `/search/commits` 按 committer-date 排序，能取到真实的 commit 标题和仓库。

**cron 时间**
`*/30 * * * *` 是 UTC。想改成北京时区的每天 9 点，写 `0 1 * * *`。GitHub 高峰期会延迟几十分钟，属于正常现象。

**私有仓库的贡献统计不到**
默认的 `GITHUB_TOKEN` 只能搜公开数据。要包含私有仓库，建一个 classic PAT（勾 `repo`），存到 Settings → Secrets → `GH_PAT`，再把 workflow 里的 `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` 换成 `${{ secrets.GH_PAT }}`。

**统计卡片为什么不显示？（已解决）**
原来引用的是 `github-readme-stats.vercel.app` / `github-readme-activity-graph.vercel.app` / `github-readme-streak-stats.herokuapp.com` 三个公共实例：
前两者是共享免费额度，常年 503 限流；**herokuapp 那个已经彻底下线**（Heroku 免费 dyno 2022-11 停服）。
而且 GitHub 会用自己的 camo 代理抓取外链图片，**一旦第一次抓取失败，失败结果会被缓存很久**，之后即使服务恢复也仍然显示空白——这就是"一直不显示"的根因。

现在的做法是**自托管**：workflow 直接从 GitHub 官方 REST + GraphQL 取数，在本地生成 SVG 提交进 `assets/`，README 用相对路径引用，由 GitHub 自己的 CDN 提供。不依赖任何第三方实例，也不会被 camo 缓存失败。SVG 内置 `prefers-color-scheme`，明暗主题自适应。

**`Total Commits` 数字为什么会跳？**
用默认的 `GITHUB_TOKEN` 时，Search API 的索引可见性不完整，两次运行可能差几个百分点。想要稳定准确，建一个**只读**的 PAT（无需任何写权限，公开数据即可）存到 Settings → Secrets → `GH_STATS_TOKEN`，workflow 已配置优先使用它。

**语言分布里 HTML/CSS 占比过高**
`EXCLUDE_REPOS` 已排除 `tech-review-notes` 和 `yuzhiquan.github.io`——这两个是博客/笔记仓库，里面 vendored 了整套前端主题，按字节统计会把 HTML/CSS 顶到第一，掩盖真实的 Go 占比。想调整就改 workflow 里的这个变量。

**Writing 区块突然变成「暂无文章」**
`articles.yml` 里 **title / date 必须加双引号**。标题中若出现半角冒号加空格（例如
`A four-stage LLM pipeline on Kubernetes: Ray + PyTorch + vLLM`），不加引号会让 YAML 解析失败，
整个区块退化成 `_暂无文章_`。
这个坑本地发现不了——脚本在没装 pyyaml 时会走内置简易解析器（能容错），
而 Actions 里装了 pyyaml，解析失败就直接返回空。改完 yml 建议本地 `pip install pyyaml` 后再跑一次脚本验证。

**博客链接不对**
`atom.xml` 里是 Hexo 默认的 `http://yoursite.com`。脚本已通过 `BLOG_SITE` 环境变量纠正成真实域名；**根治办法**是改博客 `_config.yml` 的 `url: https://yuzhiquan.github.io` 后重新生成。

# 可调参数（workflow 的 env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MAX_COMMITS` | 5 | 展示的 commit 条数 |
| `MAX_PRS` | 5 | 展示的 PR 条数 |
| `MAX_POSTS` | 5 | 展示的文章条数 |
| `SKIP_PATTERNS` | `Add files via upload,Initial commit` | commit 标题命中前缀则跳过，避免刷屏 |
| `MAX_WRITING` | 6 | Writing 区块**直接展示**的条数，其余自动折叠进 `<details>` |
| `BLOG_FEED` | 空 | RSS 地址；**已停用**（博客停更），Writing 现在只由 `articles.yml` 驱动，填回地址即可恢复 |
| `BLOG_SITE` | `https://yuzhiquan.github.io` | 纠正 RSS 里的错误域名 |
