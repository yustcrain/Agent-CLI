import unittest
from sdk import Client, Page

class Tests(unittest.TestCase):
    def test_collects_all_pages_once(self):
        pages={None:Page([1,2],"a"),"a":Page([3],"b"),"b":Page([4,5],None)}; calls=[]
        def fetch(c): calls.append(c); return pages[c]
        self.assertEqual(Client(fetch).fetch_all(),[1,2,3,4,5]); self.assertEqual(calls,[None,"a","b"])
    def test_loop_and_limit(self):
        calls=[]
        def loop(c): calls.append(c); return Page([],"x")
        with self.assertRaises(RuntimeError): Client(loop).fetch_all()
        self.assertEqual(calls,[None,"x"])
        with self.assertRaises(RuntimeError): Client(lambda c: Page([],str(c))).fetch_all(2)
        for bad in (0,-1,True):
            with self.assertRaises(ValueError): Client(lambda c: Page([],None)).fetch_all(bad)

unittest.main()
