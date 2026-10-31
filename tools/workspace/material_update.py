"""Transactional coordinator for an exact public material gitlink and root projections."""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
import bundlelib as B

JOURNAL='.course/material-update.json'
MATERIALS='SUKUNA-AI/study-materials'
URL='https://github.com/'+MATERIALS+'.git'

def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,timeout=60)
    if result.returncode:
        raise B.BundleError(result.stderr.decode('utf-8','replace').strip() or 'git failed')
    return result.stdout

def text(root,*args):
    return git(root,*args).decode('utf-8','replace').strip()

def pin_valid(pin):
    if not isinstance(pin,dict) or set(pin)!={'repository','repository_id','url','commit','digest','path'}:
        raise B.BundleError('Unknown materials pin schema')
    if pin['repository']!=MATERIALS or pin['url']!=URL or pin['path']!='materials' or pin['repository_id']!=1440790349:
        raise B.BundleError('Untrusted material repository identity')
    if not re.fullmatch('[0-9a-f]{40}',str(pin['commit'])) or not re.fullmatch('[0-9a-f]{64}',str(pin['digest'])):
        raise B.BundleError('Incomplete material pin/digest')

def clean(root):
    if text(root,'status','--porcelain','--untracked-files=all'):
        raise B.BundleError('Conflict: material submodule has local changes; nothing overwritten')

def check_release(record,pin,read):
    saved=record.get('digest')
    raw={k:v for k,v in record.items() if k!='digest'}
    if saved!=pin['digest'] or B.sha256_hex(B.canonical_json(raw))!=saved:
        raise B.BundleError('Material release digest mismatch')
    allowed={'schema_version','course_id','source_commit','content_digest','export_version','task_schema_version','compatible_previous_releases','files','digest'}
    if set(record)!=allowed or record['schema_version']!=1:
        raise B.BundleError('Unknown material release schema')
    rows=record['files']
    paths=[x['path'] for x in rows]
    if B.check_path_set(paths) or len(paths)!=len(set(paths)):
        raise B.BundleError('Invalid material file paths')
    for entry in rows:
        if set(entry)!={'path','sha256','bytes'}:
            raise B.BundleError('Unknown material file entry')
        data=read(entry['path'])
        if len(data)!=entry['bytes'] or B.sha256_hex(data)!=entry['sha256']:
            raise B.BundleError('Material file checksum mismatch: '+entry['path'])

def verify_pin(root,pin):
    pin_valid(pin)
    gm=B.safe_target(root,'.gitmodules')
    if text(root,'config','-f',str(gm),'--get','submodule.materials.url')!=URL:
        raise B.BundleError('Untrusted .gitmodules URL')
    if text(root,'config','-f',str(gm),'--get','submodule.materials.path')!='materials':
        raise B.BundleError('Unexpected submodule path')
    sub=B.safe_target(root,'materials')
    if not (sub/'.git').exists():
        raise B.BundleError('Initialize pinned materials: git submodule update --init --recursive')
    if text(sub,'remote','get-url','origin')!=URL:
        raise B.BundleError('Material origin changed; restore verified public origin')
    if text(sub,'rev-parse','HEAD')!=pin['commit']:
        raise B.BundleError('Material HEAD differs from root pin')
    clean(sub)
    record=json.loads((sub/'release.json').read_text(encoding='utf-8'))
    check_release(record,pin,lambda name:B.safe_target(sub,name).read_bytes())
    # Root projections must be exact copies from the pinned release.
    for name in ('.course/program.json','.course/task-index.json'):
        if (root/name).read_bytes()!=(sub/name).read_bytes():
            raise B.BundleError('Root projection differs from materials: '+name)

def journal_write(path,data):
    B._fsync_write(path,B.canonical_json(data))

