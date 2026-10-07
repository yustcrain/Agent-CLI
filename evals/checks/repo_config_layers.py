import copy
import unittest
from app.config import load_config

class Tests(unittest.TestCase):
    def test_deep_merge_and_no_mutation(self):
        defaults={"db":{"host":"h","port":1,"opts":{"ssl":True}},"xs":[1],"keep":7}
        overrides={"db":{"port":2,"opts":{"timeout":5}},"xs":[9],"new":{"a":1}}
        before=(copy.deepcopy(defaults),copy.deepcopy(overrides))
        got=load_config(overrides,defaults)
        self.assertEqual(got,{"db":{"host":"h","port":2,"opts":{"ssl":True,"timeout":5}},"xs":[9],"keep":7,"new":{"a":1}})
        got["db"]["opts"]["ssl"]=False; got["xs"].append(10)
        self.assertEqual((defaults,overrides),before)
    def test_types(self):
        for a,b in (([],{}),({},[]),(None,{})):
            with self.assertRaises(TypeError): load_config(b,a)

unittest.main()
