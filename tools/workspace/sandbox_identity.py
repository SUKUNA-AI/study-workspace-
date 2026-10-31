"""Portable identity from a checksum-verified archive and its pinned OCI/config digest. Never extracts layers."""
import hashlib
import json
import re
import tarfile
from pathlib import Path
import bundlelib as B

def sha(data):return hashlib.sha256(data).hexdigest()
def descriptor(value):
    if not isinstance(value,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',value):raise B.BundleError('Invalid sandbox descriptor digest')
    return 'blobs/sha256/'+value[7:]

def archive_identity(path,canonical,expected_sha,expected_bytes=None):
    descriptor(canonical)
    if not re.fullmatch('[0-9a-f]{64}',str(expected_sha)):raise B.BundleError('Invalid sandbox archive checksum')
    h=hashlib.sha256();size=0
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):
            size+=len(block)
            if size>1024*1024*1024:raise B.BundleError('Sandbox archive exceeds 1 GiB')
            h.update(block)
    if h.hexdigest()!=expected_sha or expected_bytes is not None and size!=expected_bytes:raise B.BundleError('Sandbox archive checksum/size mismatch')
    with tarfile.open(path,'r:*') as archive:
        names=[m.name for m in archive.getmembers()]
        if len(names)!=len(set(names)):raise B.BundleError('Duplicate sandbox archive entries')
        def read(name):
            try:entry=archive.getmember(name)
            except KeyError:raise B.BundleError('Pinned sandbox metadata missing: '+name)
            if not entry.isfile() or entry.size>2*1024*1024:raise B.BundleError('Unsafe sandbox metadata: '+name)
            return archive.extractfile(entry).read()
        def blob(digest):
            data=read(descriptor(digest))
            if 'sha256:'+sha(data)!=digest:raise B.BundleError('Sandbox descriptor checksum mismatch')
            return json.loads(data)
        manifest=json.loads(read('manifest.json'))
        if not isinstance(manifest,list) or len(manifest)!=1:raise B.BundleError('Sandbox must contain exactly one Docker image')
        config_path=manifest[0].get('Config','')
        if re.fullmatch(r'blobs/sha256/[0-9a-f]{64}',config_path):config_id='sha256:'+config_path.rsplit('/',1)[1]
        elif re.fullmatch(r'[0-9a-f]{64}\.json',config_path):config_id='sha256:'+config_path[:-5]
        else:raise B.BundleError('Invalid Docker config descriptor')
        config_bytes=read(config_path)
        if 'sha256:'+sha(config_bytes)!=config_id:raise B.BundleError('Docker config checksum mismatch')
        config=json.loads(config_bytes);image_manifest=None
        if canonical!=config_id:
            pinned=blob(canonical)
            if 'manifests' in pinned:
                candidates=[v for v in pinned['manifests'] if v.get('platform',{}).get('os')=='linux' and v.get('platform',{}).get('architecture')=='amd64']
                if len(candidates)!=1:raise B.BundleError('Pinned sandbox platform is ambiguous/unavailable')
                image_manifest=candidates[0]['digest'];pinned=blob(image_manifest)
            if pinned.get('config',{}).get('digest')!=config_id:raise B.BundleError('Docker config is not bound to the reviewed OCI image')
            if [descriptor(v['digest']) for v in pinned.get('layers',[])]!=manifest[0].get('Layers'):raise B.BundleError('Docker/OCI layer descriptors differ')
        if config.get('os')!='linux' or config.get('architecture')!='amd64' or config.get('rootfs',{}).get('type')!='layers':raise B.BundleError('Sandbox platform/rootfs unsupported')
        layers=config['rootfs'].get('diff_ids')
        if not isinstance(layers,list) or not layers or any(not re.fullmatch(r'sha256:[0-9a-f]{64}',str(v)) for v in layers):raise B.BundleError('Invalid sandbox rootfs identity')
        return dict(proof_version=3,canonical_image_id=canonical,config_id=config_id,manifest_digest=image_manifest,
                    archive_sha256=expected_sha,archive_bytes=size,platform='linux/amd64',rootfs_diff_ids=layers,runtime_config=config.get('config',{}))

def effective(value):
    if isinstance(value,dict):return {k:effective(v) for k,v in value.items() if v not in (None,'',False,[],{})}
    if isinstance(value,list):return [effective(v) for v in value]
    return value

def matches(inspect,proof):
    return (inspect.get('Id') in (proof['canonical_image_id'],proof['config_id'])
            and inspect.get('Os')=='linux' and inspect.get('Architecture')=='amd64'
            and inspect.get('RootFS',{}).get('Type')=='layers'
            and inspect.get('RootFS',{}).get('Layers')==proof['rootfs_diff_ids']
            and effective(inspect.get('Config',{}))==effective(proof['runtime_config']))

def inspect_runtime(docker,proof,run):
    for image in dict.fromkeys((proof['canonical_image_id'],proof['config_id'])):
        result=run([docker,'image','inspect',image],capture_output=True,text=True,timeout=30)
        if result.returncode:continue
        try:rows=json.loads(result.stdout)
        except ValueError:continue
        if isinstance(rows,list) and len(rows)==1 and matches(rows[0],proof):return rows[0]['Id']
    raise B.BundleError('No installed sandbox matches pinned config, rootfs and runtime configuration')

def resolve(root,canonical,run,docker=None):
    receipt=Path(root)/'.study/sandbox.json'
    if not receipt.exists():return canonical
    state=json.loads(receipt.read_text(encoding='utf-8'))
    if state.get('proof_version')!=3:
        if state.get('image_id')!=canonical:raise B.BundleError('Unverified sandbox alias; run install-sandbox --verify-only with the pinned archive')
        return canonical # Legacy exact digest, without broadening its permitted identity.
    if state.get('canonical_image_id')!=canonical or not docker:raise B.BundleError('Sandbox profile changed or Docker missing')
    name=state.get('archive_path','')
    if not name.startswith('.study/downloads/'):raise B.BundleError('Invalid sandbox proof archive path')
    path=B.safe_target(root,name)
    proof=archive_identity(path,canonical,state.get('archive_sha256'),state.get('archive_bytes'))
    runtime=inspect_runtime(docker,proof,run)
    if state.get('image_id')!=runtime or state.get('config_id')!=proof['config_id']:raise B.BundleError('Sandbox receipt identity differs from actual pinned runtime')
    return runtime
