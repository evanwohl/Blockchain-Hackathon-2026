from freightos.app import allowed_demo_request


def test_forwarded_payment_origin(monkeypatch):
    monkeypatch.setenv("CODESPACE_NAME", "freight-preview")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    host = "freight-preview-8783.app.github.dev"
    origin = f"https://{host}"
    monkeypatch.delenv("CODESPACES", raising=False)
    assert not allowed_demo_request(host, origin, 8783)
    monkeypatch.setenv("CODESPACES", "true")
    for proxy_host in (host, "localhost:8783", "127.0.0.1"):
        assert allowed_demo_request(proxy_host, origin, 8783)
    assert not allowed_demo_request(host, None, 8783)
    assert not allowed_demo_request(host, f"http://{host}", 8783)
    assert not allowed_demo_request(host, "https://other-8783.app.github.dev", 8783)
    assert not allowed_demo_request("other-8783.app.github.dev", origin, 8783)
    assert not allowed_demo_request(host, origin, 8784)
    assert allowed_demo_request("127.0.0.1:8783", None, 8783)
