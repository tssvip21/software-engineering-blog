from pathlib import Path
from html import escape
from markdown_it import MarkdownIt

root = Path(__file__).resolve().parent
markdown = MarkdownIt("commonmark", {"html": False}).enable("table")
posts = [
    ("02-data-design", "2026. 11. 09. · 데이터 설계 02", "테이블과 관계를 ERD로 정리하고, 구현하며 고친 부분과 남은 문제를 기록했습니다."),
    ("01-topic-proposal", "2026. 11. 02. · 기획 01", "어떤 불편에서 시작했는지, 누가 사용하는지, 이번 학기에 어디까지 구현할지 정리했습니다."),
]

def page(title, body, prefix=""):
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="반려동물 돌봄 관리 서비스의 기획과 개발 과정">
<title>{escape(title)} | 소프트웨어공학 개발 블로그</title>
<link rel="stylesheet" href="{prefix}style.css">
</head>
<body>
<header><a href="{prefix}index.html">소프트웨어공학 개발 블로그</a><span>반려동물 돌봄 관리 서비스</span></header>
<main>{body}</main>
<footer>2026 · 소프트웨어공학 학기 프로젝트</footer>
</body>
</html>
"""

entries = []
for slug, date, summary in posts:
    post = root / "posts" / (slug + ".md")
    text = post.read_text(encoding="utf-8")
    title = text.splitlines()[0].removeprefix("# ")
    body = markdown.render(text).replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")
    post.with_suffix(".html").write_text(page(title, "<article>" + body + "</article>", "../"), encoding="utf-8")
    entries.append(f'<section class="post-list"><p class="date">{escape(date)}</p><h2><a href="posts/{slug}.html">{escape(title)}</a></h2><p>{escape(summary)}</p></section>')
home = '<p class="eyebrow">프로젝트 기록</p><h1>반려동물 돌봄 관리 서비스</h1><p>주제 선정부터 데이터 설계, 구현과 배포까지 개발 과정을 기록합니다.</p>' + "".join(entries)
(root / "index.html").write_text(page("글 목록", home), encoding="utf-8")
