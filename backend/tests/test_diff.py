from specview.diff import align, render_annotated, render_diff, tokenize


def test_math_is_atomic():
    toks = tokenize('<p>x <span class="math inline">a_{b} + c</span> y</p>')
    assert [t.kind for t in toks].count("atom") == 1


def test_render_diff_word_level():
    out = render_diff("<p>defined in clause 5.2.1.</p>", "<p>defined in clause 5.2.1 of TS 38.211.</p>")
    assert out == "<p>defined in clause 5.2.1<ins> of TS 38.211</ins>.</p>"


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
