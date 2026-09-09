#!/usr/bin/env python3
"""Render GitHub statistics as SVG cards, committed into this repo.

Talks to the GraphQL API as the account owner, so private repositories and
private contributions are counted - which no public stats service can do.
Writes four files: a stats card and a language card, each in a dark and a
light variant, paired with <picture> in the README.

Environment:
    STATS_TOKEN  personal access token, read-only, with private repo access
    STATS_LOGIN  the account to report on
"""

import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

API = "https://api.github.com/graphql"
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

THEMES = {
    "dark": {
        "bg": "#0d1117", "border": "#30363d", "title": "#58a6ff",
        "value": "#e6edf3", "label": "#8b949e", "rule": "#21262d", "track": "#21262d",
    },
    "light": {
        "bg": "#ffffff", "border": "#d1d9e0", "title": "#0969da",
        "value": "#1f2328", "label": "#59636e", "rule": "#e4e8ec", "track": "#eef1f4",
    },
}

FONT = "DejaVu Sans, Verdana, Geneva, sans-serif"


# --------------------------------------------------------------------------- api


def gql(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "azzindani-profile-stats",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
            if "errors" in payload:
                raise RuntimeError(f"GraphQL errors: {payload['errors']}")
            return payload["data"]
        except urllib.error.HTTPError as exc:
            if exc.code in (502, 503, 504) and attempt < 3:
                continue
            raise
    raise RuntimeError("unreachable")


USER_QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
  }
}
"""

REPOS_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        isPrivate
        stargazerCount
        primaryLanguage { name color }
      }
    }
  }
}
"""

CONTRIB_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalPullRequestReviewContributions
      totalIssueContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_repos(login, token):
    repos, cursor = [], None
    while True:
        data = gql(REPOS_QUERY, {"login": login, "cursor": cursor}, token)
        block = data["user"]["repositories"]
        repos.extend(block["nodes"])
        if not block["pageInfo"]["hasNextPage"]:
            return repos, block["totalCount"]
        cursor = block["pageInfo"]["endCursor"]


def fetch_contributions(login, token, created_at):
    """contributionsCollection spans at most one year, so walk year by year."""
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    totals = Counter()
    days = {}

    cursor = start
    while cursor < now:
        window_end = min(cursor.replace(year=cursor.year + 1), now)
        data = gql(
            CONTRIB_QUERY,
            {
                "login": login,
                "from": cursor.isoformat().replace("+00:00", "Z"),
                "to": window_end.isoformat().replace("+00:00", "Z"),
            },
            token,
        )
        c = data["user"]["contributionsCollection"]
        totals["commits"] += c["totalCommitContributions"]
        totals["restricted"] += c["restrictedContributionsCount"]
        totals["prs"] += c["totalPullRequestContributions"]
        totals["reviews"] += c["totalPullRequestReviewContributions"]
        totals["issues"] += c["totalIssueContributions"]
        totals["contributions"] += c["contributionCalendar"]["totalContributions"]
        for week in c["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = max(days.get(day["date"], 0), day["contributionCount"])
        cursor = window_end

    return totals, days


def streaks(days):
    """Current and longest run of consecutive days with at least one contribution."""
    if not days:
        return 0, 0
    active = {datetime.strptime(d, "%Y-%m-%d").date() for d, n in days.items() if n > 0}
    if not active:
        return 0, 0

    longest = run = 0
    for day in sorted(active):
        run = run + 1 if (day - timedelta(days=1)) in active else 1
        longest = max(longest, run)

    today = date.today()
    anchor = today if today in active else today - timedelta(days=1)
    current = 0
    while anchor in active:
        current += 1
        anchor -= timedelta(days=1)

    return current, longest


# ----------------------------------------------------------------------- render


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def human(n):
    if n >= 10000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n:,}"


def card_open(width, height, t):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="8" '
        f'fill="{t["bg"]}" stroke="{t["border"]}"/>'
        f'<g font-family="{FONT}">'
    )


