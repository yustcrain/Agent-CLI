import unittest
from domain.inventory import Inventory
from domain.orders import create_order

class Tests(unittest.TestCase):
    def test_duplicate_lines_are_aggregated(self):
        inv=Inventory({"a":5,"b":4}); order=create_order(inv,[("a",2),("a",3),("b",1)])
        self.assertEqual(inv.stock,{"a":0,"b":3}); self.assertEqual(order["status"],"reserved")
    def test_every_failure_is_atomic(self):
        for lines,error in [([("a",2),("b",9)],ValueError),([("a",2),("missing",1)],KeyError),([("a",0)],ValueError),([("a",True)],ValueError)]:
            inv=Inventory({"a":5,"b":4}); before=inv.stock.copy()
            with self.assertRaises(error): inv.reserve(lines)
            self.assertEqual(inv.stock,before)

unittest.main()
