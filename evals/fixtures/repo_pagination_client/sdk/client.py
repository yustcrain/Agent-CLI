class Client:
    def __init__(self, fetch_page):
        self.fetch_page = fetch_page

    def fetch_all(self, max_pages=100):
        return list(self.fetch_page(None).items)
