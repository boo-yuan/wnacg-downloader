from wnacg.infrastructure.domain_discovery import extract_official_domains


def test_extracts_current_numbered_mirrors_without_layout_dependency() -> None:
    html = """
        <main>
            <a href="https://wnacg01.link/">publication one</a>
            <a href="https://wnacg02.link/">publication two</a>
            <a href="https://www.wn08.cfd/">current one</a>
            <a href="https://www.wn08.shop/">current two</a>
            <a href="https://www.wn07.cfd/">fallback</a>
            <a href="https://www.google.cn/chrome/">browser</a>
        </main>
    """

    assert extract_official_domains(html) == ["www.wn08.cfd", "www.wn08.shop", "www.wn07.cfd"]


def test_extracts_legacy_mirror_text_and_rejects_unrelated_hosts() -> None:
    html = """
        <p>备用地址：www.wnacg.com、wnacg.ru</p>
        <a href="https://advertising.example/">unrelated</a>
        <a href="https://192.168.1.10/">private</a>
    """

    assert extract_official_domains(html) == ["www.wnacg.com", "wnacg.ru"]
