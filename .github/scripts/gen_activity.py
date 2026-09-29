"""生成「最近 commit / 最近 PR / 精选文章」并替换 README 中的占位符。

占位符（注释行不要改动）：
    <!--START_SECTION:commits--> ... <!--END_SECTION:commits-->
    <!--START_SECTION:prs-->     ... <!--END_SECTION:prs-->
    <!--START_SECTION:writing--> ... <!--END_SECTION:writing-->
    <!--START_SECTION:updated--> ... <!--END_SECTION:updated-->

数据源说明：
  - /users/{u}/events/public 的 PushEvent 已被 GitHub 剥离 commits 明细（只剩 ref/head/before），
    因此最近 commit 改用 /search/commits（按 committer-date 排序）；
  - 最近 PR 用 /search/issues?q=...type:pr（覆盖所有仓库，比 events 更全）；
  - 两者失败时自动回退到 events（PushEvent 生成 compare 链接、PullRequestEvent 生成 PR 链接）；
  - 文章 = data/articles.yml（手动维护，知乎/LinkedIn/公众号等任意来源）+ 博客 RSS，
    按日期倒序混排。知乎和 LinkedIn 没有可用 RSS，所以必须靠 articles.yml。
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

USERNAME = os.environ.get("GH_USERNAME", "yuzhiquan")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
FEED = os.environ.get("BLOG_FEED", "https://yuzhiquan.github.io/atom.xml")
README = os.environ.get("TARGET_FILE", "README.md")
MAX_COMMITS = int(os.environ.get("MAX_COMMITS", "5"))
MAX_PRS = int(os.environ.get("MAX_PRS", "5"))
MAX_POSTS = int(os.environ.get("MAX_POSTS", "5"))
MAX_WRITING = int(os.environ.get("MAX_WRITING", "6"))
# 手动维护的跨平台文章源（知乎 / LinkedIn / 公众号等），与博客 RSS 混排
ARTICLES_FILE = os.environ.get("ARTICLES_FILE", "data/articles.yml")
BLOG_LABEL = os.environ.get("BLOG_LABEL", "Blog")
# 博客 RSS 里若写成 Hexo 默认的 http://yoursite.com，用该变量把域名纠正过来
BLOG_SITE = os.environ.get("BLOG_SITE", "")
# commit message 命中这些前缀时跳过（逗号分隔），避免刷屏 "Add files via upload"
SKIP = [s.strip() for s in os.environ.get("SKIP_PATTERNS", "Add files via upload,Initial commit").split(",") if s.strip()]

UA = f"profile-readme-bot/{USERNAME}"


def get(url, headers=None):
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def clean(text, limit=72):
    text = " ".join(str(text).split("\n")[0].split())
    text = text.replace("|", "\\|").replace("[", "(").replace("]", ")")
    return text[:limit] + ("..." if len(text) > limit else "")


def repo_of(url):
    parts = url.rstrip("/").split("/")
    return "/".join(parts[-2:])


def fetch_events():
    try:
        return get(f"https://api.github.com/users/{USERNAME}/events/public?per_page=100")
    except Exception as exc:
        print(f"events failed: {exc}")
        return []


def fallback_commits(events):
    out, seen = [], set()
    for e in events:
        if e.get("type") != "PushEvent" or len(out) >= MAX_COMMITS:
            continue
        p = e["payload"]
        repo = e.get("repo", {}).get("name", "")
        key = f"{repo}{p.get('head')}"
        if key in seen:
            continue
        seen.add(key)
        branch = p.get("ref", "").split("/")[-1]
        link = f"https://github.com/{repo}/compare/{p.get('before','')[:7]}...{p.get('head','')[:7]}"
        out.append(f"- pushed to `{branch}` · `{repo}` · [compare]({link})")
    return out


def fallback_prs(events):
    out, seen = [], set()
    for e in events:
        if e.get("type") != "PullRequestEvent" or len(out) >= MAX_PRS:
            continue
        p = (e["payload"] or {}).get("pull_request") or {}
        url = p.get("html_url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(f"- [#{p.get('number')}]({url}) {clean(p.get('title', ''))} "
                   f"· `{e.get('repo', {}).get('name', '')}`")
    return out


def fix_link(link):
    """把 RSS 里的默认域名（如 http://yoursite.com）换成真实博客地址。"""
    if not BLOG_SITE or not link.startswith("http"):
        return link
    parts = urllib.parse.urlsplit(link)
    base = urllib.parse.urlsplit(BLOG_SITE)
    return urllib.parse.urlunsplit((base.scheme, base.netloc, parts.path, "", ""))


def skipped(message):
    first = message.split("\n")[0].strip()
    return any(first.startswith(p) for p in SKIP)


def fetch_activity(events):
    # 最近 commit
    try:
        q = urllib.parse.quote(f"author:{USERNAME}")
        data = get(f"https://api.github.com/search/commits?q={q}&sort=committer-date"
                   f"&order=desc&per_page={min(MAX_COMMITS * 4, 100)}")
        commits = [
            f"- [`{it['sha'][:7]}`](https://github.com/{it['repository']['full_name']}"
            f"/commit/{it['sha']}) {clean(it['commit']['message'])} "
            f"· `{it['repository']['full_name']}`"
            for it in data.get("items", [])
            if not skipped(it["commit"]["message"])
        ][:MAX_COMMITS]
    except urllib.error.HTTPError as exc:
        print(f"commit search failed ({exc.code}), fallback to events")
        commits = fallback_commits(events)

    # 最近 PR
    try:
        q = urllib.parse.quote(f"author:{USERNAME} type:pr")
        data = get(f"https://api.github.com/search/issues?q={q}&sort=updated"
                   f"&order=desc&per_page={MAX_PRS}")
        prs = []
        for it in data.get("items", []):
            merged = (it.get("pull_request") or {}).get("merged_at")
            state = "merged" if merged else it.get("state", "")
            prs.append(f"- [#{it['number']}]({it['html_url']}) {clean(it['title'])} "
                       f"· `{repo_of(it['repository_url'])}` · _{state}_")
    except urllib.error.HTTPError as exc:
        print(f"pr search failed ({exc.code}), fallback to events")
        prs = fallback_prs(events)

    return commits, prs


