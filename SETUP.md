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

# 常见问题

**为什么不用现成的 `github-activity-readme` action？**
它的事件源是 `/events/public`，而 GitHub 已经把 `PushEvent` 里的 `commits` 明细剥离掉了（只剩 `ref/head/before`），拿不到 commit message。本脚本改用 `/search/commits` 按 committer-date 排序，能取到真实的 commit 标题和仓库。

**cron 时间**
`*/30 * * * *` 是 UTC。想改成北京时区的每天 9 点，写 `0 1 * * *`。GitHub 高峰期会延迟几十分钟，属于正常现象。

**私有仓库的贡献统计不到**
默认的 `GITHUB_TOKEN` 只能搜公开数据。要包含私有仓库，建一个 classic PAT（勾 `repo`），存到 Settings → Secrets → `GH_PAT`，再把 workflow 里的 `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` 换成 `${{ secrets.GH_PAT }}`。

**统计卡片不显示 / 长期不更新**
`github-readme-stats` 等公共实例有限流，建议 fork 到自己的 Vercel，把 README 里的域名换掉。GitHub 对图片有 camo 代理缓存，改完可能要等一会儿才生效。

**博客链接不对**
`atom.xml` 里是 Hexo 默认的 `http://yoursite.com`。脚本已通过 `BLOG_SITE` 环境变量纠正成真实域名；**根治办法**是改博客 `_config.yml` 的 `url: https://yuzhiquan.github.io` 后重新生成。

# 可调参数（workflow 的 env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MAX_COMMITS` | 5 | 展示的 commit 条数 |
| `MAX_PRS` | 5 | 展示的 PR 条数 |
| `MAX_POSTS` | 5 | 展示的文章条数 |
| `SKIP_PATTERNS` | `Add files via upload,Initial commit` | commit 标题命中前缀则跳过，避免刷屏 |
| `BLOG_FEED` | `https://yuzhiquan.github.io/atom.xml` | RSS 地址，置空则不展示文章 |
| `BLOG_SITE` | `https://yuzhiquan.github.io` | 纠正 RSS 里的错误域名 |
