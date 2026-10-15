"""Black-box HTTP tests for requirements R1-R3.

The helpers parse rendered HTML rather than depending on view function names,
template filenames, CSS classes, or implementation-only Python helpers.
"""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self.forms = []
        self.articles = []
        self._article = None
        self._anchor = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a":
            item = {"href": values.get("href", ""), "text": ""}
            self.anchors.append(item)
            self._anchor = item
        if tag == "form":
            self.forms.append(
                {
                    "action": values.get("action", ""),
                    "method": values.get("method", "get").lower(),
                    "controls": [],
                }
            )
        elif tag in {"input", "textarea", "select"} and self.forms:
            if values.get("name"):
                self.forms[-1]["controls"].append(values.get("name"))
        if tag == "article":
            self._article = []

    def handle_endtag(self, tag):
        if tag == "a":
            self._anchor = None
        if tag == "article" and self._article is not None:
            self.articles.append(" ".join(self._article).strip())
            self._article = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        self._text.append(text)
        if self._anchor is not None:
            self._anchor["text"] += (" " if self._anchor["text"] else "") + text
        if self._article is not None:
            self._article.append(text)

    @property
    def text(self):
        return " ".join(self._text)


def parse(response):
    parser = PageParser()
    parser.feed(response.get_data(as_text=True))
    return parser


def links_to(page, path):
    return [a for a in page.anchors if urlsplit(a["href"]).path == path]


def pagination_links(page):
    result = []
    for anchor in page.anchors:
        query = parse_qs(urlsplit(anchor["href"]).query, keep_blank_values=True)
        if "page" in query:
            result.append((anchor, query))
    return result


def test_index_titles_link_to_public_detail_page(client, add_posts):
    post_id = add_posts(
        [
            {
                "title": "A public title",
                "body": "Public body text",
                "created": "2026-03-04 05:06:07",
            }
        ]
    )[0]

    index = client.get("/")
    assert index.status_code == 200
    page = parse(index)
    matching = links_to(page, f"/{post_id}")
    assert any("A public title" in link["text"] for link in matching)

    detail = client.get(f"/{post_id}")
    assert detail.status_code == 200
    detail_page = parse(detail)
    for expected in ("A public title", "Public body text", "alice", "2026-03-04"):
        assert expected in detail_page.text


def test_missing_detail_returns_404(client):
    assert client.get("/987654").status_code == 404


def test_detail_escapes_post_and_author_content(client, app, add_posts):
    with app.app_context():
        from flaskr.db import get_db

        db = get_db()
        db.execute("UPDATE user SET username = ? WHERE id = 1", ("<b>alice</b>",))
        db.commit()
    post_id = add_posts(
        [{"title": "<script>title()</script>", "body": "<img src=x onerror=boom>"}]
    )[0]

    html = client.get(f"/{post_id}").get_data(as_text=True)
    assert "<script>title()</script>" not in html
    assert "<img src=x onerror=boom>" not in html
    assert "<b>alice</b>" not in html
    assert "&lt;script&gt;title()&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=boom&gt;" in html
    assert "&lt;b&gt;alice&lt;/b&gt;" in html


def test_only_author_gets_edit_and_delete_controls(client, add_posts, login):
    post_id = add_posts([{"title": "Owned", "body": "private controls"}])[0]

    anonymous = parse(client.get(f"/{post_id}"))
    assert not links_to(anonymous, f"/{post_id}/update")
    assert all(form["action"] != f"/{post_id}/delete" for form in anonymous.forms)

    login("bob", "bob-pass")
    other_user = parse(client.get(f"/{post_id}"))
    assert not links_to(other_user, f"/{post_id}/update")
    assert all(form["action"] != f"/{post_id}/delete" for form in other_user.forms)
    assert client.post(f"/{post_id}/update", data={"title": "stolen", "body": "x"}).status_code == 403
    assert client.post(f"/{post_id}/delete").status_code == 403

    client.get("/auth/logout")
    login()
    owner = parse(client.get(f"/{post_id}"))
    assert links_to(owner, f"/{post_id}/update")
    assert any(form["action"] == f"/{post_id}/delete" for form in owner.forms)


def test_index_has_get_search_form_and_empty_query_shows_all(client, add_posts):
    add_posts([{"title": "First"}, {"title": "Second"}])
    page = parse(client.get("/?q=   "))
    assert any(form["method"] == "get" and "q" in form["controls"] for form in page.forms)
    assert "First" in page.text
    assert "Second" in page.text


