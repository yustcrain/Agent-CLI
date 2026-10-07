import copy
import unittest
from events import decode, Event

class Tests(unittest.TestCase):
    def test_v1_and_v2_no_mutation_or_aliasing(self):
        for payload,expected in [({"version":1,"user":"u","type":"click","data":{"x":[1]}},Event("u","click",{"x":[1]})),({"version":2,"user_id":"u2","kind":"view","metadata":{"a":1}},Event("u2","view",{"a":1}))]:
            before=copy.deepcopy(payload); got=decode(payload); self.assertEqual(got,expected); self.assertEqual(payload,before)
            got.metadata["changed"]=True; self.assertEqual(payload,before)
    def test_invalid(self):
        for value in (None,[],{}, {"version":True}, {"version":3}, {"version":2,"user_id":"u","kind":"x","metadata":[]}):
            with self.assertRaises(ValueError): decode(value)

unittest.main()
