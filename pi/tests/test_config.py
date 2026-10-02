from nt532.config import load_site


def test_site_config_loads():
    site = load_site()
    assert site["tags"]["family"] == "36h11"
    assert site["board"]["plane_y"] == site["table"]["depth"]
