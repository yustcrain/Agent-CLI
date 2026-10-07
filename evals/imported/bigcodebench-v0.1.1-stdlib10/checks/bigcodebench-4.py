from pathlib import Path
import unittest, sys
exec(compile(Path('solution.py').read_text(), 'solution.py', 'exec'), globals())
import unittest
class TestCases(unittest.TestCase):
    def test_case_1(self):
        """Checks the basic functionality with single-element lists."""
        input_dict = {'a': [1], 'b': [2], 'c': [3]}
        expected_output = {1: 1, 2: 1, 3: 1}
        self.assertEqual(task_func(input_dict), expected_output)
    def test_case_2(self):
        """Verifies the function with lists that have distinct integers."""
        input_dict = {'a': [1, 2], 'b': [3, 4], 'c': [5, 6]}
        expected_output = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1}
        self.assertEqual(task_func(input_dict), expected_output)
        
    def test_case_3(self):
        """ Tests the function with lists containing duplicate integers to ensure counts are aggregated correctly."""
        input_dict = {'a': [1, 1, 2], 'b': [3, 4, 4], 'c': [5, 5, 5]}
        expected_output = {1: 2, 2: 1, 3: 1, 4: 2, 5: 3}
        self.assertEqual(task_func(input_dict), expected_output)
        
    def test_case_4(self):
        """ Validates how the function handles an empty dictionary."""
        input_dict = {}
        expected_output = {}
        self.assertEqual(task_func(input_dict), expected_output)
        
    def test_case_5(self):
        """Ensures the function handles dictionaries where lists are empty correctly."""
        input_dict = {'a': [], 'b': [], 'c': []}
        expected_output = {}
        self.assertEqual(task_func(input_dict), expected_output)
    def test_case_6(self):
        """Test input with mixed integer and non-integer types to see if function filters or fails gracefully"""
        input_dict = {'a': [1, 2, 'three'], 'b': [4, None], 'c': [5, [6]]}
        with self.assertRaises(TypeError):
            task_func(input_dict)
    def test_case_7(self):
        """Test with large lists to evaluate performance"""
        input_dict = {'a': list(range(1000)), 'b': list(range(1000))}
        expected_output = {i: 2 for i in range(1000)}
        result = task_func(input_dict)
        self.assertEqual(result, expected_output)
    def test_case_8(self):
        """Test with non-string keys to see how function handles it"""
        input_dict = {1: [1, 2, 3], 2.5: [4, 5, 6]}
        expected_output = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1}
        self.assertEqual(task_func(input_dict), expected_output)
suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
if suite.countTestCases() == 0: raise RuntimeError('No benchmark tests collected')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
