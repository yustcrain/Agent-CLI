import unittest
from net import RetryPolicy, execute

class Fatal(Exception): pass
class Tests(unittest.TestCase):
    def test_transient_retries_and_delays(self):
        calls=[]; sleeps=[]
        def op():
            calls.append(1)
            if len(calls)<4: raise TimeoutError(str(len(calls)))
            return "ok"
        p=RetryPolicy(4,(TimeoutError,),(0.1,0.2))
        self.assertEqual(execute(op,p,sleeps.append),"ok"); self.assertEqual(len(calls),4); self.assertEqual(sleeps,[0.1,0.2,0.2])
    def test_fatal_is_not_retried_and_last_error_is_preserved(self):
        fatal=Fatal("stop"); calls=[]
        def op(): calls.append(1); raise fatal
        with self.assertRaises(Fatal) as caught: execute(op,RetryPolicy(3,(TimeoutError,),(0,)),lambda _:None)
        self.assertIs(caught.exception,fatal); self.assertEqual(len(calls),1)
        transient=TimeoutError("last")
        with self.assertRaises(TimeoutError) as caught: execute(lambda: (_ for _ in ()).throw(transient),RetryPolicy(1,(TimeoutError,),(0,)),lambda _:None)
        self.assertIs(caught.exception,transient)
    def test_invalid_policy_before_operation(self):
        for policy in (RetryPolicy(0),RetryPolicy(True),RetryPolicy(2,delays=()),RetryPolicy(2,delays=(-1,))):
            called=[]
            with self.assertRaises(ValueError): execute(lambda: called.append(1),policy,lambda _:None)
            self.assertEqual(called,[])

unittest.main()
