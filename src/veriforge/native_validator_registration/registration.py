"""Offline file-only candidate registration. Never imports wheel/product code.

Run explicitly with native CPython -I -S -B after separate target authorization.
No download, installer, build backend, service, database or subprocess is used.
"""
import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import sys
import zipfile

ASSETS = Path(__file__).resolve().parent
MAX_FILE = 32 * 1048576
MAX_WHEEL = 64 * 1048576

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()

def require(value, message):
    if not value:
        raise ValueError(message)

def safe_name(name):
    require(type(name) is str and name and len(name)<=512, 'invalid archive path')
    parts=name.split('/')
    require(not any(p in ('','.','..') for p in parts) and '\\' not in name and ':' not in name,
            'invalid archive path')
    require(not PurePosixPath(name).is_absolute(), 'absolute archive path')
    require(not any(p.endswith(('.',' ')) or p.split('.')[0].upper() in
        {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
        for p in parts), 'nonportable archive path')
    return name

def no_links(path):
    path=Path(path).absolute()
    for part in (*reversed(path.parents),path):
        if part.exists() or part.is_symlink():
            require(not part.is_symlink() and not part.is_junction(), 'link or junction rejected')

def read_file(path, limit=MAX_FILE):
    path=Path(path)
    no_links(path)
    before=path.stat()
    require(type(limit) is int and 0<=limit<=MAX_WHEEL and
        stat.S_ISREG(before.st_mode) and 0<=before.st_size<=limit,'regular bounded file required')
    identity=lambda info:(info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns)
    try:
        with path.open('rb',buffering=0) as stream:
            require(identity(os.fstat(stream.fileno()))==identity(before),'file changed during open')
            # One sentinel byte detects growth without reading/allocating the
            # growing remainder. Unbuffered I/O avoids read-ahead beyond the
            # request, which is always at most limit + 1.
            raw=stream.read(min(limit,before.st_size)+1)
            require(identity(os.fstat(stream.fileno()))==identity(before),'file changed during read')
    except OSError:
        raise ValueError('bounded file descriptor unavailable') from None
    after=path.stat()
    require(identity(before)==identity(after) and len(raw)==before.st_size and len(raw)<=limit,
            'file changed during read')
    no_links(path)
    return raw

def wheel_contents(path, spec):
    """Validate fixed locked wheel and every RECORD entry before selecting files."""
    path=Path(path)
    require(path.name==spec['filename'],'unexpected wheel filename')
    archive=read_file(path,MAX_WHEEL)
    require(digest(archive)==spec['sha256'],'locked wheel digest differs')
    contents={}
    folded=set()
    total=0
    with zipfile.ZipFile(io.BytesIO(archive)) as wheel:
        entries=wheel.infolist()
        require(len(entries)<=2048,'wheel entry bound')
        for entry in entries:
            if entry.is_dir():
                safe_name(entry.filename.rstrip('/'))
                continue
            name=safe_name(entry.filename)
            require(name.casefold() not in folded,'wheel path collision')
            folded.add(name.casefold())
            require(not stat.S_ISLNK(entry.external_attr>>16),'wheel link rejected')
            require(entry.file_size<=MAX_FILE and total+entry.file_size<=MAX_WHEEL,'wheel size bound')
            total+=entry.file_size
            require(not name.endswith(('.pth','.pyc','.exe')) and
                Path(name).name not in {'sitecustomize.py','usercustomize.py'} and
                not any(p.endswith('.data') for p in name.split('/')),'unsupported wheel entry')
            raw=wheel.read(entry)
            require(len(raw)==entry.file_size,'wheel length differs')
            contents[name]=raw
    metadata=[n for n in contents if n.endswith('.dist-info/METADATA')]
    require(len(metadata)==1,'one wheel distribution required')
    info=metadata[0].rsplit('/',1)[0]
    message=BytesParser().parsebytes(contents[metadata[0]])
    normalize=lambda name:name.lower().replace('_','-')
    require(normalize(message['Name'])==normalize(spec['name']) and message['Version']==spec['version'],
            'wheel identity differs')
    require(message['License-Expression']==spec['license_expression'],'reviewed license expression differs')
    license_names=message.get_all('License-File',[])
    require(license_names,'license files required')
    license_paths=[]
    for name in license_names:
        safe_name(name)
        matches=[p for p in (info+'/licenses/'+name,info+'/'+name) if p in contents]
        require(len(matches)==1 and contents[matches[0]],'license file missing or ambiguous')
        license_paths.extend(matches)
    wheel_meta=BytesParser().parsebytes(contents[info+'/WHEEL'])
    require(wheel_meta['Wheel-Version']=='1.0','unsupported wheel version')
    require(set(wheel_meta.get_all('Tag',[])) <= {'py3-none-any','cp312-cp312-win_amd64'} and
            bool(wheel_meta.get_all('Tag',[])),'unsupported wheel tag')
    record_name=info+'/RECORD'
    rows=list(csv.reader(io.StringIO(contents[record_name].decode())))
    recorded={}
    for row in rows:
        require(len(row)==3,'invalid wheel RECORD')
        name,claimed,size=row
        safe_name(name)
        require(name not in recorded and name in contents,'invalid RECORD inventory')
        recorded[name]=claimed
        if name==record_name:
            require(not claimed and not size,'RECORD self hash must be empty')
        else:
            actual='sha256='+base64.urlsafe_b64encode(hashlib.sha256(contents[name]).digest()).rstrip(b'=').decode()
            require(claimed==actual and size==str(len(contents[name])),'wheel RECORD differs')
    require(set(recorded)==set(contents),'wheel RECORD closure differs')
    runtime={n:raw for n,raw in contents.items() if n.endswith(('.py','.pyd','.so','.dll')) or n==metadata[0]}
    require(runtime,'empty runtime')
    selected=dict(runtime)
    notices=[name for name in contents if name.startswith(info+'/licenses/') or
             (name.startswith(info+'/') and Path(name).name.upper().startswith(('LICENSE','NOTICE','COPYING')))]
    for name in (*license_paths,*notices,info+'/WHEEL',record_name):
        selected[name]=contents[name]
    return runtime,selected,{'license_expression':message['License-Expression'],
        'license_files':{n:digest(contents[n]) for n in set(license_paths)|set(notices)}}

def source_snapshot(root, expected):
    """Exact current app tree, excluding this new immutable asset package."""
    root=Path(root)
    names={p.relative_to(root).as_posix() for p in (root/'src/veriforge').rglob('*.py')
           if 'native_validator_registration' not in p.relative_to(root).parts}
    require(names==set(expected),'application source inventory differs')
    result={}
    for name,pin in expected.items():
        raw=read_file(root/safe_name(name))
        require(digest(raw)==pin,'application baseline differs: '+name)
        result[name]=raw
    return result

def register(source_root, host_site, wheel_dir, output):
    """Generate a new ignored source candidate; never updates the source checkout."""
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,'use -I -S -B')
    recipe=json.loads(read_file(ASSETS/'recipe.json'))
    require(platform.python_version()==recipe['python'] and sys.platform=='win32' and
        sys.implementation.name=='cpython' and sys.maxsize==2**63-1 and
        platform.machine().lower() in {'amd64','x86_64'},'unqualified target runtime')
    require(Path(sys.executable).resolve()==Path(getattr(sys,'_base_executable',sys.executable)).resolve(),
            'native base interpreter required')
    root=Path(source_root).absolute()
    no_links(root)
    output=Path(output).absolute()
    no_links(output)
    require(output.parent==root/'.runtime/native-validator' and not output.exists(),'new ignored candidate directory required')
    require(digest(read_file(root/'uv.lock'))==recipe['lock_sha256'],'current dependency lock differs')
    sources=source_snapshot(root,recipe['source_files'])
    for name,pin in recipe['overlays'].items():
        raw=read_file(ASSETS/'overlay'/name)
        require(digest(raw)==pin,'reviewed overlay differs')
        sources[name]=raw
    legacy_pins=json.loads(read_file(ASSETS/'legacy-files.json'))
    legacy={name:read_file(ASSETS/'legacy'/safe_name(name)) for name in legacy_pins}
    require(all(digest(raw)==legacy_pins[name] for name,raw in legacy.items()),'legacy firstparty source differs')
    require(legacy_pins['src/veriforge/system_investigation/native_tool_records.py']==recipe['legacy_hash'] and
        digest(sources['src/veriforge/system_investigation/native_tool_records.py'])==recipe['current_hash'],
        'immutable validator identity differs')
    runtime={'python':recipe['python'],'distribution_files':{},'native_interpreter':{
        'path':str(Path(sys.executable).resolve()),'sha256':digest(read_file(sys.executable))}}
    dependencies={}
    provenance={}
    for name,spec in recipe['wheels'].items():
        code,selected,licenses=wheel_contents(Path(wheel_dir)/spec['filename'],dict(spec,name=name))
        runtime[name]=spec['version']
        runtime['distribution_files'][name]={n:digest(raw) for n,raw in code.items()}
        for relative,raw in code.items():
            require(read_file(Path(host_site)/relative)==raw,'active host dependency differs from locked wheel')
        require(not set(selected)&set(dependencies),'dependency file collision')
        dependencies.update(selected)
        provenance[name]=dict(spec,**licenses)
    require(len([n for n in dependencies if n.endswith(('.py','.pyd','.so','.dll')) or n.endswith('.dist-info/METADATA')])==119,
            'reviewed runtime closure count differs')
    legacy_manifest={'files':legacy_pins,'runtime':runtime,'dependency_files':{n:digest(raw) for n,raw in dependencies.items()}}
    active_files={n.removeprefix('src/veriforge/'):digest(raw) for n,raw in sources.items()
                  if n.startswith('src/veriforge/') and not n.startswith(('src/veriforge/api/','src/veriforge/core/'))}
    active_manifest={'files':active_files,'runtime':runtime}
    guard=read_file(ASSETS/'tree_guard.template.py').decode()
    worker=read_file(ASSETS/'worker.template.py').decode().replace('@TREE_GUARD@',guard).replace('@MANIFEST_SHA@',digest(encoded(legacy_manifest)))
    policy=read_file(ASSETS/'policy.template.py').decode().replace('@TREE_GUARD@',guard)
    replacements={'LEGACY_HASH':recipe['legacy_hash'],'CURRENT_HASH':recipe['current_hash'],
        'LEGACY_MANIFEST_SHA':digest(encoded(legacy_manifest)),'LEGACY_WORKER_SHA':digest(worker.encode()),
        'ACTIVE_MANIFEST_SHA':digest(encoded(active_manifest))}
    for name,value in replacements.items(): policy=policy.replace('@'+name+'@',value)
    generated=dict(sources)
    prefix='src/veriforge/core/'
    generated[prefix+'native_validator_policy.py']=policy.encode()
    generated[prefix+'native-validator-dependencies.json']=encoded(active_manifest)
    lp=prefix+'_native_validator_v1/'
    generated.update({lp+n:raw for n,raw in legacy.items()})
    generated.update({lp+'dependencies/'+n:raw for n,raw in dependencies.items()})
    generated[lp+'manifest.json']=encoded(legacy_manifest)
    generated[lp+'worker.py']=worker.encode()
    # Recheck original input vector before creating output; partial output is
    # retained on write failure and is unusable without COMPLETED.json.
    source_snapshot(root,recipe['source_files'])
    output.mkdir(parents=True,exist_ok=False)
    for name,raw in generated.items():
        path=output/safe_name(name)
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
    receipt={'schema':recipe['schema'],'head':recipe['head'],'recipe_sha256':digest(read_file(ASSETS/'recipe.json')),
        'runtime':runtime,'dependency_provenance':provenance,'files':{n:digest(raw) for n,raw in generated.items()},
        'execution':'not_run','operational_acceptance':'pending'}
    require(all(digest(read_file(output/n))==pin for n,pin in receipt['files'].items()),'output bytes differ')
    (output/'COMPLETED.json').write_bytes(encoded(receipt))
    return receipt

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-root','host-site','wheel-dir','output'): parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=register(args.source_root,args.host_site,args.wheel_dir,args.output)
    print(json.dumps({'files':len(result['files']),'execution':'not_run','operational_acceptance':'pending'}))

if __name__=='__main__':
    main()
