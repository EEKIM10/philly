import requests
from urllib.parse import urlparse


def determine_homeserver(url: str):
    """Reserves the homeserver base URL."""
    if not url.startswith("http"):
        url = "http://" + url
    parsed = urlparse(url)
    qualified = "http://{base}/.well-known/matrix/client".format(base=parsed.netloc)
    response = requests.get(qualified, allow_redirects=True)
    if response.status_code != 200:
        # Assume 8448
        return "http://{base}:8448".format(base=parsed.netloc)

    return response.json()["m.homeserver"]["base_url"]


def is_valid_server(url: str) -> bool:
    """Checks if a server is valid to use."""
    url = urlparse(url)
    url = url._replace(path="/_matrix/client/versions")
    try:
        response = requests.get(url.geturl())
    except requests.exceptions.ConnectionError:
        return False
    return response.status_code == 200
