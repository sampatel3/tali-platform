from app.main import _build_cors_origins


def test_www_frontend_url_allows_apex_origin():
    origins = _build_cors_origins("https://www.taali.ai", None)

    assert "https://www.taali.ai" in origins
    assert "https://taali.ai" in origins


def test_extra_origins_are_normalized_and_deduped():
    origins = _build_cors_origins(
        "https://www.taali.ai/",
        " https://taali.ai/ , https://frontend-psi-navy-15.vercel.app/ ",
    )

    assert origins.count("https://taali.ai") == 1
    assert "https://frontend-psi-navy-15.vercel.app" in origins
