from __future__ import annotations

import unittest

from codex_transcript_viewer.highlight import highlight
from codex_transcript_viewer.markdown import render_markdown, split_memory_citations


class LinkTests(unittest.TestCase):
    def test_web_link_opens_in_new_tab(self) -> None:
        html = render_markdown("See [the docs](https://example.com/a?b=1&c=2).")
        self.assertIn(
            '<a href="https://example.com/a?b=1&amp;c=2" target="_blank" '
            'rel="noopener noreferrer">the docs</a>',
            html,
        )

    def test_file_link_shows_label_with_path_on_hover(self) -> None:
        html = render_markdown("Fixed in [parser.py](/Users/me/repo/parser.py:856).")
        self.assertIn('<span class="md-path" title="/Users/me/repo/parser.py:856">parser.py</span>', html)
        self.assertNotIn("<a ", html)

    def test_link_label_keeps_inline_code(self) -> None:
        html = render_markdown("[`index.py:524`](/r/index.py:524)")
        self.assertIn('title="/r/index.py:524"><code>index.py:524</code></span>', html)

    def test_bare_and_angle_urls_become_links(self) -> None:
        html = render_markdown("Visit https://example.com/x, or <https://example.org>.")
        self.assertIn('<a href="https://example.com/x" target="_blank"', html)
        self.assertIn(">https://example.com/x</a>,", html)
        self.assertIn('<a href="https://example.org" target="_blank"', html)

    def test_script_urls_are_not_links(self) -> None:
        html = render_markdown("[click](javascript:alert(1))")
        self.assertNotIn("<a ", html)
        self.assertNotIn('href="javascript', html)

    def test_quotes_in_targets_stay_escaped(self) -> None:
        html = render_markdown('[x](https://e.com/"onmouseover="alert(1))')
        self.assertNotIn('"onmouseover', html)

    def test_links_inside_code_are_left_alone(self) -> None:
        html = render_markdown("`[a](https://e.com)` and\n```\nhttps://e.com/*x*\n```")
        self.assertNotIn("<a ", html)
        self.assertIn("<code>[a](https://e.com)</code>", html)
        self.assertNotIn("<em>", html)


class TableTests(unittest.TestCase):
    TABLE = (
        "Results:\n"
        "| Name | Count | Note |\n"
        "|:-----|------:|:----:|\n"
        "| `a|b` | **3** | [x](https://e.com) |\n"
        "| c \\| d | 4 |\n"
        "\n"
        "Done."
    )

    def test_pipe_table_renders(self) -> None:
        html = render_markdown(self.TABLE)
        self.assertIn('<table class="md-table"><thead><tr><th>Name</th>'
                      '<th style="text-align:right">Count</th>'
                      '<th style="text-align:center">Note</th></tr></thead>', html)
        self.assertIn("<td><code>a|b</code></td>", html)
        self.assertIn('<td style="text-align:right"><strong>3</strong></td>', html)
        self.assertIn(">x</a></td>", html)
        self.assertIn("<td>c | d</td>", html)
        # Short rows are padded to the header width.
        self.assertIn('<td style="text-align:right">4</td><td style="text-align:center"></td></tr>', html)
        self.assertTrue(html.startswith("Results:\n<table"))
        self.assertIn("</table>\nDone.", html)

    def test_table_without_leading_pipes(self) -> None:
        html = render_markdown("a | b\n--- | ---\n1 | 2")
        self.assertIn("<th>a</th><th>b</th>", html)
        self.assertIn("<td>1</td><td>2</td>", html)

    def test_pipes_without_separator_stay_text(self) -> None:
        text = "a | b\nc | d"
        self.assertEqual(render_markdown(text), "a | b\nc | d")

    def test_mismatched_separator_is_not_a_table(self) -> None:
        self.assertNotIn("<table", render_markdown("| a | b |\n|---|\n| 1 | 2 |"))

    def test_table_in_code_block_stays_code(self) -> None:
        html = render_markdown("```\n| a | b |\n|---|---|\n```")
        self.assertNotIn("<table", html)


