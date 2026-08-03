from wnacg.infrastructure.crawler import WnacgCrawler


def test_search_parser_extracts_metadata_and_pages() -> None:
    html = """
    <div class="gallary_item">
      <div class="title"><a href="/photos-index-aid-123.html"> Example </a></div>
      <img src="//img.example/cover.jpg">
      <div class="info_col">20張 2026-01-02</div>
    </div>
    <div class="f_left paginator"><a>1</a><a>7</a></div>
    """
    comics, pages = WnacgCrawler._parse_search_html(html)
    assert pages == 7
    assert comics[0].aid == "123"
    assert comics[0].pic_count == "20图"


def test_direct_gallery_url_detection_is_not_triggered_by_keyword() -> None:
    assert WnacgCrawler.gallery_aid("ordinary keyword") is None
    assert WnacgCrawler.gallery_aid("aid:123") == "123"
    assert WnacgCrawler.gallery_aid("https://www.wnacg.com/photos-index-aid-456.html") == "456"


def test_view_page_parser_returns_links_and_page_count() -> None:
    html = """
    <div class="pic_box"><a href="/photos-view-id-1.html"></a></div>
    <div class="pic_box"><a href="/photos-view-id-2.html"></a></div>
    <div class="f_left paginator"><span>1</span><a>3</a></div>
    """
    links, pages = WnacgCrawler._parse_view_page(html, "https://example.test")
    assert pages == 3
    assert links == [
        "https://example.test/photos-view-id-1.html",
        "https://example.test/photos-view-id-2.html",
    ]
