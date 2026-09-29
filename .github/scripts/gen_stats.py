"""生成个人主页用的三张统计卡片 SVG，写入 assets/。

设计目标：彻底不依赖第三方统计服务（github-readme-stats / streak-stats /
activity-graph 的公共实例常年 503 限流，且 GitHub camo 会缓存失败结果导致图片空白）。

数据源全部来自 GitHub 官方：
  - REST   /users/{u}                        基础信息
  - REST   /users/{u}/repos                  star 数、语言
  - REST   /repos/{o}/{r}/languages          语言字节数
  - REST   /search/commits, /search/issues   总 commit / PR 数
  - GraphQL contributionsCollection          近一年贡献日历（53 周 x 7 天）

生成的 SVG 提交进仓库，README 用相对路径引用，由 GitHub 自己的 CDN 提供，
不存在跨域、限流、代理失败问题。SVG 内置 prefers-color-scheme，明暗主题自适应。

用法：
    GH_USERNAME=yuzhiquan GITHUB_TOKEN=xxx python .github/scripts/gen_stats.py
可选环境变量：
    ASSETS_DIR   输出目录，默认 assets
    LANG_REPOS   统计语言的仓库数量上限（按 star 排序），默认 40
    LANG_COUNT   展示的语言数量，默认 6
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
ASSETS_DIR = os.environ.get("ASSETS_DIR", "assets")
LANG_REPOS = int(os.environ.get("LANG_REPOS", "40"))
LANG_COUNT = int(os.environ.get("LANG_COUNT", "6"))
# 博客/笔记类仓库里 vendored 了整套前端主题，按字节统计会把 HTML/CSS 顶到第一，
# 用 EXCLUDE_REPOS 排除（逗号分隔的 full_name）。
EXCLUDE_REPOS = {s.strip() for s in os.environ.get("EXCLUDE_REPOS", "").split(",") if s.strip()}

UA = f"profile-readme-bot/{USERNAME}"
REST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": UA,
    "X-GitHub-Api-Version": "2022-11-28",
}
if TOKEN:
    REST_HEADERS["Authorization"] = f"Bearer {TOKEN}"

# GitHub 官方贡献热力图配色（浅色 / 深色）
LEVELS = ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]
LIGHT_Q = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]
DARK_Q = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]

# 常见语言配色（GitHub linguist 风格）
LANG_COLORS = {
    "Go": "#00ADD8", "Python": "#3572A5", "Shell": "#89e051", "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6", "Java": "#b07219", "C": "#555555", "C++": "#f34b7d",
    "C#": "#178600", "Rust": "#dea584", "HTML": "#e34c26", "CSS": "#563d7c",
    "Makefile": "#427819", "Dockerfile": "#384d54", "Ruby": "#701516",
    "PHP": "#4F5D95", "Kotlin": "#A97BFF", "Swift": "#F05138", "Lua": "#000080",
    "Perl": "#0298c3", "Scala": "#c22d40", "Dart": "#00B4AB", "Jupyter Notebook": "#DA5B0B",
    "Vue": "#41b883", "Svelte": "#ff3e00", "Jinja": "#a52a22", "HCL": "#844FBA",
    "Smarty": "#f0c040", "Batchfile": "#C1F12E", "PowerShell": "#012456",
    "Objective-C": "#438eff", "R": "#198CE7", "TeX": "#3D6117", "Nginx": "#009639",
}


# --------------------------------------------------------------------------- IO

def get_json(url):
    req = urllib.request.Request(url, headers=REST_HEADERS)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.load(resp)


def graphql(query, variables):
    if not TOKEN:
        raise RuntimeError("GraphQL requires a token")
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.load(resp)
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False)[:200])
    return data["data"]


def safe(label, fn, default=None):
    """任一数据源失败都不让整个任务挂掉。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] {label} failed: {exc}")
        return default


# ------------------------------------------------------------------------ data

def fetch_user():
    return get_json(f"https://api.github.com/users/{USERNAME}")


