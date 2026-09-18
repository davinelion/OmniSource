"""Workflow YAML integrity — the failure mode actionlint caught in CI.

CI's *Lint (ruff + actionlint)* job failed on a change whose `run: |` block
contained a shell heredoc written at column 0: the heredoc body terminated the
YAML block scalar, so the rest of the shell script became *invalid YAML* and the
workflow could not run at all. The suite here is stdlib-only (CI installs no
PyYAML), so this module pins the two mechanical invariants that failure broke,
directly on the text:

* a `run:` block scalar is indented under its key, and the first non-blank line
  after it is YAML structure again (a mapping key or a sequence item) - never a
  leftover line of shell script;
* a shell heredoc opened inside a `run:` block is terminated *inside that block*,
  at the block's own indentation (so YAML stripping puts the terminator at
  column 0 where bash expects it).

Both checks are structural, not a YAML parse: they cannot prove a workflow is
valid, but they fail loudly on the exact shape that shipped broken and on
anything resembling it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# `run: |`, `run: >`, `run: |-`, `run: >-` (the scalar header, not an expression).
_RUN_SCALAR_RE = re.compile(r"^(?P<indent>[ \t]*)run:[ \t]*[|>][-+]?[ \t]*$")
# A shell heredoc opener: `<<'EOF'`, `<<"EOF"`, `<<EOF`, `<<-EOF` - never `<<<`.
_HEREDOC_RE = re.compile(r"<<(?!=)(?P<dash>-?)(?P<quote>['\"]?)(?P<token>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)")
# What may follow a block scalar: a sequence item or a mapping key.
_STRUCTURE_RE = re.compile(r"^[ \t]*(?:-[ \t]+\S|\S[^:#]*:)")


def _inside_quotes(line: str, index: int) -> bool:
    """True when ``line[index]`` sits inside a quoted string (crude but effective).

    ``echo "URL_YT<<EOF"`` is GitHub's multiline ``$GITHUB_ENV`` syntax, not a
    shell heredoc; counting unescaped quotes before the match skips it.
    """
    double = 0
    single = 0
    escaped = False
    for char in line[:index]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"' and not single % 2:
            double += 1
        elif char == "'" and not double % 2:
            single += 1
    return bool(double % 2 or single % 2)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _run_blocks(text: str) -> list[tuple[int, int, int]]:
    """Every `run:` block scalar as ``(start, key_indent, end)`` (end exclusive)."""
    lines = text.splitlines()
    blocks: list[tuple[int, int, int]] = []
    index = 0
    while index < len(lines):
        match = _RUN_SCALAR_RE.match(lines[index])
        if not match:
            index += 1
            continue
        key_indent = len(match.group("indent"))
        start = index + 1
        cursor = start
        while cursor < len(lines):
            line = lines[cursor]
            if not line.strip():
                cursor += 1
                continue
            if _indent_of(line) <= key_indent:
                break
            cursor += 1
        blocks.append((start, key_indent, cursor))
        index = cursor
    return blocks


class RunBlockIndentationTests(unittest.TestCase):
    def test_every_run_block_is_indented_under_its_key(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            for start, key_indent, end in _run_blocks(text):
                with self.subTest(workflow=path.name, line=start + 1):
                    body = [line for line in text.splitlines()[start:end] if line.strip()]
                    self.assertTrue(body, f"{path.name}:{start + 1} has an empty run block")
                    for line in body:
                        self.assertGreater(
                            _indent_of(line),
                            key_indent,
                            f"{path.name}:{start + 1}: run block line escapes the block scalar: {line[:60]!r}",
                        )

    def test_the_line_after_a_run_block_is_yaml_structure(self) -> None:
        """The bug's signature: leftover shell text where YAML expects structure."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for _start, _key_indent, end in _run_blocks("\n".join(lines)):
                cursor = end
                while cursor < len(lines) and (not lines[cursor].strip() or lines[cursor].lstrip().startswith("#")):
                    cursor += 1
                if cursor >= len(lines):
                    continue  # the block runs to the end of the file
                following = lines[cursor]
                with self.subTest(workflow=path.name, line=cursor + 1):
                    self.assertRegex(
                        following,
                        _STRUCTURE_RE,
                        f"{path.name}:{cursor + 1}: expected YAML structure after a run block, "
                        f"found {following.strip()[:60]!r} - a block scalar was terminated early "
                        "(a heredoc body written at column 0 does exactly this)",
                    )


class HeredocTests(unittest.TestCase):
    def test_heredocs_open_inside_run_blocks_are_terminated_inside_them(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for start, _key_indent, end in _run_blocks(text):
                body = lines[start:end]
                # YAML infers the block's indentation from its first content line;
                # after stripping, that line sits at column 0, so a terminator is
                # only valid at exactly this indentation.
                floor = next((_indent_of(line) for line in body if line.strip()), None)
                for line in body:
                    if line.strip() and floor is not None:
                        self.assertGreaterEqual(
                            _indent_of(line),
                            floor,
                            f"{path.name}:{start + 1}: run block line sits below the block's inferred "
                            f"indentation: {line.strip()[:60]!r}",
                        )
                for offset, line in enumerate(body):
                    for match in _HEREDOC_RE.finditer(line):
                        if _inside_quotes(line, match.start()):
                            continue
                        token = match.group("token")
                        terminator = next(
                            (candidate for candidate in body[offset + 1 :] if candidate.strip() == token),
                            None,
                        )
                        location = f"{path.name}:{start + offset + 1}"
                        with self.subTest(workflow=path.name, line=start + offset + 1, token=token):
                            self.assertIsNotNone(
                                terminator,
                                f"{location}: heredoc {token!r} is never terminated inside its run block - "
                                "the terminator at column 0 would end the YAML block scalar (invalid YAML)",
                            )
                            if terminator is not None and floor is not None:
                                self.assertEqual(
                                    _indent_of(terminator),
                                    floor,
                                    f"{location}: heredoc {token!r} terminator must sit at the run block's "
                                    "indentation so YAML stripping puts it at column 0",
                                )

    def test_the_scan_finds_the_blocks_it_is_supposed_to(self) -> None:
        # A guard on the guard: the workflows have run blocks, so a silent regex
        # drift cannot make every assertion above vacuous.
        total = 0
        for path in sorted(WORKFLOWS.glob("*.yml")):
            total += len(_run_blocks(path.read_text(encoding="utf-8")))
        self.assertGreater(total, 20, f"only {total} run block(s) found across the workflows")


class WorkflowShapeTests(unittest.TestCase):
    def test_every_workflow_declares_a_name_and_jobs(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(workflow=path.name):
                self.assertRegex(text, r"(?m)^name: ")
                self.assertRegex(text, r"(?m)^jobs:")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
