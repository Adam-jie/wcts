"""TestFix-authored frozen pre-change behavior, separate from upstream's one test.

Behavior sources are the fixed upstream README and public CLI options. These
checks intentionally exclude the CSV markup bug from their baseline contract.
"""
from __future__ import annotations

import re

from click.testing import CliRunner


CLI_ENV = {"NO_COLOR": "1", "COLUMNS": "160", "TERM": "dumb"}


def invoke_cli(arguments, *, stdin=None):
    from rich_cli.__main__ import main

    return CliRunner().invoke(
        main, ["--width", "160", *arguments], input=stdin,
        env=CLI_ENV, color=False, terminal_width=160,
    )


def success(result, *contains):
    assert result.exit_code == 0, result.output
    for value in contains:
        assert value in result.output


def test_ordinary_csv_file_remains_a_readable_table(tmp_path):
    path = tmp_path / "ordinary.csv"
    path.write_text("name,value\nalpha,12\nbeta,34\n", encoding="utf-8")
    success(invoke_cli([str(path)]), "name", "value", "alpha", "beta", "12", "34")


def test_quoted_csv_cells_preserve_embedded_commas(tmp_path):
    path = tmp_path / "quoted.csv"
    path.write_text('name,value\n"Smith, Jane",2\n"Jones, Sam",3\n', encoding="utf-8")
    success(invoke_cli(["--csv", str(path)]), "Smith, Jane", "Jones, Sam")


def test_ordinary_tsv_is_autodetected(tmp_path):
    path = tmp_path / "ordinary.tsv"
    path.write_text("name\tvalue\nalpha\t12\nbeta\t34\n", encoding="utf-8")
    success(invoke_cli([str(path)]), "alpha", "beta", "12", "34")


def test_numeric_csv_cells_preserve_sign_and_decimal_spelling(tmp_path):
    path = tmp_path / "numbers.csv"
    path.write_text("name,value\nalpha,-12.50\nbeta,0.125\n", encoding="utf-8")
    success(invoke_cli([str(path)]), "-12.50", "0.125")


def test_head_still_selects_first_data_row(tmp_path):
    path = tmp_path / "head.csv"
    path.write_text("name,value\nfirstrow,1\nsecondrow,2\nthirdrow,3\n", encoding="utf-8")
    result = invoke_cli(["--csv", "--head", "1", str(path)])
    success(result, "firstrow")
    assert "secondrow" not in result.output
    assert "thirdrow" not in result.output


def test_tail_still_selects_last_data_row(tmp_path):
    path = tmp_path / "tail.csv"
    path.write_text("name,value\nfirstrow,1\nsecondrow,2\nthirdrow,3\n", encoding="utf-8")
    result = invoke_cli(["--csv", "--tail", "1", str(path)])
    success(result, "thirdrow")
    assert "firstrow" not in result.output
    assert "secondrow" not in result.output


def test_json_stdin_formatting_is_preserved():
    success(invoke_cli(["--json", "-"], stdin='{"ready": true, "count": 2}'), '"ready"', "true", '"count"')


def test_markdown_rendering_is_preserved(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# Report\n\n**Key result**\n", encoding="utf-8")
    result = invoke_cli([str(path)])
    success(result, "Report", "Key result")
    assert "**Key result**" not in result.output


def test_explicit_print_mode_still_interprets_intentional_markup():
    result = invoke_cli(["--print", "[bold]intentional[/bold]"])
    success(result, "intentional")
    assert "[bold]" not in result.output
    assert "[/bold]" not in result.output


def test_help_still_describes_the_public_options():
    success(invoke_cli(["--help"]), "--csv", "--print", "--export-svg", "--width")


def test_version_still_exits_successfully_with_a_version():
    result = invoke_cli(["--version"])
    success(result)
    assert re.search(r"\b\d+\.\d+\.\d+\b", result.output)


def test_unknown_option_remains_a_usage_error():
    result = invoke_cli(["--testfix-invalid-option"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_unicode_csv_is_preserved(tmp_path):
    path = tmp_path / "unicode.csv"
    path.write_text("name,value\n中文名称,2\n数字,3\n", encoding="utf-8")
    success(invoke_cli([str(path)]), "中文名称", "数字")
