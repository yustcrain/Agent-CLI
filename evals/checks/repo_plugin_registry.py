import unittest
from plugins import Plugin, Registry

class Tests(unittest.TestCase):
    def test_normalized_aliases_and_idempotence(self):
        r=Registry(); p=Plugin(" Search ",( "Find", "LOOKUP")); r.register(p); r.register(p)
        for key in ("search"," SEARCH ","find","lookup"," LookUp "): self.assertIs(r.get(key),p)
    def test_collision_is_atomic(self):
        r=Registry(); one=Plugin("one",("shared",)); two=Plugin("two",("ok"," SHARED "))
        r.register(one); before=dict(r._plugins)
        with self.assertRaises(ValueError): r.register(two)
        self.assertEqual(r._plugins,before)
        with self.assertRaises(KeyError): r.get("two")
    def test_empty_rejected_without_mutation(self):
        r=Registry()
        with self.assertRaises(ValueError): r.register(Plugin("x",(" ",)))
        self.assertEqual(r._plugins,{})

unittest.main()