def test_search_trims_and_ascii_case_folds_titles_only(client, add_posts):
    add_posts(
        [
            {"title": "Alpha NEEDLE Omega", "body": "ordinary"},
            {"title": "unrelated", "body": "needle appears only in body"},
            {"title": "need", "body": "prefix is not a full match"},
        ]
    )
    page = parse(client.get("/", query_string={"q": "  needle  "}))
    assert "Alpha NEEDLE Omega" in page.text
    assert "unrelated" not in page.text
    assert "need prefix is not a full match" not in page.text


@pytest.mark.parametrize(
    ("query", "matching", "nonmatching"),
    [
        ("%", "100% literal", "ordinary percent-free"),
        ("_", "under_score", "underscore free"),
        ("\\", r"back\slash", "slash free"),
        ("' OR 1=1 --", "SQL ' OR 1=1 -- text", "safe ordinary"),
    ],
)
def test_search_treats_like_metacharacters_and_sql_as_literal(
    client, add_posts, query, matching, nonmatching
):
    add_posts([{"title": matching}, {"title": nonmatching}])
    response = client.get("/", query_string={"q": query})
    assert response.status_code == 200
    page = parse(response)
    assert matching in page.text
    assert nonmatching not in page.text


def test_search_query_is_html_escaped(client):
    query = '<script>alert("query")</script>'
    html = client.get("/", query_string={"q": query}).get_data(as_text=True)
    assert query not in html
    assert "&lt;script&gt;alert(" in html


def test_search_matches_identical_non_ascii_literals(client, add_posts):
    title = "Écho Σ"
    add_posts([{"title": title}, {"title": "plain ASCII"}])

    for query in ("Écho", "Σ"):
        response = client.get("/", query_string={"q": query})
        assert response.status_code == 200
        page = parse(response)
        assert len(page.articles) == 1
        assert title in page.articles[0]
        assert "plain ASCII" not in page.text


def test_pagination_limits_to_five_and_uses_deterministic_newest_order(
    client, add_posts
):
    add_posts(
        [
            {"title": "old", "created": "2026-01-01 00:00:00"},
            {"title": "tie-low-id", "created": "2026-02-01 00:00:00"},
            {"title": "tie-high-id", "created": "2026-02-01 00:00:00"},
            {"title": "new-1", "created": "2026-03-01 00:00:00"},
            {"title": "new-2", "created": "2026-04-01 00:00:00"},
            {"title": "new-3", "created": "2026-05-01 00:00:00"},
        ]
    )

    first = parse(client.get("/"))
    assert len(first.articles) == 5
    displayed = " | ".join(first.articles)
    expected = ["new-3", "new-2", "new-1", "tie-high-id", "tie-low-id"]
    assert all(title in displayed for title in expected)
    assert [displayed.index(title) for title in expected] == sorted(
        displayed.index(title) for title in expected
    )
    assert "old" not in displayed

    second = parse(client.get("/?page=2"))
    assert len(second.articles) == 1
    assert "old" in second.articles[0]
    assert pagination_links(first)
    assert pagination_links(second)


def test_pagination_preserves_search_query_in_navigation(client, add_posts):
    add_posts([{"title": f"match {number}"} for number in range(6)])
    add_posts([{"title": "excluded"}])
    first = parse(client.get("/", query_string={"q": " match "}))
    links = pagination_links(first)
    assert any(query.get("page") == ["2"] and query.get("q") == ["match"] for _, query in links)

    second = parse(client.get("/", query_string={"q": "match", "page": "2"}))
    assert len(second.articles) == 1
    assert "excluded" not in second.text
    assert any(query.get("page") == ["1"] and query.get("q") == ["match"] for _, query in pagination_links(second))


@pytest.mark.parametrize("page", ["0", "-1", "1.0", "+1", " 1", "1 ", "abc", "１"])
def test_page_must_be_ascii_positive_decimal(client, page):
    assert client.get("/", query_string={"page": page}).status_code == 400


def test_page_beyond_filtered_last_page_returns_404(client, add_posts):
    add_posts([{"title": "match"}, {"title": "other"}])
    assert client.get("/", query_string={"q": "match", "page": "2"}).status_code == 404


def test_very_large_ascii_decimal_page_returns_404_instead_of_500(client):
    response = client.get("/", query_string={"page": "9" * 5000})
    assert response.status_code == 404


def test_empty_filtered_page_one_has_message_and_no_articles_or_pagination(
    client, add_posts
):
    add_posts([{"title": "something present"}])
    response = client.get("/", query_string={"q": "no-such-title"})
    assert response.status_code == 200
    page = parse(response)
    assert page.articles == []
    assert pagination_links(page) == []
    lowered = page.text.lower()
    assert any(phrase in lowered for phrase in ("no posts", "no articles", "no results", "nothing found"))
