"""Windows-private data ACLs and owned subprocess lifetime. No global settings."""
import ctypes,os,re,sqlite3,subprocess
from pathlib import Path
from ctypes import wintypes as w

_job_handle=None

def current_sid():
 result=subprocess.run(['whoami.exe','/user','/fo','csv','/nh'],capture_output=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 match=re.search(rb'S-1-5-[0-9-]+',result.stdout)
 if result.returncode or not match:raise OSError('current Windows identity unavailable')
 return match[0].decode('ascii')

def owned_directory(path):
 candidate=Path(path)
 if candidate.exists() and (candidate.is_symlink() or (hasattr(candidate,'is_junction') and candidate.is_junction())):raise ValueError('data directory reparse points are not supported')
 root=candidate.resolve()
 if root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction()):raise ValueError('data directory reparse points are not supported')
 if any(root.iterdir()) and not (root/'.radar-owner').exists():
  database=root/'radar.sqlite3'
  if not database.is_file():raise ValueError('data directory is nonempty and is not owned by Radar')
  con=sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)
  try:
   if not con.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone() or not con.execute("SELECT value FROM meta WHERE key='service_epoch'").fetchone():raise ValueError('not a Radar data directory')
  finally:con.close()
 return root

def secure_directory(path):
 if os.name!='nt':
  os.chmod(path,0o700);return
 root=owned_directory(path);sid=current_sid();advapi=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 convert=advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW;convert.argtypes=[w.LPCWSTR,w.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.DWORD)];convert.restype=w.BOOL
 set_security=advapi.SetFileSecurityW;set_security.argtypes=[w.LPCWSTR,w.DWORD,ctypes.c_void_p];set_security.restype=w.BOOL
 kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
 descriptor=ctypes.c_void_p()
 if not convert('D:P(A;OICI;FA;;;'+sid+')(A;OICI;FA;;;SY)',1,ctypes.byref(descriptor),None):raise ctypes.WinError(ctypes.get_last_error())
 try:
  paths=[root]
  for directory,folders,files in os.walk(root,followlinks=False):
   for name in folders+files:
    candidate=Path(directory)/name
    if candidate.lstat().st_file_attributes & 0x400:raise ValueError('data directory contains a reparse point')
    if not candidate.resolve().is_relative_to(root):raise ValueError('data path escaped owner directory')
    paths.append(candidate)
  for candidate in paths:
   if not set_security(str(candidate),0x80000004,descriptor):raise ctypes.WinError(ctypes.get_last_error())
 finally:kernel.LocalFree(descriptor)
 (root/'.radar-owner').touch(exist_ok=True)

def inspect_acl(path):
 """Return only allowed SID identifiers for isolated verification; never file contents."""
 advapi=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 get=advapi.GetFileSecurityW;get.argtypes=[w.LPCWSTR,w.DWORD,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)];get.restype=w.BOOL
 needed=w.DWORD();get(str(path),4,None,0,ctypes.byref(needed));buf=ctypes.create_string_buffer(needed.value)
 if not get(str(path),4,buf,needed,ctypes.byref(needed)):raise ctypes.WinError(ctypes.get_last_error())
 present=w.BOOL();defaulted=w.BOOL();dacl=ctypes.c_void_p()
 advapi.GetSecurityDescriptorDacl.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.BOOL),ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.BOOL)]
 if not advapi.GetSecurityDescriptorDacl(buf,ctypes.byref(present),ctypes.byref(dacl),ctypes.byref(defaulted)) or not present.value or not dacl.value:raise OSError('missing protected DACL')
 class Size(ctypes.Structure):_fields_=[('count',w.DWORD),('used',w.DWORD),('free',w.DWORD)]
 info=Size();advapi.GetAclInformation.argtypes=[ctypes.c_void_p,ctypes.c_void_p,w.DWORD,w.DWORD]
 if not advapi.GetAclInformation(dacl,ctypes.byref(info),ctypes.sizeof(info),2):raise ctypes.WinError(ctypes.get_last_error())
 advapi.GetAce.argtypes=[ctypes.c_void_p,w.DWORD,ctypes.POINTER(ctypes.c_void_p)];advapi.ConvertSidToStringSidW.argtypes=[ctypes.c_void_p,ctypes.POINTER(w.LPWSTR)];kernel.LocalFree.argtypes=[ctypes.c_void_p]
 allowed=[]
 for index in range(info.count):
  ace=ctypes.c_void_p();advapi.GetAce(dacl,index,ctypes.byref(ace));kind=ctypes.c_ubyte.from_address(ace.value).value
  if kind!=0:raise OSError('unexpected non-allow ACE')
  string=w.LPWSTR()
  if not advapi.ConvertSidToStringSidW(ace.value+8,ctypes.byref(string)):raise ctypes.WinError(ctypes.get_last_error())
  allowed.append(string.value);kernel.LocalFree(ctypes.cast(string,ctypes.c_void_p))
 return {'allow_sids':allowed,'private':set(allowed)=={current_sid(),'S-1-5-18'}}

def own_child_processes():
 global _job_handle
 if os.name!='nt' or _job_handle:return
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 class Basic(ctypes.Structure):_fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('processes',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
 class IO(ctypes.Structure):_fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
 class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
 kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];kernel.CreateJobObjectW.restype=w.HANDLE;kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];kernel.GetCurrentProcess.restype=w.HANDLE
 handle=kernel.CreateJobObjectW(None,None);limits=Extended();limits.basic.flags=0x2000
 if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):raise ctypes.WinError(ctypes.get_last_error())
 _job_handle=handle # OS closes it on exit; KILL_ON_JOB_CLOSE also terminates curl children.