def fetch_repos():
    repos, page = [], 1
    while True:
        batch = get_json(
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?per_page=100&page={page}&sort=updated")
        repos.extend(batch)
        if len(batch) < 100 or page >= 5:
            break
        page += 1
    return repos


def fetch_languages(repos):
    """按 star 排序取前 N 个仓库，逐个取语言字节数并累加。"""
    owned = [r for r in repos if not r.get("fork") and r["full_name"] not in EXCLUDE_REPOS]
    owned.sort(key=lambda r: (r.get("stargazers_count", 0), r.get("size", 0)), reverse=True)
    tally, done = {}, 0
    for repo in owned:
        if done >= LANG_REPOS:
            break
        try:
            data = get_json(repo["languages_url"])
        except urllib.error.HTTPError:
            continue
        done += 1
        for lang, size in data.items():
            tally[lang] = tally.get(lang, 0) + size
    return tally


def fetch_counts():
    """总 commit 数与总 PR 数（Search API）。"""
    def commits():
        d = get_json("https://api.github.com/search/commits"
                     f"?q=author:{USERNAME}&per_page=1")
        return d.get("total_count", 0)

    def prs():
        d = get_json("https://api.github.com/search/issues"
                     f"?q=author:{USERNAME}+type:pr&per_page=1")
        return d.get("total_count", 0)

    return commits(), prs()


CALENDAR_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""


SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


def scrape_calendar():
    """GraphQL 不可用时的回退：抓 GitHub 官方贡献页片段，解析 data-date / data-level。"""
    url = f"https://github.com/users/{USERNAME}/contributions"
    req = urllib.request.Request(url, headers=SCRAPE_HEADERS)
    with urllib.request.urlopen(req, timeout=45) as resp:
        html = resp.read().decode("utf-8", "ignore")

    found = re.findall(r'data-date="(\d{4}-\d{2}-\d{2})"[^>]*?data-level="(\d)"', html)
    if not found:
        found = re.findall(r'data-level="(\d)"[^>]*?data-date="(\d{4}-\d{2}-\d{2})"', html)
        found = [(d, lv) for lv, d in found]
    if not found:
        raise RuntimeError("no contribution cells parsed")

    days, total = [], 0
    for datestr, level in found:
        count_by_level = {0: 0, 1: 1, 2: 3, 3: 6, 4: 10}
        count = count_by_level.get(int(level), 0)
        total += count
        days.append({
            "date": datestr,
            "contributionCount": count,
            "contributionLevel": LEVELS[int(level)],
        })
    return total, days


def fetch_calendar():
    try:
        data = graphql(CALENDAR_QUERY, {"login": USERNAME})
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] graphql calendar failed ({exc}), fall back to scraping")
        return scrape_calendar()
    cal = data["user"]["contributionsCollection"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]
    return cal["totalContributions"], days


# ------------------------------------------------------------------- svg utils

def fmt(n):
    return f"{n:,}" if isinstance(n, int) else "—"


def svg_open(width, height, title):
    """统一外壳：圆角卡片 + 明暗主题自适应样式。"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
  <title>{escape(title)}</title>
  <style>
    .card  {{ fill: #ffffff; stroke: #d0d7de; }}
    .title {{ fill: #1f2328; font: 600 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .label {{ fill: #656d76; font: 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .value {{ fill: #1f2328; font: 600 17px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .name  {{ fill: #1f2328; font: 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .pct   {{ fill: #656d76; font: 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .track {{ fill: #eaeef2; }}
    .q0 {{ fill: {LIGHT_Q[0]}; }} .q1 {{ fill: {LIGHT_Q[1]}; }}
    .q2 {{ fill: {LIGHT_Q[2]}; }} .q3 {{ fill: {LIGHT_Q[3]}; }} .q4 {{ fill: {LIGHT_Q[4]}; }}
    .axis  {{ fill: #656d76; font: 10px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .frame {{ fill: none; stroke: #d0d7de; }}
    @media (prefers-color-scheme: dark) {{
      .card  {{ fill: #0d1117; stroke: #30363d; }}
      .title, .value, .name {{ fill: #e6edf3; }}
      .label, .pct, .axis {{ fill: #8b949e; }}
      .track {{ fill: #21262d; }}
      .q0 {{ fill: {DARK_Q[0]}; }} .q1 {{ fill: {DARK_Q[1]}; }}
      .q2 {{ fill: {DARK_Q[2]}; }} .q3 {{ fill: {DARK_Q[3]}; }} .q4 {{ fill: {DARK_Q[4]}; }}
      .frame {{ stroke: #30363d; }}
    }}
  </style>
  <rect class="card" x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10"/>
"""


# -------------------------------------------------------------------- renderers

def render_stats(items):
    w, pad, cols, rows = 380, 16, 2, 3
    cell_w = (w - 2 * pad) // cols
    cell_h = 54
    h = pad * 2 + cell_h * rows + 6
    out = [svg_open(w, h, f"{USERNAME}'s GitHub stats")]
    for i, (label, value, color) in enumerate(items[:6]):
        col, row = i % cols, i // cols
        x = pad + col * cell_w
        y = pad + row * cell_h
        out.append(f'  <rect x="{x + 2}" y="{y + 14}" width="7" height="7" rx="2" fill="{color}"/>')
        out.append(f'  <text class="label" x="{x + 16}" y="{y + 22}">{escape(label)}</text>')
        out.append(f'  <text class="value" x="{x + 2}" y="{y + 44}">{escape(fmt(value))}</text>')
    out.append("</svg>\n")
    return w, h, "".join(out)


