import unittest
from datetime import datetime,timedelta,timezone
from auth import create_session,is_active,rotate

class Tests(unittest.TestCase):
    def test_boundaries_and_rotation(self):
        now=datetime(2024,1,1,tzinfo=timezone.utc); s=create_session("a",60,now)
        self.assertTrue(is_active(s,now+timedelta(seconds=59))); self.assertFalse(is_active(s,s.expires_at))
        r=rotate(s,"b",now+timedelta(seconds=1)); self.assertEqual(r.token,"b"); self.assertEqual(r.expires_at,s.expires_at); self.assertEqual(r.issued_at,now+timedelta(seconds=1))
        with self.assertRaises(ValueError): rotate(s,"c",s.expires_at)
    def test_validation(self):
        aware=datetime.now(timezone.utc); naive=datetime.now()
        for token,ttl,now in (("",1,aware),("x",0,aware),("x",True,aware),("x",1,naive)):
            with self.assertRaises(ValueError): create_session(token,ttl,now)
        s=create_session("x",1,aware)
        with self.assertRaises(ValueError): is_active(s,naive)
        with self.assertRaises(ValueError): rotate(s," ",aware)

unittest.main()
