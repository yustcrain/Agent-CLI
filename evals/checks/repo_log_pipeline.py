import unittest
from logs import parse_jsonl, summarize

class Tests(unittest.TestCase):
    def test_parse_and_chronological_summary(self):
        text='\n{"level":"info","timestamp":"2024-01-01T10:00:00+02:00"}\n {"level":"error","timestamp":"2024-01-01T07:30:00Z"} \n{"level":"info","timestamp":"2024-01-01T09:00:00Z"}\n'
        events=parse_jsonl(text); self.assertEqual(len(events),3)
        self.assertEqual(summarize(events),{"level_counts":{"info":2,"error":1},"first_timestamp":"2024-01-01T07:30:00Z","last_timestamp":"2024-01-01T09:00:00Z"})
    def test_empty(self):
        self.assertEqual(summarize(parse_jsonl(" \n")),{"level_counts":{},"first_timestamp":None,"last_timestamp":None})
    def test_line_numbers(self):
        for text,line in [('\n[]',2),('{"level":1,"timestamp":"x"}',1),('{"level":"x","timestamp":"bad"}',1)]:
            with self.assertRaisesRegex(ValueError,fr"line {line}"): parse_jsonl(text)

unittest.main()
