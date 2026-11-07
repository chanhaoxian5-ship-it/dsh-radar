import ctypes,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from ctypes import wintypes as w
from radar.windows import secure_directory,inspect_acl
@unittest.skipUnless(os.name=='nt','Windows-specific acceptance')
class WindowsLifecycleTest(unittest.TestCase):
 def test_private_directory_and_new_secret_file_acl(self):
  with tempfile.TemporaryDirectory() as parent:
   root=Path(parent)/'data';root.mkdir();secure_directory(root);secret=root/'probe.token';secret.write_text('fixed-test-not-a-real-secret')
   self.assertTrue(inspect_acl(root)['private']);self.assertTrue(inspect_acl(secret)['private'])
 def test_refuses_unowned_directory(self):
  with tempfile.TemporaryDirectory() as parent:
   root=Path(parent);(root/'unrelated.txt').write_text('preserve')
   with self.assertRaises(ValueError):secure_directory(root)
   self.assertEqual((root/'unrelated.txt').read_text(),'preserve')
 def test_backend_job_terminates_owned_fetch_child_on_parent_exit(self):
  code="from radar.windows import own_child_processes;import subprocess,sys,time;own_child_processes();c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)','radar-job-fixture'],stdout=subprocess.DEVNULL);print(c.pid,flush=True);time.sleep(60)"
  parent=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  child_handle=None;kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE;kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.CloseHandle.argtypes=[w.HANDLE];kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
  try:
   line=parent.stdout.readline();self.assertTrue(line.strip().isdigit(),line);child=int(line);child_handle=kernel.OpenProcess(0x100001,False,child);self.assertTrue(child_handle)
   parent.terminate();parent.wait(timeout=5)
   self.assertEqual(kernel.WaitForSingleObject(child_handle,5000),0)
  finally:
   if parent.poll() is None:parent.terminate();parent.wait(timeout=5)
   if child_handle:
    if kernel.WaitForSingleObject(child_handle,0)!=0:kernel.TerminateProcess(child_handle,1)
    kernel.CloseHandle(child_handle)
   parent.stdout.close();parent.stderr.close()