def render_langs(pairs):
    """pairs: [(lang, percent), ...]"""
    w, pad = 380, 16
    row_h = 30
    h = 46 + row_h * len(pairs) + 8
    out = [svg_open(w, h, "Most used languages")]
    out.append(f'  <text class="title" x="{pad}" y="28">Most Used Languages</text>')
    bar_w = w - 2 * pad
    for i, (lang, pct) in enumerate(pairs):
        y = 46 + i * row_h
        color = LANG_COLORS.get(lang, "#8b949e")
        out.append(f'  <text class="name" x="{pad}" y="{y + 11}">{escape(lang)}</text>')
        out.append(f'  <text class="pct" x="{w - pad}" y="{y + 11}" '
                   f'text-anchor="end">{pct:.1f}%</text>')
        out.append(f'  <rect class="track" x="{pad}" y="{y + 17}" width="{bar_w}" height="8" rx="4"/>')
        fill = max(6, int(bar_w * pct / 100))
        out.append(f'  <rect x="{pad}" y="{y + 17}" width="{fill}" height="8" rx="4" fill="{color}"/>')
    out.append("</svg>\n")
    return w, h, "".join(out)


def render_graph(days, total):
    """53 周 x 7 天热力图，仿 GitHub 官方样式。"""
    cell, gap, step = 10, 3, 13
    # top 需要给标题(y=16)和月份标签(y=top-6)各留一行，否则会重叠
    left, top = 32, 42
    weeks = (len(days) + 6) // 7
    w = left + weeks * step + 12
    h = top + 7 * step + 30
    out = [svg_open(w, h, "Contribution graph")]

    out.append(f'  <text class="title" x="12" y="16">{fmt(total)} contributions in the last year</text>')

    # 星期标签（周一 / 周三 / 周五）
    for idx, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(f'  <text class="axis" x="8" y="{top + idx * step + 9}">{name}</text>')

    last_month = None
    for i in range(weeks):
        week = days[i * 7:(i + 1) * 7]
        x = left + i * step
        for j, day in enumerate(week):
            if not day:
                continue
            level = LEVELS.index(day["contributionLevel"]) if day["contributionLevel"] in LEVELS else 0
            out.append(f'  <rect class="q{level}" x="{x}" y="{top + j * step}" '
                       f'width="{cell}" height="{cell}" rx="2"/>')
        # 月份标签：该周首日跨月时打标
        if week and week[0].get("date"):
            month = week[0]["date"][:7]
            if month != last_month:
                last_month = month
                label = datetime.strptime(week[0]["date"], "%Y-%m-%d").strftime("%b")
                out.append(f'  <text class="axis" x="{x}" y="{top - 6}">{label}</text>')

    # 图例：Less [5 色块] More，顺序左→右排布，避免文字压到色块
    ly = top + 7 * step + 14
    leg_x = w - 12 - 135
    out.append(f'  <text class="axis" x="{leg_x}" y="{ly}">Less</text>')
    for k in range(5):
        out.append(f'  <rect class="q{k}" x="{leg_x + 26 + k * 13}" y="{ly - 9}" '
                   f'width="10" height="10" rx="2"/>')
    out.append(f'  <text class="axis" x="{leg_x + 92}" y="{ly}">More</text>')

    # 连续贡献天数
    cur, longest, run = 0, 0, 0
    for day in days:
        if day["contributionCount"] > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    for day in reversed(days):
        if day["contributionCount"] > 0:
            cur += 1
        elif cur:
            break
    out.append(f'  <text class="axis" x="12" y="{ly}">'
               f'Longest streak {longest} d · Current streak {cur} d</text>')

    out.append("</svg>\n")
    return w, h, "".join(out)


# ------------------------------------------------------------------------ main

def write_asset(name, width, height, content):
    os.makedirs(ASSETS_DIR, exist_ok=True)
    path = os.path.join(ASSETS_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  wrote {path} ({width}x{height}, {len(content)} bytes)")


def main():
    print(f"generating stats for {USERNAME}")
    user = safe("user", fetch_user, {}) or {}
    repos = safe("repos", fetch_repos, []) or []
    commits, prs = safe("counts", fetch_counts, (0, 0)) or (0, 0)

    stars = sum(r.get("stargazers_count", 0) for r in repos if not r.get("fork"))
    total, days = safe("calendar", fetch_calendar, (0, [])) or (0, [])

    items = [
        ("Total Commits", commits, "#f78166"),
        ("Total Pull Requests", prs, "#a371f7"),
        ("Contributions (1y)", total, "#3fb950"),
        ("Public Repositories", user.get("public_repos", 0), "#58a6ff"),
        ("Total Stars Earned", stars, "#e3b341"),
        ("Followers", user.get("followers", 0), "#db61a2"),
    ]
    write_asset("github-stats.svg", *render_stats(items))

    tally = safe("languages", lambda: fetch_languages(repos), {}) or {}
    if tally:
        grand = sum(tally.values()) or 1
        top = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)[:LANG_COUNT]
        pairs = [(lang, size * 100.0 / grand) for lang, size in top]
    else:
        pairs = []
    print(f"  languages: {[p[0] for p in pairs]}")
    write_asset("top-langs.svg", *render_langs(pairs))

    if days:
        write_asset("contribution-graph.svg", *render_graph(days, total))
    else:
        print("[warn] no contribution calendar, skip graph")

    print(f"done at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")


if __name__ == "__main__":
    main()
