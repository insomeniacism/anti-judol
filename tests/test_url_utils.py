"""Unit tests for strict competition URL validation & deduplication normalization."""

import pytest

from app.url_utils import (
    InvalidURLError,
    domain_to_url,
    validate_and_normalize,
)


class TestNormalizationRules:
    """Verify exact competition anti-duplicate normalization behavior."""

    @pytest.mark.parametrize(
        ("url_a", "url_b"),
        [
            ("http://example.com", "https://example.com"),
            ("https://www.example.com", "https://example.com"),
            ("https://WWW.Example.COM/", "http://example.com"),
            ("https://example.com/?ref=123#top", "https://example.com"),
            ("https://www.toko.id/promo/?utm=abc#sec", "http://TOKO.ID/promo/"),
            ("https://toko.id/a/b/", "https://toko.id/a/b"),
        ],
    )
    def test_equivalent_urls_produce_same_normalized_key(self, url_a: str, url_b: str) -> None:
        assert validate_and_normalize(url_a).normalized_url == validate_and_normalize(url_b).normalized_url

    @pytest.mark.parametrize(
        ("url_a", "url_b"),
        [
            # Different subdomains must remain distinct
            ("https://blog.toko.id", "https://toko.id"),
            ("https://m.slotgacor.com", "https://slotgacor.com"),
            ("https://www2.toko.id", "https://toko.id"),
            # Path is CASE-SENSITIVE!
            ("https://toko.id/Promo", "https://toko.id/promo"),
            ("https://toko.id/Slot/Gacor", "https://toko.id/slot/gacor"),
            # Different paths remain distinct
            ("https://toko.id/a", "https://toko.id/b"),
        ],
    )
    def test_distinct_urls_produce_different_normalized_keys(self, url_a: str, url_b: str) -> None:
        assert validate_and_normalize(url_a).normalized_url != validate_and_normalize(url_b).normalized_url

    def test_exact_normalized_outputs(self) -> None:
        res = validate_and_normalize("HTTPS://WWW.Blog.Toko.ID/Promo/Bonus/?ref=1#hash/")
        assert res.domain == "blog.toko.id"
        assert res.normalized_url == "blog.toko.id/Promo/Bonus"

        root_res = validate_and_normalize("https://www.judol88.xyz/")
        assert root_res.domain == "judol88.xyz"
        assert root_res.normalized_url == "judol88.xyz"


class TestValidationRules:
    """Verify strict competition URL validation rejections."""

    @pytest.mark.parametrize(
        "bad_url",
        [
            "",
            "   ",
            "ftp://example.com",
            "javascript:alert(1)",
            "example.com",  # missing scheme
            "http://example.com:8080",
            "https://example.com:443/login",
            "https://example.com:",
            "http://user:pass@example.com",
            "https://admin@example.com/panel",
            "http://localhost/admin",
            "https://sub.localhost",
            "http://127.0.0.1/slot",
            "https://192.168.1.1",
            "http://[::1]/",
            "https://[2001:db8::1]/",
            "https://intranet/login",  # no TLD
            "https://example.com/has space",
            "https://" + "a" * 290 + ".com",  # > 300 chars
        ],
    )
    def test_invalid_urls_raise_error(self, bad_url: str) -> None:
        with pytest.raises(InvalidURLError):
            validate_and_normalize(bad_url)

    def test_max_length_300_boundary(self) -> None:
        # Exactly 300 chars should pass
        prefix = "https://example.com/"
        exact_300 = prefix + "a" * (300 - len(prefix))
        assert len(exact_300) == 300
        assert validate_and_normalize(exact_300).normalized_url == "example.com/" + "a" * (300 - len(prefix))

        # 301 chars should fail
        over_300 = exact_300 + "a"
        assert len(over_300) == 301
        with pytest.raises(InvalidURLError):
            validate_and_normalize(over_300)


class TestDomainToUrl:
    """Verify helper for crt.sh / raw domain conversion."""

    def test_strips_wildcard_and_adds_scheme(self) -> None:
        assert domain_to_url("*.slotgacor.com") == "https://slotgacor.com"
        assert domain_to_url("pinjol.id") == "https://pinjol.id"
        assert domain_to_url("http://existing.id") == "http://existing.id"
