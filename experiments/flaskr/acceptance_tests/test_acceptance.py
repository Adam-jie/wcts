"""HTTP/HTML/database acceptance, independent from model-generated tests."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from flaskr.db import get_db


class Page(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.article_count = 0
        self.links = []
        self.inputs = []
        self.forms = []
        self.tags = []
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if tag == "article": self.article_count += 1
        if tag == "a": self.links.append(attrs.get("href", ""))
        if tag == "input": self.inputs.append(attrs)
        if tag == "form": self.forms.append(attrs)


def pagination(page):
    return [(link, parse_qs(urlsplit(link).query)) for link in page.links if "page" in parse_qs(urlsplit(link).query)]


def test_public_detail_and_title_navigation(client, seed):
    identifier = seed(["A complete article"], ["Full article body"])[0]
    detail = client.get(f"/{identifier}")
    assert detail.status_code == 200
    assert b"A complete article" in detail.data and b"Full article body" in detail.data
    assert b"author" in detail.data and b"2026-10-14" in detail.data
    assert f"/{identifier}" in Page(client.get("/")).links
    assert client.get("/99999").status_code == 404


def test_reader_cannot_modify_author_post(client, seed):
    identifier = seed(["Protected article"])[0]
    assert client.post("/auth/login", data={"username": "reader", "password": "password"}).status_code == 302
    assert client.get(f"/{identifier}").status_code == 200
    assert client.get(f"/{identifier}/update").status_code == 403
    assert client.post(f"/{identifier}/delete").status_code == 403
    client.get("/auth/logout")
    assert client.post(f"/{identifier}/delete").status_code == 302


def test_search_is_title_only_case_insensitive_and_trimmed(client, seed):
    seed(["Agent Design", "Other topic", "AGENT Testing"], ["first body", "agent only in body", "third body"])
    response = client.get("/", query_string={"q": "  aGeNt  "})
    assert response.status_code == 200
    assert Page(response).article_count == 2
    assert b"Agent Design" in response.data and b"AGENT Testing" in response.data
    assert b"Other topic" not in response.data
    assert Page(client.get("/", query_string={"q": "   "})).article_count == 3


@pytest.mark.parametrize("literal", ["%", "_", "\\"])
def test_search_treats_like_metacharacters_as_literal(client, seed, literal):
    seed(["prefix" + literal + "suffix", "ordinary title"])
    response = client.get("/", query_string={"q": literal})
    assert response.status_code == 200 and Page(response).article_count == 1
    assert b"ordinary title" not in response.data


def test_sql_looking_query_does_not_match_everything_or_modify_database(client, app, seed):
    seed(["Keep one", "Keep two"])
    for query in ["' OR 1=1 --", "'; DROP TABLE post; --"]:
        response = client.get("/", query_string={"q": query})
        assert response.status_code == 200 and Page(response).article_count == 0
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM post").fetchone()[0] == 2


def test_pagination_is_five_posts_and_ties_are_deterministic(client, seed):
    ids = seed([f"Item {i}" for i in range(13)])
    pages = [client.get("/", query_string={"page": i}) for i in (1, 2, 3)]
    assert all(page.status_code == 200 for page in pages)
    assert [Page(page).article_count for page in pages] == [5, 5, 3]
    for page_number, response in enumerate(pages):
        actual = [int(urlsplit(link).path[1:]) for link in Page(response).links if urlsplit(link).path[1:].isdigit()]
        assert actual == list(reversed(ids))[page_number * 5 : (page_number + 1) * 5]
    assert {int(query["page"][0]) for _, query in pagination(Page(pages[0]))} == {2}
    assert {int(query["page"][0]) for _, query in pagination(Page(pages[1]))} == {1, 3}
    assert {int(query["page"][0]) for _, query in pagination(Page(pages[2]))} == {2}
    assert client.get("/?page=4").status_code == 404


def test_created_time_is_primary_sort_key(client, seed):
    earlier = seed(["Earlier but larger id"], created="2025-01-01 00:00:00")[0]
    newest = seed(["Latest"], created="2026-01-01 00:00:00")[0]
    seed(["Earlier yet"], created="2024-01-01 00:00:00")
    page = Page(client.get("/"))
    links = [link for link in page.links if urlsplit(link).path[1:].isdigit()]
    assert links[:2] == [f"/{newest}", f"/{earlier}"]


def test_search_and_pagination_compose_and_preserve_query(client, seed):
    seed([f"Needle article {i}" for i in range(11)] + [f"unrelated {i}" for i in range(8)])
    response = client.get("/", query_string={"q": "Needle", "page": 2})
    assert response.status_code == 200 and Page(response).article_count == 5
    assert b"unrelated" not in response.data
    links = pagination(Page(response))
    assert {query["page"][0] for _, query in links} == {"1", "3"}
    assert all(query.get("q") == ["Needle"] for _, query in links)
    assert client.get("/", query_string={"q": "Needle", "page": 4}).status_code == 404


@pytest.mark.parametrize("page", ["0", "-1", "abc", "1.0", "", " 1", "+1", "١"])
def test_malformed_page_is_rejected(client, seed, page):
    seed(["one"])
    assert client.get("/", query_string={"page": page}).status_code == 400


def test_empty_filtered_and_empty_database_first_page(client, seed):
    response = client.get("/")
    assert response.status_code == 200 and Page(response).article_count == 0
    assert not pagination(Page(response))
    assert b"No " in response.data or b"empty" in response.data.lower()
    seed(["Existing"])
    response = client.get("/", query_string={"q": "nonexistent"})
    assert response.status_code == 200 and Page(response).article_count == 0
    assert not pagination(Page(response))
    assert client.get("/", query_string={"q": "nonexistent", "page": 2}).status_code == 404


def test_html_escaping_and_real_get_search_form(client, seed):
    payload = '<script>alert("test")</script>'
    identifier = seed([payload], [payload])[0]
    for response in [client.get("/", query_string={"q": payload}), client.get(f"/{identifier}")]:
        assert response.status_code == 200 and "script" not in Page(response).tags
        assert b"&lt;script&gt;" in response.data
    page = Page(client.get("/", query_string={"q": payload}))
    assert any(item.get("name") == "q" and item.get("value") == payload for item in page.inputs)
    assert any(form.get("method", "get").lower() == "get" for form in page.forms)