class ExistingFormattingTests(unittest.TestCase):
    def test_emphasis_headers_lists(self) -> None:
        html = render_markdown("# Title\n**bold** and *it*\n- item")
        self.assertEqual(html, "<h1>Title</h1>\n<strong>bold</strong> and <em>it</em>\n• item")

    def test_code_block_contents_are_not_formatted(self) -> None:
        html = render_markdown("```py\nx = a**b**c\n```")
        self.assertEqual(html, '<pre><code class="language-py">x = a**b**c\n</code></pre>')

    def test_html_is_escaped(self) -> None:
        self.assertEqual(render_markdown("<script>x</script>"), "&lt;script&gt;x&lt;/script&gt;")


class ListAndQuoteTests(unittest.TestCase):
    def test_nested_bullets_change_shape_by_depth(self) -> None:
        html = render_markdown("- a\n  - b\n    - c\n      - d\n* e\n+ f")
        self.assertEqual(html, "\u2022 a\n  \u25e6 b\n    \u25aa c\n      \u25aa d\n\u2022 e\n\u2022 f")

    def test_star_bullet_is_not_italics(self) -> None:
        html = render_markdown("* one\n* two")
        self.assertNotIn("<em>", html)

    def test_numbered_items_get_styled_numbers(self) -> None:
        html = render_markdown("1. first\n  2) nested")
        self.assertEqual(
            html,
            '<span class="md-list-number">1.</span> first\n  <span class="md-list-number">2.</span> nested',
        )

    def test_blockquote_groups_lines(self) -> None:
        html = render_markdown("before\n> one **bold**\n>two\nafter")
        self.assertEqual(html, "before\n<blockquote>one <strong>bold</strong>\ntwo</blockquote>after")


class HighlightTests(unittest.TestCase):
    def test_python_tokens(self) -> None:
        html = highlight('def f():  # hi\n    return "x" + 1', "python")
        self.assertIn('<span class="tok-keyword">def</span>', html)
        self.assertIn('<span class="tok-comment"># hi</span>', html)
        self.assertIn('<span class="tok-string">&quot;x&quot;</span>', html)
        self.assertIn('<span class="tok-number">1</span>', html)

    def test_strings_hide_comment_markers(self) -> None:
        html = highlight('url = "http://x#y"', "py")
        self.assertNotIn("tok-comment", html)

    def test_shell_hash_inside_variable_is_not_a_comment(self) -> None:
        html = highlight("echo ${#arr[@]} # count", "bash")
        self.assertEqual(html.count("tok-comment"), 1)
        self.assertIn("${#arr[@]}", html)

    def test_diff_lines(self) -> None:
        html = highlight("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n same", "diff")
        for kind in ("meta", "hunk", "removed", "added"):
            self.assertIn(f"tok-{kind}", html)

    def test_unknown_language_is_left_alone(self) -> None:
        self.assertIsNone(highlight("x", "brainfuck"))
        self.assertEqual(render_markdown("```brainfuck\n<+>\n```"),
                         '<pre><code class="language-brainfuck">&lt;+&gt;\n</code></pre>')

    def test_code_is_escaped_in_highlighted_blocks(self) -> None:
        html = render_markdown("```js\nif (a < b) { x = '<b>' }\n```")
        self.assertIn("&lt;", html)
        self.assertNotIn("<b>", html)


class CodeBlockSpacingTests(unittest.TestCase):
    def test_line_breaks_around_code_blocks_are_dropped(self) -> None:
        html = render_markdown("Before:\n\n```\nx\n```\n\nAfter")
        self.assertEqual(html, 'Before:\n<pre><code class="language-">x\n</code></pre>After')


