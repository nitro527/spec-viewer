from specview.diff import align, render_annotated, render_diff, tokenize


def test_math_is_atomic():
    toks = tokenize('<p>x <span class="math inline">a_{b} + c</span> y</p>')
    assert [t.kind for t in toks].count("atom") == 1


def test_render_diff_word_level():
    out = render_diff("<p>defined in clause 5.2.1.</p>", "<p>defined in clause 5.2.1 of TS 38.211.</p>")
    assert out == "<p>defined in clause 5.2.1 <ins>of TS 38.211</ins>.</p>"


def test_annotation_never_spans_tags():
    toks = tokenize("<p>a <b>b</b> c</p>")
    out = render_annotated(toks, [1] * len(toks), ["15.0.0", "16.0.0"])
    assert out.count('<span class="vc"') == 3
    assert "<b><span" in out


def test_align_pairs_similar_blocks():
    old = ["the quick brown fox", "unrelated paragraph here"]
    new = ["the quick brown fox jumps", "brand new text entirely"]
    pairs = align(old, new, key=lambda s: s, text=lambda s: s)
    assert (0, 0) in pairs
    assert (None, 1) in pairs and (1, None) in pairs


def test_multiline_math_groups():
    from lxml import html

    from specview.parse_docx import _fix_multiline_math, _top_groups

    assert _top_groups("{a = 1}{b_{x} = 2}") == ["a = 1", "b_{x} = 2"]
    assert _top_groups("{a}+{b}") is None and _top_groups("x_{a}") is None and _top_groups("{a}") is None
    root = html.fragment_fromstring('<p><span class="math inline">{a = 1}{b = 2}</span></p>', create_parent="div")
    _fix_multiline_math(root)
    span = root.xpath("//span")[0]
    assert span.get("class") == "math display"
    assert span.text == r"\begin{gathered}a = 1 \\ b = 2\end{gathered}"


def test_big_table_diff_is_row_wise_and_exact():
    import random
    import time

    from specview.diff import opcodes, tokenize

    rnd = random.Random(1)
    rows = [f"<tr><td>row {i} value {rnd.randint(0, 9)}</td><td>{' '.join('w%d' % rnd.randint(0, 50) for _ in range(30))}</td></tr>"
            for i in range(1500)]
    old = "<table>" + "".join(rows) + "</table>"
    rows[100] = rows[100].replace("value", "VALUE")
    rows[900:902] = ["<tr><td>inserted row</td></tr>"]
    new = "<table>" + "".join(rows) + "</table>"
    a, b = tokenize(old), tokenize(new)
    t = time.time()
    ops = opcodes(a, b)
    assert time.time() - t < 5  # was quadratic: minutes on conformance-spec tables
    # opcodes tile both sequences and "equal" really is equal
    assert ops[0][1] == 0 and ops[0][3] == 0 and ops[-1][2] == len(a) and ops[-1][4] == len(b)
    for (_, _, i2, _, j2), (_, i1n, _, j1n, _) in zip(ops, ops[1:]):
        assert (i2, j2) == (i1n, j1n)
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            assert [x.text for x in a[i1:i2]] == [x.text for x in b[j1:j2]]
    changed = [x.text for tag, i1, i2, j1, j2 in ops if tag != "equal" for x in b[j1:j2]]
    assert "VALUE" in changed and "inserted" in changed and "w1" not in changed[:5]


def test_table_with_many_repeated_rows_diffs_fast():
    """Conformance-spec tables repeat rows (empty cells, "FFS"): difflib alone went quadratic on them."""
    import time

    from specview.diff import render_diff

    rows = [f"<tr><td>{i % 7}</td><td>FFS</td></tr>" if i % 3 else f"<tr><td>step {i}</td></tr>" for i in range(30000)]
    old = "<table>" + "".join(rows) + "</table>"
    rows[15000] = "<tr><td>changed step</td></tr>"
    new = "<table>" + "".join(rows) + "</table>"
    t = time.time()
    out = render_diff(old, new)
    assert time.time() - t < 10
    assert "<ins>changed</ins>" in out and out.count("<ins>") == 1


def test_whitespace_only_differences_keep_tokens_aligned():
    from specview.diff import opcodes, tokenize

    a, b = tokenize("a b  c<i>d</i>"), tokenize("a  b c <i>d</i> e")
    ops = opcodes(a, b)
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            norm = lambda toks: [" " if x.kind == "ws" else x.text for x in toks]  # noqa: E731
            assert norm(a[i1:i2]) == norm(b[j1:j2])
    assert ops[0][1] == 0 and ops[-1][2] == len(a) and ops[-1][4] == len(b)


def test_big_table_rows_that_only_split_words_differently_are_changes():
    from specview.diff import opcodes, render_diff, tokenize

    rows = [f"<tr><td>row {i} value</td></tr>" for i in range(1500)]
    old = "<table>" + "".join(rows) + "</table>"
    rows[700] = "<tr><td>row700 value</td></tr>"
    new = "<table>" + "".join(rows) + "</table>"
    out = render_diff(old, new)
    assert "<ins>row700</ins>" in out
    a, b = tokenize(old), tokenize(new)
    for tag, i1, i2, j1, j2 in opcodes(a, b):
        if tag == "equal":
            assert [x.text.strip() for x in a[i1:i2]] == [x.text.strip() for x in b[j1:j2]]


def test_whitespace_only_blocks_and_deep_anchor_nesting():
    from specview.diff import _patience, opcodes, render_diff, tokenize

    assert render_diff("abc", "  ") == "<del>abc</del>  "
    assert opcodes(tokenize(" "), tokenize(" ")) == [("equal", 0, 1, 0, 1)]
    # anchors found one per level: recursion used to hit the limit
    a = [f"u{i}" if i % 2 else "x" for i in range(6000)]
    b = list(reversed(a))
    assert _patience(a, b) is not None
