"""Pre-authored independent acceptance for upstream rich-cli issue #147.

Source: https://github.com/Textualize/rich-cli/issues/147 (observed behavior and
expected behavior only). These are TestFix experiment checks, not upstream tests.
They exercise the public CLI without prescribing how CSV text is represented.
"""
from __future__ import annotations

from html.parser import HTMLParser
from xml.etree import ElementTree

import pytest
from click.testing import CliRunner


CLI_ENV = {"NO_COLOR": "1", "COLUMNS": "160", "TERM": "dumb"}


def invoke_cli(arguments, *, stdin=None):
    from rich_cli.__main__ import main

    return CliRunner().invoke(
        main, ["--width", "160", *arguments], input=stdin,
        env=CLI_ENV, color=False, terminal_width=160,
    )


def assert_success(result):
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("literal", [
    "[red]literal[/red]",
    "[not-a-style]",
    "[/bold]",
    "list[0]",
    "https://example.test/a[0]?q=[red]",
    "中文[绿色]内容",
    "[bold]中文[/bold]",
    ":rocket:",
    "[link=https://example.test]shown[/link]",
    "🚀[red]火箭[/red]",
])
def test_csv_data_preserves_literal_text(tmp_path, literal):
    path = tmp_path / "literal.csv"
    path.write_text(f"name,value\n{literal},2\n", encoding="utf-8")
    result = invoke_cli(["--csv", str(path)])
    assert_success(result)
    assert literal in result.output


@pytest.mark.parametrize("literal", [
    "[bold]name[/bold]", "[/red]", "[unknown-header]",
])
def test_csv_headers_preserve_literal_text(tmp_path, literal):
    path = tmp_path / "headers.csv"
    path.write_text(f"{literal},value\nalpha,12\nbeta,34\n", encoding="utf-8")
    result = invoke_cli(["--csv", str(path)])
    assert_success(result)
    assert literal in result.output


@pytest.mark.parametrize("source", ["file", "stdin"])
def test_same_csv_literal_contract_for_files_and_stdin(tmp_path, source):
    csv = "name,value\n[red]source[/red],2\n"
    if source == "file":
        path = tmp_path / "source.csv"
        path.write_text(csv, encoding="utf-8")
        result = invoke_cli(["--csv", str(path)])
    else:
        result = invoke_cli(["--csv", "-"], stdin=csv)
    assert_success(result)
    assert "[red]source[/red]" in result.output


def test_tsv_preserves_header_and_cell_literals(tmp_path):
    path = tmp_path / "source.tsv"
    path.write_text("[bold]header[/bold]\tvalue\n[red]tab[/red]\t2\n", encoding="utf-8")
    result = invoke_cli([str(path)])
    assert_success(result)
    assert "[bold]header[/bold]" in result.output
    assert "[red]tab[/red]" in result.output


def test_head_preserves_literals_without_disabling_row_selection(tmp_path):
    path = tmp_path / "head.csv"
    path.write_text("name,value\n[red]first[/red],1\n[blue]second[/blue],2\n[bold]third[/bold],3\n", encoding="utf-8")
    result = invoke_cli(["--csv", "--head", "1", str(path)])
    assert_success(result)
    assert "[red]first[/red]" in result.output
    assert "second" not in result.output
    assert "third" not in result.output


def test_tail_preserves_literals_without_disabling_row_selection(tmp_path):
    path = tmp_path / "tail.csv"
    path.write_text("name,value\n[red]first[/red],1\n[blue]second[/blue],2\n[bold]third[/bold],3\n", encoding="utf-8")
    result = invoke_cli(["--csv", "--tail", "1", str(path)])
    assert_success(result)
    assert "[bold]third[/bold]" in result.output
    assert "first" not in result.output
    assert "second" not in result.output


def test_duplicate_header_values_are_not_lost_or_deduplicated(tmp_path):
    path = tmp_path / "duplicate.csv"
    path.write_text("[red]field[/red],[red]field[/red]\n1,2\n3,4\n", encoding="utf-8")
    result = invoke_cli(["--csv", str(path)])
    assert_success(result)
    assert result.output.count("[red]field[/red]") == 2


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.data = []

    def handle_data(self, value):
        self.data.append(value)


def test_html_export_preserves_literals_and_decoded_entities(tmp_path):
    source = tmp_path / "export.csv"
    target = tmp_path / "literal.html"
    literal = "[red]export & <literal>[/red]"
    source.write_text(f"name,value\n{literal},2\n", encoding="utf-8")
    result = invoke_cli(["--csv", "--export-html", str(target), str(source)])
    assert_success(result)
    parser = VisibleHTML()
    parser.feed(target.read_text(encoding="utf-8"))
    assert literal in "".join(parser.data)


def test_svg_export_preserves_literals_and_decoded_entities(tmp_path):
    source = tmp_path / "export.csv"
    target = tmp_path / "literal.svg"
    literal = "[bold]SVG & <中文>[/bold]"
    source.write_text(f"name,value\n{literal},2\n", encoding="utf-8")
    result = invoke_cli(["--csv", "--export-svg", str(target), str(source)])
    assert_success(result)
    root = ElementTree.fromstring(target.read_text(encoding="utf-8"))
    # Rich uses nonbreaking spaces for SVG positioning; compare visible text,
    # after XML entity decoding, without treating that layout choice as data loss.
    visible = "".join(root.itertext()).replace("\u00a0", " ")
    assert literal in visible
