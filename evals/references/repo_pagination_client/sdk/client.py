class Client:
    def __init__(self, fetch_page):
        self.fetch_page = fetch_page

    def fetch_all(self, max_pages=100):
        if type(max_pages) is not int or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer")
        items=[]; cursor=None; seen=set()
        for _ in range(max_pages):
            if cursor is not None:
                if cursor in seen:
                    raise RuntimeError("pagination cursor repeated")
                seen.add(cursor)
            page=self.fetch_page(cursor)
            items.extend(page.items)
            cursor=page.next_cursor
            if cursor is None:
                return items
        raise RuntimeError("pagination exceeded max_pages")
