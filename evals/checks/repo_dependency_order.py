import unittest
from build import Project, order_projects

class Tests(unittest.TestCase):
    def test_dependency_and_stable_order(self):
        ps=[Project("app",("db","core")),Project("misc"),Project("core"),Project("db",("core",))]
        self.assertEqual(order_projects(ps),["misc","core","db","app"]); self.assertEqual(ps[0].dependencies,("db","core"))
    def test_errors(self):
        cases=[([Project("a"),Project("a")],"duplicate"),([Project("a",("x",))],"missing"),([Project("a",("b",)),Project("b",("a",))],"cycle")]
        for projects,word in cases:
            with self.assertRaisesRegex(ValueError,word): order_projects(projects)

unittest.main()