def norm_date(text):
    """统一日期为 YYYY-MM-DD，方便排序；解析不了就原样截断。"""
    s = (text or "").strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    try:
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


def fetch_posts():
    """博客 RSS（可选）。返回结构化 dict 列表，便于和 articles.yml 混排。"""
    if not FEED:
        return []
    req = urllib.request.Request(FEED, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    posts = []
    if root.tag.endswith("feed"):  # Atom
        for entry in root.findall("a:entry", ns)[:MAX_POSTS]:
            posts.append({
                "title": entry.findtext("a:title", "", ns),
                "url": fix_link(entry.find("a:link", ns).get("href")),
                "date": norm_date(entry.findtext("a:updated", "", ns)),
                "source": BLOG_LABEL,
            })
    else:  # RSS 2.0
        for item in root.findall(".//item")[:MAX_POSTS]:
            posts.append({
                "title": item.findtext("title", ""),
                "url": fix_link(item.findtext("link", "")),
                "date": norm_date(item.findtext("pubDate", "")),
                "source": BLOG_LABEL,
            })
    return posts


def load_articles(path):
    """读取手动维护的 articles.yml；没装 pyyaml 就走简易解析。"""
    if not os.path.exists(path):
        print(f"no articles file: {path}")
        return []
    try:
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8"))
        # 兼容顶层列表和 {articles: [...]} 两种写法
        if isinstance(data, dict):
            data = data.get("articles") or data.get("items") or []
        return data or []
    except ImportError:
        pass
    except Exception as exc:
        print(f"yaml parse failed: {exc}")
        return []

    items, cur = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n")
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw.startswith("- "):
                if cur:
                    items.append(cur)
                cur = {}
                rest = raw[2:].strip()
                if ":" in rest:
                    k, v = rest.split(":", 1)
                    cur[k.strip()] = v.strip().strip("\"'")
            elif cur is not None and ":" in raw:
                k, v = raw.strip().split(":", 1)
                cur[k.strip()] = v.strip().strip("\"'")
    if cur:
        items.append(cur)
    return items


def url_key(url):
    return (url or "").strip().rstrip("/").lower()


def build_writing(articles, posts):
    """articles.yml + 博客 RSS 合并，按 URL 去重，按日期倒序，渲染成 Markdown 列表。"""
    rows, seen = [], set()
    for a in articles or []:
        if not a.get("title") or not a.get("url"):
            continue
        key = url_key(a["url"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "title": a["title"],
            "url": a["url"],
            "date": norm_date(str(a.get("date", ""))),
            "source": a.get("source", ""),
        })
    for p in posts:
        if not p.get("title") or not p.get("url"):
            continue
        key = url_key(p["url"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(p)
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)

    def line(r):
        date = f" · {r['date']}" if r.get("date") else ""
        src = f" · `{r['source']}`" if r.get("source") else ""
        return f"- [{clean(r['title'])}]({r['url']}){date}{src}"

    body = "\n".join(line(r) for r in rows[:MAX_WRITING])
    rest = rows[MAX_WRITING:]
    if rest:
        # 文章较多时把其余的折叠起来：<details> 内需空行，GitHub 才会渲染其中的 Markdown
        more = "\n".join(line(r) for r in rest)
        body += (f"\n\n<details>\n<summary><b>📂 展开其余 {len(rest)} 篇</b>"
                 f"（共 {len(rows)} 篇）</summary>\n\n{more}\n\n</details>")
    return body


def replace(text, name, body, inline=False):
    pattern = rf"<!--START_SECTION:{name}-->.*?<!--END_SECTION:{name}-->"
    block = f"<!--START_SECTION:{name}-->{body}<!--END_SECTION:{name}-->" if inline \
        else f"<!--START_SECTION:{name}-->\n{body}\n<!--END_SECTION:{name}-->"
    return re.sub(pattern, block, text, flags=re.S)


def main():
    events = fetch_events()
    commits, prs = fetch_activity(events)
    try:
        posts = fetch_posts()
    except Exception as exc:
        print(f"feed skipped: {exc}")
        posts = []

    articles = load_articles(ARTICLES_FILE)
    writing = build_writing(articles, posts)

    with open(README, encoding="utf-8") as f:
        content = f.read()
    content = replace(content, "commits", "\n".join(commits) or "_暂无公开 commit_")
    content = replace(content, "prs", "\n".join(prs) or "_暂无公开 PR_")
    content = replace(content, "writing", writing or "_暂无文章_")
    content = replace(content, "updated",
                      datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), inline=True)
    with open(README, "w", encoding="utf-8") as f:
        f.write(content)
    total_writing = writing.count("\n- [") + (1 if writing.startswith("- [") else 0)
    print(f"done: {len(commits)} commits / {len(prs)} PRs / {total_writing} writing")


if __name__ == "__main__":
    main()
