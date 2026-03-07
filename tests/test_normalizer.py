"""テキスト正規化のテスト。"""

import pytest
from src.normalizer import normalize_for_matching, char_ngrams, jaccard_similarity


class TestNormalize:
    def test_basic(self):
        result = normalize_for_matching("こんにちは。")
        assert "。" not in result

    def test_filler_removal(self):
        result = normalize_for_matching("えーと、今日は")
        assert "えーと" not in result

    def test_whitespace(self):
        result = normalize_for_matching("こんにちは　　世界")
        assert "　　" not in result

    def test_nfkc(self):
        result = normalize_for_matching("ＡＢＣ")
        assert "abc" in result


class TestNgrams:
    def test_basic(self):
        ngrams = char_ngrams("abcde", n=3)
        assert ngrams == {"abc", "bcd", "cde"}

    def test_short(self):
        ngrams = char_ngrams("ab", n=3)
        assert ngrams == {"ab"}

    def test_empty(self):
        ngrams = char_ngrams("", n=3)
        assert ngrams == set()


class TestJaccard:
    def test_identical(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        assert jaccard_similarity({"a"}, {"b"}) == 0.0

    def test_partial(self):
        assert jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_empty(self):
        assert jaccard_similarity(set(), set()) == 0.0
