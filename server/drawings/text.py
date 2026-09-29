"""文字の正規化。検索語と検索対象の両方に同じ処理をかける。"""
import unicodedata


def norm(s) -> str:
    """全角半角・大文字小文字の違いをなくす（NFKC＋大文字）。"""
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s)).upper().strip()


def build_search_text(*parts) -> str:
    return " ".join(p for p in (norm(x) for x in parts) if p)