class MemoryCitationTests(unittest.TestCase):
    BLOCK = (
        "<oai-mem-citation>\n<citation_entries>\n"
        "MEMORY.md:383-405|note=[slack digest source of truth]\n"
        "extensions/x/resources/a.md:1-2|note=[other]\n"
        "</citation_entries>\n<rollout_ids>\n019e-a\n019e-b\n</rollout_ids>\n</oai-mem-citation>"
    )

    def test_split_citations(self) -> None:
        text, entries, rollouts = split_memory_citations("Done.\n\n" + self.BLOCK)
        self.assertEqual(text, "Done.")
        self.assertEqual(entries[0], {"location": "MEMORY.md:383-405", "note": "slack digest source of truth"})
        self.assertEqual(len(entries), 2)
        self.assertEqual(rollouts, ["019e-a", "019e-b"])

    def test_unclosed_block_and_typo_close_tag(self) -> None:
        text, entries, rollouts = split_memory_citations(
            "Done.\n<oai-mem-citation>\n<citation_entries>\nMEMORY.md:1-2|note=[n]\n"
            "</citation_entries>\n<rollout_ids>\nr1\n</rollup_ids>"
        )
        self.assertEqual((text, len(entries), rollouts), ("Done.", 1, ["r1"]))

    def test_text_without_citations_is_unchanged(self) -> None:
        self.assertEqual(split_memory_citations("plain\n"), ("plain\n", [], []))


class PlaceholderTests(unittest.TestCase):
    """Input NULs must never be read as the renderer's internal placeholders."""

    def test_nul_digits_render_as_text(self) -> None:
        self.assertEqual(render_markdown("x \x0099\x00 y"), "x \ufffd99\ufffd y")

    def test_nul_placeholder_in_code_does_not_loop(self) -> None:
        self.assertEqual(render_markdown("`\x000\x00`"), "<code>\ufffd0\ufffd</code>")

    def test_forged_placeholder_cannot_inject_attributes(self) -> None:
        html = render_markdown("[first](https://a/onmouseover=x=42//) [second](\x000\x00)")
        self.assertEqual(html.count("<a "), 1)
        self.assertNotIn('title="<', html)

    def test_parked_markup_never_lands_in_an_attribute(self) -> None:
        html = render_markdown("[a](```x\ncode\n```)")
        self.assertNotRegex(html, r'(href|title)="[^"]*<')


class LinkEdgeTests(unittest.TestCase):
    def test_quoted_url_stops_at_the_quote(self) -> None:
        html = render_markdown('see "https://example.com/a" now')
        self.assertIn('<a href="https://example.com/a" target="_blank"', html)
        self.assertIn("https://example.com/a</a>&quot; now", html)
        html = render_markdown("see 'https://example.com/a' now")
        self.assertIn('href="https://example.com/a"', html)

    def test_angle_autolink_with_query(self) -> None:
        html = render_markdown("<https://example.org/?a=1&b=2>")
        self.assertEqual(
            html,
            '<a href="https://example.org/?a=1&amp;b=2" target="_blank" '
            'rel="noopener noreferrer">https://example.org/?a=1&amp;b=2</a>',
        )

    def test_balanced_parentheses_in_targets(self) -> None:
        html = render_markdown("[w](https://en.wikipedia.org/wiki/Foo_(bar)) and https://x.org/a_(b).")
        self.assertIn('href="https://en.wikipedia.org/wiki/Foo_(bar)"', html)
        self.assertIn('href="https://x.org/a_(b)"', html)
        self.assertIn("a_(b)</a>.", html)

    def test_unbalanced_paren_after_bare_url_stays_outside(self) -> None:
        html = render_markdown("(see https://x.org/a)")
        self.assertIn('href="https://x.org/a"', html)
        self.assertIn("</a>)", html)

    def test_angle_wrapped_path_with_spaces(self) -> None:
        html = render_markdown("[notes](</Users/me/My Notes/a.md>)")
        self.assertIn('<span class="md-path" title="/Users/me/My Notes/a.md">notes</span>', html)

    def test_bold_inside_link_label(self) -> None:
        html = render_markdown("[**b**](https://x.com)")
        self.assertIn("><strong>b</strong></a>", html)

    def test_url_inside_bold(self) -> None:
        html = render_markdown("**https://x.com**")
        self.assertEqual(
            html,
            '<strong><a href="https://x.com" target="_blank" rel="noopener noreferrer">'
            "https://x.com</a></strong>",
        )