def render_stats(metrics, theme_name, generated):
    t = THEMES[theme_name]
    w, h = 760, 232
    tiles = [
        ("Contributions", human(metrics["contributions"])),
        ("Commits", human(metrics["commits"])),
        ("Pull requests", human(metrics["prs"])),
        ("Code reviews", human(metrics["reviews"])),
        ("Repositories", human(metrics["repos"])),
        ("Private repos", human(metrics["private"])),
        ("Stars earned", human(metrics["stars"])),
        ("Longest streak", f'{metrics["longest_streak"]} d'),
    ]

    parts = [card_open(w, h, t)]
    parts.append(
        f'<text x="28" y="44" fill="{t["title"]}" font-size="19" font-weight="700">Statistics</text>'
    )
    parts.append(
        f'<text x="{w - 28}" y="44" fill="{t["label"]}" font-size="12" text-anchor="end">'
        f"including private repositories · {esc(generated)}</text>"
    )
    parts.append(f'<line x1="28" y1="62" x2="{w - 28}" y2="62" stroke="{t["rule"]}"/>')

    col_w = (w - 56) / 4
    for i, (label, value) in enumerate(tiles):
        x = 28 + col_w * (i % 4)
        y = 122 if i < 4 else 200
        parts.append(
            f'<text x="{x:.0f}" y="{y}" fill="{t["value"]}" font-size="30" font-weight="700">{esc(value)}</text>'
        )
        parts.append(
            f'<text x="{x:.0f}" y="{y + 22}" fill="{t["label"]}" font-size="12.5">{esc(label)}</text>'
        )

    parts.append("</g></svg>")
    return "".join(parts)


def render_languages(langs, total_repos, theme_name):
    t = THEMES[theme_name]
    w = 760
    rows = (len(langs) + 1) // 2
    h = 108 + rows * 26

    parts = [card_open(w, h, t)]
    parts.append(
        f'<text x="28" y="44" fill="{t["title"]}" font-size="19" font-weight="700">Languages</text>'
    )
    parts.append(
        f'<text x="{w - 28}" y="44" fill="{t["label"]}" font-size="12" text-anchor="end">'
        f"by repository, not by file size</text>"
    )

    bar_w = w - 56
    counted = sum(n for _, _, n in langs) or 1
    x = 28.0
    parts.append(f'<rect x="28" y="62" width="{bar_w}" height="10" rx="5" fill="{t["track"]}"/>')
    for _, color, count in langs:
        seg = bar_w * count / counted
        if seg < 1:
            continue
        parts.append(
            f'<rect x="{x:.1f}" y="62" width="{seg:.1f}" height="10" fill="{color}"/>'
        )
        x += seg

    for i, (name, color, count) in enumerate(langs):
        col = i % 2
        row = i // 2
        lx = 28 + col * (bar_w / 2)
        ly = 108 + row * 26
        pct = 100.0 * count / counted
        parts.append(f'<circle cx="{lx + 5:.0f}" cy="{ly - 4}" r="5" fill="{color}"/>')
        parts.append(
            f'<text x="{lx + 18:.0f}" y="{ly}" fill="{t["value"]}" font-size="13.5">{esc(name)}</text>'
        )
        parts.append(
            f'<text x="{lx + bar_w / 2 - 16:.0f}" y="{ly}" fill="{t["label"]}" font-size="13" '
            f'text-anchor="end">{pct:.1f}%</text>'
        )

    parts.append("</g></svg>")
    return "".join(parts)


# ------------------------------------------------------------------------- main


def main():
    token = os.environ.get("STATS_TOKEN")
    login = os.environ.get("STATS_LOGIN")
    if not token or not login:
        sys.exit("STATS_TOKEN and STATS_LOGIN must both be set")

    user = gql(USER_QUERY, {"login": login}, token)["user"]
    repos, total_repos = fetch_repos(login, token)
    totals, days = fetch_contributions(login, token, user["createdAt"])
    current_streak, longest_streak = streaks(days)

    by_language = Counter()
    colors = {}
    for repo in repos:
        lang = repo.get("primaryLanguage")
        if not lang:
            continue
        by_language[lang["name"]] += 1
        colors[lang["name"]] = lang["color"] or "#8b949e"

    top = by_language.most_common(8)
    langs = [(name, colors[name], count) for name, count in top]

    metrics = {
        "contributions": totals["contributions"],
        "commits": totals["commits"],
        "prs": totals["prs"],
        "reviews": totals["reviews"],
        "issues": totals["issues"],
        "repos": total_repos,
        "private": sum(1 for r in repos if r["isPrivate"]),
        "stars": sum(r["stargazerCount"] for r in repos),
        "followers": user["followers"]["totalCount"],
        "current_streak": current_streak,
        "longest_streak": longest_streak,
    }

    generated = date.today().isoformat()
    os.makedirs(OUT_DIR, exist_ok=True)
    for theme in THEMES:
        with open(os.path.join(OUT_DIR, f"stats-{theme}.svg"), "w", encoding="utf-8") as fh:
            fh.write(render_stats(metrics, theme, generated))
        with open(os.path.join(OUT_DIR, f"langs-{theme}.svg"), "w", encoding="utf-8") as fh:
            fh.write(render_languages(langs, total_repos, theme))

    print(json.dumps(metrics, indent=2))
    print("languages:", ", ".join(f"{n} {c}" for n, _, c in langs))


if __name__ == "__main__":
    main()