def recover(root):
    path=root/JOURNAL
    if not path.exists():
        return B.recover(root)
    data=json.loads(path.read_text(encoding='utf-8'))
    sub=B.safe_target(root,'materials')
    clean(sub)
    head=text(sub,'rev-parse','HEAD')
    if head not in (data['old_commit'],data['new_commit']):
        raise B.BundleError('Material HEAD changed after interrupted update; preserve and inspect manually')
    if (root/B.JOURNAL_PATH).exists():
        B.recover(root)
    installed=B.load_manifest(root/B.MANIFEST_PATH)
    installed_digest=B.bundle_digest(installed)
    if installed_digest==data['new_digest']:
        # Root apply completed before process interruption; preserve the coherent new release.
        verify_pin(root,installed['materials_release'])
        path.unlink()
        return 'Recovered: verified completed root/material release'
    if installed_digest!=data['old_digest']:
        raise B.BundleError('Root changed after interrupted update; no automatic overwrite')
    git(sub,'checkout','--detach',data['old_commit'])
    verify_pin(root,installed['materials_release'])
    path.unlink()
    return 'Recovered: old material pin and root projections restored'

def main(a,root):
    if a.command=='recover':
        print(recover(root)); return 0
    if (root/JOURNAL).exists() or (root/B.JOURNAL_PATH).exists():
        raise B.BundleError('Interrupted update journal found; run tools/study.py recover first')
    old=B.load_manifest(root/B.MANIFEST_PATH)
    new=B.load_manifest(Path(a.bundle)/B.MANIFEST_PATH)
    if not old.get('materials_release') or not new.get('materials_release'):
        raise B.BundleError('Legacy flat material layout requires explicit migration; no overwrite')
    pin_valid(new['materials_release'])
    verify_pin(root,old['materials_release'])
    if old['materials_release']['repository_id']!=new['materials_release']['repository_id'] or new.get('target_repository')!=old.get('target_repository'):
        raise B.BundleError('Repository identity changed; tooling review required')
    plan=B.plan_update(root,Path(a.bundle),new,old,expect_digest=a.expect_digest)
    if plan.blocked:
        for problem in plan.errors+plan.conflicts:
            print(problem)
        return 2 if plan.conflicts and not plan.errors else 1
    if any(o.path.startswith('materials/') or o.path=='materials' for o in plan.ops):
        raise B.BundleError('Root bundle may not write inside the material gitlink')
    changed=set(text(root,'diff','--cached','--name-only').splitlines())
    if changed & ({o.path for o in plan.ops}|{B.MANIFEST_PATH,'materials','.gitmodules'}):
        raise B.BundleError('Conflict: staged files overlap update; preserve staged work first')
    sub=root/'materials'; pin=new['materials_release']; sha=pin['commit']
    try:
        git(sub,'cat-file','-e',sha+'^{commit}')
    except B.BundleError:
        # Fetch one reviewed pin only. No update --remote and no code execution from materials.
        git(sub,'fetch','--no-tags','origin',sha)
    read=lambda name:git(sub,'show',sha+':'+name)
    record=json.loads(read('release.json'))
    check_release(record,pin,read)
    for name in ('.course/program.json','.course/task-index.json'):
        if (Path(a.bundle)/name).read_bytes()!=read(name):
            raise B.BundleError('New root projection differs from pinned commit: '+name)
    print(json.dumps({'old_pin':old['materials_release']['commit'],'new_pin':sha,
                      'operations':[{'kind':o.kind,'path':o.path} for o in plan.ops]},ensure_ascii=False,indent=2))
    if a.dry_run:
        print('Dry run: no workspace files or pins changed'); return 0
    journal=root/JOURNAL
    clean(sub)
    if B.bundle_digest(B.load_manifest(root/B.MANIFEST_PATH))!=B.bundle_digest(old):
        raise B.BundleError('Root manifest changed during preflight; no writes performed')
    journal_write(journal,{'old_commit':old['materials_release']['commit'],'new_commit':sha,
                           'old_digest':B.bundle_digest(old),'new_digest':B.bundle_digest(new)})
    try:
        git(sub,'checkout','--detach',sha)
        if os.environ.get('STUDY_UPDATE_FAULT')=='after-materials':
            raise B.InjectedFault('after-materials')
        B.apply_plan(root,plan,(Path(a.bundle)/B.MANIFEST_PATH).read_bytes(),new,
                     fault_at=int(os.environ['COURSE_UPDATE_FAULT_AT']) if os.environ.get('COURSE_UPDATE_FAULT_AT') else None,
                     hard_crash=os.environ.get('COURSE_UPDATE_HARD_CRASH')=='1')
        verify_pin(root,pin)
        journal.unlink()
    except BaseException:
        recover(root)
        raise
    print('Update coherent. Inspect git diff, then commit materials gitlink and root projections together.')
    return 0