class FenceTests(unittest.TestCase):
    def test_backticks_inside_code_do_not_close_the_fence(self) -> None:
        html = render_markdown('```python\ns = "```"\nprint("**literal**")\n```\nafter')
        self.assertNotIn("<strong>", html)
        self.assertIn("**literal**", html)
        self.assertTrue(html.endswith("</code></pre>after"))

    def test_longer_fence_holds_a_markdown_example(self) -> None:
        html = render_markdown("````md\n```py\nx\n```\n````")
        self.assertEqual(html.count("<pre>"), 1)
        self.assertIn("```py\nx\n```\n</code>", html)

    def test_tilde_fence(self) -> None:
        self.assertIn('<pre><code class="language-sh">', render_markdown("~~~sh\nls\n~~~"))

    def test_space_after_language(self) -> None:
        html = render_markdown("```python \nx = 1\n```")
        self.assertIn('class="language-python"', html)

    def test_indented_fence_in_a_list(self) -> None:
        html = render_markdown("- item\n  ```py\n  x = 1\n  ```")
        self.assertIn('<pre><code class="language-py">x = <span class="tok-number">1</span>\n</code></pre>', html)

    def test_unclosed_fence_runs_to_the_end(self) -> None:
        html = render_markdown("before\n```\n**not bold**")
        self.assertNotIn("<strong>", html)
        self.assertIn("<pre><code", html)


class EmphasisTests(unittest.TestCase):
    def test_arithmetic_is_not_italic(self) -> None:
        self.assertEqual(render_markdown("2 * 3 * 4"), "2 * 3 * 4")

    def test_italic_and_bold_still_work(self) -> None:
        self.assertEqual(render_markdown("*a* and **b c**"), "<em>a</em> and <strong>b c</strong>")

    def test_italic_inside_bold(self) -> None:
        self.assertEqual(render_markdown("**a *b* c**"), "<strong>a <em>b</em> c</strong>")

    def test_deeper_headings(self) -> None:
        self.assertEqual(render_markdown("#### Four\n###### Six"), "<h4>Four</h4>\n<h6>Six</h6>")
        self.assertEqual(render_markdown("####### Seven"), "####### Seven")


class CitationInCodeTests(unittest.TestCase):
    EXAMPLE = (
        "Example:\n```xml\n<oai-mem-citation>\n<citation_entries>\na.py:1|note=[example]\n"
        "</citation_entries>\n```\nUNIQUE_END"
    )

    def test_quoted_block_in_a_fence_stays(self) -> None:
        self.assertEqual(split_memory_citations(self.EXAMPLE), (self.EXAMPLE, [], []))

    def test_real_block_after_an_example_is_still_split(self) -> None:
        text = self.EXAMPLE + (
            "\n\n<oai-mem-citation>\n<citation_entries>\nMEMORY.md:1|note=[real]\n"
            "</citation_entries>\n</oai-mem-citation>"
        )
        body, entries, _ = split_memory_citations(text)
        self.assertEqual(body, self.EXAMPLE)
        self.assertEqual(entries, [{"location": "MEMORY.md:1", "note": "real"}])


class PathologicalInputTests(unittest.TestCase):
    """Unmatched openers must not make rendering quadratic."""

    CASES = {
        "brackets": "[" * 64_000,
        "bold openers": "**x " * 16_000,
        "italic openers": "*x " * 21_000,
        "angle links": "<https://a" * 6_400,
        "block comments": "```js\n" + "/*x\n" * 16_000 + "```",
        "triple quotes": "```py\n" + '"""x\n' * 12_800 + "```",
        "template strings": "```js\n" + "`x\n" * 21_000 + "```",
        "citation tags": "<oai-mem-citation>" * 3_500,
    }

    def test_each_case_renders_quickly(self) -> None:
        import time

        for name, text in self.CASES.items():
            with self.subTest(name):
                start = time.perf_counter()
                render_markdown(text)
                split_memory_citations(text)
                self.assertLess(time.perf_counter() - start, 1.0)


if __name__ == "__main__":
    unittest.main()
