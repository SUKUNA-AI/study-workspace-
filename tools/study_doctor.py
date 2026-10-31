#!/usr/bin/env python3
"""Capability probes; no installs, project builds, OS changes or GPU jobs."""
from __future__ import annotations
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25)
        return p.returncode, (p.stdout + p.stderr).strip()[:1800]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def probe(profile):
    windows = platform.system() == 'Windows'
    expected_windows = profile.startswith('windows')
    native = windows == expected_windows
    results = []
    def row(name, state, detail='', required=False):
        results.append(dict(capability=name, state=state, detail=detail, required=required))
    row('python', 'available' if sys.version_info >= (3, 10) else 'unsupported', sys.version.split()[0], True)
    for name in ('git', 'cmake', 'judge', 'codex', 'nvcc', 'nvidia-smi'):
        found = shutil.which(name)
        if name == 'judge' and not found:
            local = Path(__file__).resolve().parent.parent / '.study/bin' / ('judge.exe' if windows else 'judge')
            found = str(local) if local.is_file() else None
        required = name == 'git' or ('cuda' in profile and name in ('nvcc', 'nvidia-smi'))
        if not native:
            row(name, 'unsupported', 'Профиль другой ОС; запусти doctor в соответствующей среде.', required)
        elif not found:
            row(name, 'missing', 'judge: install-judge с проверенным release manifest' if name == 'judge' else 'Не найден в PATH', required)
        else:
            args = ['version'] if name == 'judge' else ['--query-gpu=name,driver_version', '--format=csv,noheader'] if name == 'nvidia-smi' else ['--version']
            code, output = run([found, *args])
            row(name, 'available' if code == 0 else 'unsupported', output, required)
    compiler = next((shutil.which(n) for n in ('cl', 'clang++', 'g++') if shutil.which(n)), None)
    row('cxx', 'available' if compiler and native else 'missing' if native else 'unsupported', compiler or 'Открой developer shell MSVC либо установи заявленный compiler', True)
    if compiler and native:
        with tempfile.TemporaryDirectory(prefix='study-capabilities-') as temp:
            wd = Path(temp)
            msvc = Path(compiler).stem.lower() == 'cl'
            cases = {'cxx23_flag': '#include <version>\nstatic_assert(__cplusplus >= 202100L); int main() {}',
                     'library_expected': '#include <expected>\nint main(){ std::expected<int,int> x(1); return *x-1; }',
                     'import_std': 'import std;\nint main(){return 0;}',
                     'sanitizer': 'int main(){return 0;}'}
            for name, source in cases.items():
                file = wd / 'probe.cpp'
                file.write_text(source, encoding='utf-8')
                args = [compiler, '/nologo', '/std:c++latest', '/Zc:__cplusplus', str(file), '/Fe:' + str(wd/'probe.exe')] if msvc else [compiler, '-std=c++23', str(file), '-o', str(wd/'probe')]
                if name == 'sanitizer':
                    args += ['/fsanitize=address'] if msvc else ['-fsanitize=address,undefined']
                code, output = run(args, wd)
                row(name, 'available' if code == 0 else 'unsupported', output or 'Compiler probe succeeded; not a claim of full C++23 support', name == 'cxx23_flag')
    else:
        for name in ('cxx23_flag', 'library_expected', 'import_std', 'sanitizer'):
            row(name, 'not_checked', 'Сначала нужен compiler', name == 'cxx23_flag')
    row('posix_semantics', 'unsupported' if windows else 'available', 'Windows CPU не подтверждает POSIX-семантику')
    row('gpu_runtime', 'not_checked', 'Компиляция CUDA, запуск и sanitizer проверяются отдельной командой; CPU не подтверждает GPU')
    ready = not any(r['required'] and r['state'] != 'available' for r in results)
    return dict(schema_version=1, profile=profile, platform=platform.platform(), ready=ready, results=results,
                cpu_available=all(r['state']=='available' for r in results if r['capability'] in ('python','git','cxx','cxx23_flag')))


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', choices=['linux-cpu','windows-cpu','linux-cuda','windows-cuda'], default='windows-cpu' if os.name=='nt' else 'linux-cpu')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    report = probe(a.profile)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('Можно выполнять CPU сейчас: ' + ('да' if report['cpu_available'] else 'смотри обязательные возможности ниже'))
        for r in report['results']:
            print(f"{r['capability']}: {r['state']} — {r['detail']}")
        print('GPU-проверка выполняется отдельно. Ничего не установлено автоматически.')
    return 0 if report['ready'] else 1

if __name__ == '__main__':
    sys.exit(main())
