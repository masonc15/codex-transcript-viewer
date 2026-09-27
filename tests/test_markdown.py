from __future__ import annotations

import unittest

from codex_transcript_viewer.markdown import render_markdown


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


if __name__ == "__main__":
    unittest.main()
