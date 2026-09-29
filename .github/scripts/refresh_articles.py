"""扫描源仓库，把新文章补进 data/articles.yml（已有条目保留并更新）。

用途：往源仓库（含私有仓库）推了新文章后，在 Actions 页面手动跑一次
`Refresh article list`，就能把新文件自动补进主页的三个区块。

为什么是手动而不是每 30 分钟跑：
  - 扫一遍要 100+ 次 API 请求，跟着定时任务跑会撞 GitHub 限流（5000/小时）；
  - 私有仓库只有 PAT 能读，需要仓库 Secret GH_ARTICLES_TOKEN；没配就只扫公开仓库。

用法：
  GH_ARTICLES_TOKEN=<PAT> python .github/scripts/refresh_articles.py
"""

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("GH_ARTICLES_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
OUT = os.environ.get("ARTICLES_FILE", "data/articles.yml")
UA = "article-refresh-bot"

HEADER = """# 主页三大精选区块的文章源
#
# 三个分组分别对应 README 里的三个区块（占位符名见下）：
#   ai-infra  -> <!--START_SECTION:aiinfra-->     AI Infra
#   ai-agent  -> <!--START_SECTION:aiagent-->     AI Agent
#   interview -> <!--START_SECTION:interview-->   面试准备
#
# 这个文件由 .github/scripts/refresh_articles.py 生成与维护
# （Actions → Refresh article list → Run workflow 手动触发）。
# 也可以手工改，push 后定时任务会在下一次运行时渲染到 README。
#
# ⚠️ title 和 date 必须加双引号：
#   1. 标题里的半角冒号加空格（如 "X: Y"）不加引号会让 YAML 解析失败，
#      整个区块会退化成「暂无文章」；
#   2. date 不加引号会被 pyyaml 解析成 date 对象，排序时和字符串比较会炸。
#
# 中文路径的链接是百分号编码，直接写中文会 404。
# ai-infra-book-analysis / MiniAgent-tutorial / havesomefun 是私有仓库，
# 访客点开链接会看到 404，这是预期行为。
"""

# dir 为 "" 表示整个仓库；exclude 是路径片段黑名单
SOURCES = {
    "ai-infra": [
        {"repo": "yuzhiquan/ai-infra-book-analysis", "branch": "main",
         "dir": "beginner-series", "label": "入门系列", "pick": "zh"},
        {"repo": "yuzhiquan/ai-infra-book-analysis", "branch": "main",
         "dir": "blogs", "label": "博客", "pick": "zh"},
    ],
    "ai-agent": [
        {"repo": "yuzhiquan/MiniAgent-tutorial", "branch": "main", "dir": "",
         "label": "MiniAgent 源码", "exclude": ["tutorials_en/"]},
        {"repo": "yuzhiquan/agent-from-scratch", "branch": "main", "dir": "",
         "label": "从零实现"},
    ],
    "interview": [
        {"repo": "yuzhiquan/tech-review-notes", "branch": "main", "dir": "",
         "label": "GPU 调度"},
        {"repo": "yuzhiquan/havesomefun", "branch": "master", "dir": "",
         "label": "算法手册",
         "exclude": ["README.md", "havesomefun-仓库分析报告.md", "21天日程表.md",
                     "恢复计划.md"]},
    ],
}


def get(url, retries=3):
    """带重试的 GET：代理抖动 / 偶发 502 会让单次请求失败。"""
    h = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    last = None
    for attempt in range(retries):
        try:
            return json.load(urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=45))
        except urllib.error.HTTPError as exc:
            last = f"{exc.code} {url}"
            if exc.code in (401, 403, 404):
                break                      # 权限/不存在，重试没意义
        except Exception as exc:           # URLError / 超时 / 代理 502
            last = f"{exc} {url}"
        time.sleep(2 * (attempt + 1))
    print(f"  ! {last}")
    return None


def md_files(repo, branch, sub):
    tree = get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1")
    if not tree or "tree" not in tree:
        return []
    paths = [t["path"] for t in tree["tree"] if t["path"].lower().endswith(".md")]
    if sub:
        paths = [p for p in paths if p.startswith(sub + "/")]
    return paths


def wanted(path, src):
    name = os.path.basename(path)
    for bad in src.get("exclude", []):
        if bad in path:
            return False
    if src.get("pick") == "zh":
        # 中英双语目录：只收中文版和目录索引
        return name == "README.md" or ".zh-CN.md" in name
    return True


def first_heading(repo, branch, path):
    d = get(f"https://api.github.com/repos/{repo}/contents/"
            f"{urllib.parse.quote(path)}?ref={branch}")
    if not d or "content" not in d:
        return ""
    text = base64.b64decode(d["content"]).decode("utf-8", "ignore")
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return re.sub(r"^#+\s*", "", s).strip()
    return os.path.basename(path).rsplit(".", 1)[0]


def last_date(repo, branch, path):
    d = get(f"https://api.github.com/repos/{repo}/commits?path="
            f"{urllib.parse.quote(path)}&sha={branch}&per_page=1")
    if not d:
        return ""
    return (d[0]["commit"]["committer"]["date"] or "")[:10]


def scan():
    found = {}
    for group, srcs in SOURCES.items():
        rows = []
        for src in srcs:
            print(f"scan {src['repo']}/{src['dir'] or '*'}")
            for path in md_files(src["repo"], src["branch"], src["dir"]):
                if not wanted(path, src):
                    continue
                title = first_heading(src["repo"], src["branch"], path)
                if not title:
                    continue
                rows.append({
                    "title": title,
                    "url": f"https://github.com/{src['repo']}/blob/{src['branch']}/"
                           f"{urllib.parse.quote(path)}",
                    "date": last_date(src["repo"], src["branch"], path),
                    "source": src["label"],
                })
        found[group] = rows
        print(f"  -> {len(rows)} 篇")
    return found


def load_existing():
    if not os.path.exists(OUT):
        return {}
    try:
        import yaml
    except ImportError:
        print("pyyaml 未安装，跳过合并（将完全按扫描结果重写）")
        return {}
    data = yaml.safe_load(open(OUT, encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, list)}


def merge(existing, found):
    """按 URL 合并：新扫到的覆盖同 URL 旧条目，已有但本次没扫到的保留。"""
    out = {}
    for group in SOURCES:
        by_url = {}
        for item in existing.get(group, []):
            if isinstance(item, dict) and item.get("url"):
                by_url[item["url"].rstrip("/")] = item
        for item in found.get(group, []):
            by_url[item["url"].rstrip("/")] = item
        rows = list(by_url.values())
        rows.sort(key=lambda r: str(r.get("date", "")), reverse=True)
        out[group] = rows
    # 保留不在 SOURCES 里的自定义分组
    for group, rows in existing.items():
        if group not in out:
            out[group] = rows
    return out


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump(groups):
    lines = [HEADER]
    for group, rows in groups.items():
        lines.append(f"{group}:")
        for r in rows:
            lines.append(f"  - title: {q(r.get('title', ''))}")
            lines.append(f"    url: {q(r.get('url', ''))}")
            lines.append(f"    date: {q(r.get('date', ''))}")
            lines.append(f"    source: {q(r.get('source', ''))}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main():
    found = scan()
    existing = load_existing()
    merged = merge(existing, found)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(dump(merged))
    print("written", OUT, {k: len(v) for k, v in merged.items()})


if __name__ == "__main__":
    main()
