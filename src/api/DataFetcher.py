import requests

class DataFetcher:
    """
    Fetch data from the course planner API
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.data = None

    def get(self) -> dict[str, object]:
        """
        Get data from the API
        """

        if self.data is not None:
            return self.data

        resp = requests.get(self.url)

        if resp.status_code != 200:
            return {}

        json_resp = resp.json()
        if json_resp.get("status") != "success":
            return {}

        self.data = {"data": json_resp["data"]["query"]["rows"]}
        return self.data
