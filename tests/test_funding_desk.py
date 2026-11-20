import re
from unittest.mock import Mock

from freightos.app import Handler


def test_driver_desk_and_existing_pages_serve_the_same_plan_and_receipt_controls():
    def read(path):
        handler = Mock(spec=Handler)
        handler.path = path
        Handler.do_GET(handler)
        status, _, body = handler.reply.call_args.args
        assert status == 200 and handler.reply.call_args.kwargs["static"]
        return body.decode()

    desk = read("/fuel-desk")
    for path in ("/fuel-desk", "/fuel-stop", "/load"):
        html = desk if path == "/fuel-desk" else read(path)
        assert "{{" not in html
        ids = re.findall(r'\bid="([^"]+)"', html)
        assert len(ids) == len(set(ids))
        if path != "/load":
            assert {"mix-priority", "payday-choices", "mix-request", "mix-path"} <= set(ids)
        if path != "/fuel-stop":
            assert {"cycle-steps", "cycle-verify", "cycle-open", "cycle-close"} <= set(ids)
    assert 'href="/fuel-desk"' in read("/")
    assert 'id="desk-save"' in desk and 'id="credit-send"' not in desk
    for asset in ("/fuel-desk.js", "/fuel-desk.css", "/funding_mix.js"):
        assert read(asset)
