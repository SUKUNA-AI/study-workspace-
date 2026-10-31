"""Bounded UTF-8 learner artifacts. No discovery outside an explicitly selected task directory."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
from pathlib import Path
import bundlelib as B

MAX_FILES=32
MAX_FILE_BYTES=64*1024
MAX_TOTAL_BYTES=256*1024
MAX_REQUEST_BYTES=512*1024
BUILD_DIRS={'build','cmake-build-debug','cmake-build-release','cmakefiles','out','dist','bin','obj','node_modules','vendor','__pycache__'}
DENIED_NAMES={'agents.md','claude.md','codex.md','skill.md','cmakecache.txt','cmake_install.cmake','compile_commands.json','credentials','credentials.json','secrets.json','id_rsa','id_ed25519'}
EXTENSIONS={'.cpp','.cc','.cxx','.c','.h','.hpp','.hxx','.cu','.cuh','.md','.txt','.cmake','.json','.yaml','.yml','.sh','.py','.toml','.ini','.cfg','.csv','.tsv','.log','.patch','.diff'}

def sha(data):return hashlib.sha256(data).hexdigest()
def map_digest(files):return sha(''.join(name+'\0'+sha(files[name].encode('utf-8'))+'\n' for name in sorted(files)).encode('utf-8'))

def path_valid(name):
    error=B.path_error(name)
    if error:raise B.BundleError('Invalid selected artifact path '+str(name)+': '+error)
    parts=name.split('/');lower=[p.casefold() for p in parts]
    if (any(p.startswith('.') or p in BUILD_DIRS or p in {'books','secrets','private'} for p in lower) or lower[0] in {'materials','tools','generated'} or any(p in DENIED_NAMES for p in lower)
        or any('secret' in p or p.startswith(('token.','credentials.')) for p in lower)
        or (Path(name).suffix.casefold() not in EXTENSIONS and parts[-1] not in ('Makefile','GNUmakefile'))):
        raise B.BundleError('Private, instruction, generated, or binary artifact is forbidden: '+name)

def validate_files(files):
    if not isinstance(files,dict) or not files or len(files)>MAX_FILES:raise B.BundleError('Select 1–32 UTF-8 artifact files')
    errors=B.check_path_set(list(files))
    if errors:raise B.BundleError('; '.join(errors))
    total=0
    for name,value in files.items():
        path_valid(name)
        if not isinstance(value,str):raise B.BundleError('Artifact is not UTF-8 text: '+name)
        data=value.encode('utf-8');total+=len(data)
        if len(data)>MAX_FILE_BYTES or '\0' in value or any(ord(c)<32 and c not in '\r\n\t' for c in value):
            raise B.BundleError('Binary/control data or file exceeds 64 KiB: '+name)
    if total>MAX_TOTAL_BYTES:raise B.BundleError('Selected artifacts exceed 256 KiB UTF-8 total')
    return files

def target(root,name,manifest):
    path_valid(name)
    if not B.match_any(manifest.get('student_paths',[]),name):raise B.BundleError('Artifact outside learner-owned paths: '+name)
    managed={e['path'] for e in manifest.get('files',[])}|{e['path'] for e in manifest.get('managed_regions',[])}
    if name in managed:raise B.BundleError('Managed artifact cannot be selected or imported: '+name)
    current=root
    for part in name.split('/'):
        current=current/part
        if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):raise B.BundleError('Artifact symlink/junction forbidden: '+name)
    return B.safe_target(root,name)

def metadata(step):
    learning=(step.get('package') or {}).get('learning') or {}
    primary=learning.get('artifact_path') or step.get('artifact_path') or (step.get('result') or {}).get('path')
    required=list((step.get('submission') or {}).get('files') or [])
    for name in learning.get('deliverables',[]):
        required.append((str(Path(primary).parent).replace('\\','/')+'/'+name) if '/' not in name and primary and not primary.endswith('/') else name)
    return primary,list(dict.fromkeys(required))

def directory_files(root,name,manifest):
    folder=target(root,name.rstrip('/')+'/placeholder.txt',manifest).parent
    if not folder.is_dir():raise B.BundleError('Selected task directory does not exist: '+name)
    paths=[];excluded=[]
    for parent,dirs,files in os.walk(folder,followlinks=False):
        for child in list(dirs):
            p=Path(parent)/child
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise B.BundleError('Artifact symlink/junction forbidden: '+str(p))
            if child.startswith('.') or child.casefold() in BUILD_DIRS|{'books','private','secrets'} or child.casefold().startswith('cmake-build-'):
                dirs.remove(child);excluded.append(p.relative_to(root).as_posix()+'/')
        dirs.sort()
        for child in sorted(files):
            p=Path(parent)/child;rel=p.relative_to(root).as_posix()
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise B.BundleError('Artifact symlink/junction forbidden: '+rel)
            if child.startswith('.') or child.casefold() in DENIED_NAMES:
                excluded.append(rel);continue
            target(root,rel,manifest);paths.append(rel)
            if len(paths)>MAX_FILES:raise B.BundleError('Task directory exceeds 32 selected files; choose explicit bounded artifacts')
    return paths,excluded

def make(files,primary=None,excluded=()):
    validate_files(files)
    if primary is not None and primary not in files:raise B.BundleError('Primary source is not in selected inventory')
    return {'files':dict(sorted(files.items())),'file_digests':{n:sha(v.encode()) for n,v in sorted(files.items())},
            'digest':map_digest(files),'primary_path':primary,'primary_digest':sha(files[primary].encode()) if primary else None,
            'total_bytes':sum(len(v.encode()) for v in files.values()),'excluded':list(excluded)}

def collect(a,root,manifest,step,required=False):
    primary=getattr(a,'source',None);names=list(getattr(a,'files',None) or []);excluded=[]
    if primary:
        p=Path(primary)
        if p.is_absolute():
            try:p=p.relative_to(root)
            except ValueError:raise B.BundleError('Primary source must be inside workspace')
        primary=p.as_posix();names.insert(0,primary)
    declared,requirements=metadata(step)
    folder=getattr(a,'source_dir',None)
    if folder:
        allowed={p.rstrip('/') for p in [declared,*requirements] if isinstance(p,str) and p.endswith('/')}
        if folder.rstrip('/') not in allowed:raise B.BundleError('Directory must equal the declared task-specific artifact directory')
        found,excluded=directory_files(root,folder,manifest);names.extend(found)
    if len(names)!=len(set(names)):raise B.BundleError('Duplicate selected artifact path')
    files={}
    for name in names:
        p=target(root,name,manifest)
        if not p.is_file() or p.stat().st_size>MAX_FILE_BYTES:raise B.BundleError('Artifact missing/not a file/over 64 KiB: '+name)
        try:files[name]=p.read_bytes().decode('utf-8')
        except UnicodeDecodeError:raise B.BundleError('Artifact is not UTF-8: '+name)
    inv=make(files,primary,excluded)
    if required:
        for name in requirements:
            needed=directory_files(root,name,manifest)[0] if name.endswith('/') else [name]
            if any(n not in files for n in needed):raise B.BundleError('Required artifact not selected: '+name+'; add --file or --source-dir explicitly')
    if len(files)>1 and step.get('judge') and declared and primary!=declared:
        raise B.BundleError('Primary source must equal declared artifact path: '+declared)
    return inv

def verify_live(root,inv,manifest):
    for name,expected in inv['file_digests'].items():
        path=target(root,name,manifest)
        if not path.is_file() or sha(path.read_bytes())!=expected:raise B.BundleError('Selected artifact changed: '+name)

def save_snapshot(folder,inv):
    for name,value in inv['files'].items():
        p=B.safe_target(folder/'sources',name);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open('xb') as f:f.write(value.encode())

def verify_snapshot(folder,inv):
    for name,value in inv['files'].items():
        p=B.safe_target(folder/'sources',name)
        if not p.is_file() or p.read_bytes()!=value.encode():raise B.BundleError('Saved artifact snapshot changed: '+name)

def request_bytes(body):
    data=json.dumps(body,ensure_ascii=False).encode('utf-8')
    if len(data)>MAX_REQUEST_BYTES:raise B.BundleError('Encoded source request exceeds 512 KiB')
    return data

def import_files(root,manifest,files):
    validate_files(files);targets=[]
    for name,value in files.items():
        p=target(root,name,manifest);data=value.encode()
        if p.exists() and (not p.is_file() or p.read_bytes()!=data):raise B.BundleError('Conflict: existing artifact preserved: '+name)
        targets.append((p,data))
    created=[]
    with tempfile.TemporaryDirectory(prefix='.study-import-',dir=root) as staging:
        prepared=[]
        for n,(p,data) in enumerate(targets):
            temp=Path(staging)/str(n);temp.write_bytes(data);prepared.append((p,data,temp))
        try:
            for p,data,temp in prepared:
                target(root,p.relative_to(root).as_posix(),manifest)
                if p.exists():
                    if not p.is_file() or p.read_bytes()!=data:raise B.BundleError('Artifact changed after import preflight: '+str(p))
                    continue
                p.parent.mkdir(parents=True,exist_ok=True)
                os.link(temp,p) # Exclusive atomic creation of complete bytes; never replace a target.
                created.append((p,data))
        except BaseException:
            for p,data in reversed(created):
                if p.is_file() and p.read_bytes()==data:p.unlink()
            raise
    return [str(p) for p,_ in created]
