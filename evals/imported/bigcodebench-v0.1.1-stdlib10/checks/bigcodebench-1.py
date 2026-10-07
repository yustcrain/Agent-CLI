from pathlib import Path
import unittest, sys
exec(compile(Path('solution.py').read_text(), 'solution.py', 'exec'), globals())
import unittest
import string
class TestCases(unittest.TestCase):
    def setUp(self):
        # Prepare valid characters and set a random seed for reproducibility
        self.valid_chars = string.ascii_uppercase + string.ascii_lowercase
        random.seed(42)  # Ensuring reproducibility for tests
    def test_generated_string_properties(self):
        # Consolidated test for different lengths to check structure and correctness
        test_lengths = [10, 50, 100, 150, 5]
        for length in test_lengths:
            with self.subTest(length=length):
                result = task_func(length)
                self.assertTrue(len(result) <= length, "Length of result should be <= requested string length")
                self.assertEqual(sum(result.values()), length, f"Total counts should sum to {length}")
                self.assertTrue(all(char in self.valid_chars for char in result), "All characters should be valid letters")
    def test_zero_length(self):
        # Test edge case where length is zero
        result = task_func(0)
        self.assertEqual(len(result), 0, "Result should be empty for zero length")
        self.assertEqual(sum(result.values()), 0, "Sum of counts should be zero for zero length")
    def test_negative_length(self):
        # Test handling of negative length input
        with self.assertRaises(ValueError, msg="Negative length should raise an error"):
            task_func(-1)
suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
if suite.countTestCases() == 0: raise RuntimeError('No benchmark tests collected')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
