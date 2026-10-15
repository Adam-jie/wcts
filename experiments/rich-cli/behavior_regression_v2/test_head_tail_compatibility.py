"""Preserve observed upstream CLI behavior for simultaneous head/tail flags.

The pinned CSV command accepts --head 1 --tail 1 and gives head precedence.
Zero is not accepted by either public Click option (IntRange(min=1)); those
invocations must retain usage exit code 2 rather than acquire new semantics.
These nine checks supplement, and do not replace, the original frozen suite.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner


CSV = "name,value\nfirstrow,1\nsecondrow,2\nthirdrow,3\n"
CLI_ENV = {"NO_COLOR": "1", "COLUMNS": "160", "TERM": "dumb"}


def invoke_cli(arguments, *, stdin=None):
    from rich_cli.__main__ import main

    return CliRunner().invoke(
        main, ["--width", "160", *arguments], input=stdin,
        env=CLI_ENV, color=False, terminal_width=160,
    )


@pytest.mark.parametrize("mode", ["explicit-csv-stdin", "auto-csv-file", "auto-tsv-file"])
@pytest.mark.parametrize("head,tail", [(1, 1), (0, 1), (1, 0)],
                         ids=["head1-tail1", "head0-tail1", "head1-tail0"])
def test_simultaneous_head_tail_preserves_public_cli_contract(tmp_path, mode, head, tail):
    if mode == "explicit-csv-stdin":
        prefix, resource, stdin = ["--csv"], "-", CSV
    else:
        suffix = ".csv" if mode == "auto-csv-file" else ".tsv"
        path = tmp_path / ("selection" + suffix)
        path.write_text(CSV if suffix == ".csv" else CSV.replace(",", "\t"),
                        encoding="utf-8")
        prefix, resource, stdin = [], str(path), None

    result = invoke_cli([*prefix, "--head", str(head), "--tail", str(tail), resource],
                        stdin=stdin)
    if head == 0 or tail == 0:
        assert result.exit_code == 2, result.output
        assert "Invalid value" in result.output
        assert ("--head" if head == 0 else "--tail") in result.output
        return

    assert result.exit_code == 0, result.output
    head_only = invoke_cli([*prefix, "--head", "1", resource], stdin=stdin)
    assert head_only.exit_code == 0, head_only.output
    assert result.output == head_only.output
    assert "firstrow" in result.output
    assert "secondrow" not in result.output
    assert "thirdrow" not in result.output
