import tempfile,unittest
from pathlib import Path
from radar.service import Service,Forbidden
from radar.maintenance import backup,restore

class RestoreTest(unittest.TestCase):
 def test_restored_epoch_and_credentials(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);s=Service(root/'original');token=s.provision(['read'],['*']);epoch=s.call(token,'contract',{})['service_epoch']
   backup(root/'original'/'radar.sqlite3',root/'backup.sqlite3');s.close()
   restore(root/'backup.sqlite3',root/'restored');s=Service(root/'restored')
   with self.assertRaises(Forbidden):s.call(token,'contract',{})
   token=s.provision(['read'],['*']);self.assertNotEqual(s.call(token,'contract',{})['service_epoch'],epoch);s.close()
