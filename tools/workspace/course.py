#!/usr/bin/env python3
"""course.py — проверка и обновление курса в workspace ученика. Только стандартная библиотека.

  python3 tools/workspace/course.py verify                      # целостность установленного комплекта
  python3 tools/workspace/course.py verify-bundle DIR [--expect-digest H]   # проверка поставки до установки
  python3 tools/workspace/course.py update --bundle DIR [--expect-digest H] [--expect-commit SHA] [--dry-run]
  python3 tools/workspace/course.py recover                     # вернуть состояние после оборванного обновления
  python3 tools/workspace/course.py status [--portal-release FILE]
  python3 tools/workspace/course.py check-program               # проверка .course/program.json для helper

Контракт: docs/plans/study-workspace/decisions/bundle-contract.md (в репозитории study-portal).
Обновление меняет только управляемые файлы и регионы по manifest, не трогает твои работы и при любой
ошибке или конфликте не меняет ни одного файла. Коды выхода: 0 — готово, 1 — ошибка, 2 — конфликты (запись не выполнялась).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundlelib as B  # noqa: E402

KNOWN_PROGRAM_SCHEMAS = ("1.5",)
KNOWN_PROJECTIONS = (1,)


def default_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- program.json
def check_program(root: Path, program_path: Path | None = None) -> tuple[dict | None, list[str]]:
    """Проверка компактной программы. Возвращает (данные, проблемы); проблемы называют недостающий ID и способ исправления."""
    fix = "обнови курс: python3 tools/workspace/course.py update --bundle <папка новой поставки> (или повтори bootstrap)"
    p = Path(program_path) if program_path else Path(root) / ".course" / "program.json"
    if not p.is_file():
        return None, [f"нет файла {p.name} ({p}): {fix}"]
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        return None, [f"{p.name} не читается как JSON: {e}. {fix}"]
    probs: list[str] = []
    proj = d.get("projection") or {}
    if proj.get("kind") != "learner-control":
        probs.append("нет блока projection (это не компактная программа курса): " + fix)
    elif proj.get("version") not in KNOWN_PROJECTIONS:
        probs.append(f"неизвестная версия проекции {proj.get('version')!r} (поддержаны {list(KNOWN_PROJECTIONS)}): "
                     "обнови tools/workspace/ и tools/tutor_control.py из свежей поставки")
    if d.get("schema_version") not in KNOWN_PROGRAM_SCHEMAS:
        probs.append(f"неизвестная schema_version программы {d.get('schema_version')!r} "
                     f"(поддержаны {list(KNOWN_PROGRAM_SCHEMAS)}): обнови tools/ из свежей поставки")
    if probs:
        return None, probs
    dates = (d.get("program") or {}).get("dates") or {}
    if not dates.get("study_start"):
        probs.append("нет program.dates.study_start: " + fix)
    units = d.get("units")
    if not isinstance(units, list) or not units:
        return None, probs + ["нет units[]: " + fix]
    want = dates.get("weeks")
    ids = [u.get("id") for u in units]
    if isinstance(want, int):
        for n in range(1, want + 1):
            if f"U{n:02d}" not in ids:
                probs.append(f"нет недели U{n:02d} в program.json: {fix}")
    for u in units:
        uid = u.get("id", "?")
        for k in ("start", "end", "mode", "steps", "tasks"):
            if k not in u:
                probs.append(f"{uid}: нет поля {k}: {fix}")
        if "kind" not in (u.get("quiz") or {}):
            probs.append(f"{uid}: нет quiz.kind: {fix}")
        for t in u.get("tasks", []):
            if t.get("kind") == "exam" and not t.get("output"):
                probs.append(f"{uid}/{t.get('id')}: у задачи экзамена нет output (путь протокола): {fix}")
    if "exams" not in d:
        probs.append("нет блока exams (правила контрольных, зачётов, сессий): " + fix)
    return (d if not probs else None), probs


# ----------------------------------------------------------------------------- команды
def cmd_verify(a) -> int:
    root = Path(a.root)
    m = B.load_manifest(root / B.MANIFEST_PATH)
    B.require_manifest(m)
    problems, warns, ok = [], [], 0
    for f in m["files"]:
        try:
            p = B.safe_target(root, f["path"])
        except B.BundleError as e:
            problems.append(str(e))
            continue
        if not p.is_file():
            problems.append(f"{f['path']}: управляемый файл отсутствует")
            continue
        st = B.file_state(p.read_bytes(), f)
        if st == "modified":
            warns.append(f"{f['path']}: изменён локально (обновление покажет конфликт)")
        elif st == "ok_eol":
            warns.append(f"{f['path']}: line-endings отличаются от LF; хеш сходится после нормализации")
            ok += 1
        else:
            ok += 1
    by_file: dict[str, list[dict]] = {}
    for r in m.get("managed_regions", []):
        by_file.setdefault(r["path"], []).append(r)
    for path, regs in by_file.items():
        p = root / path
        if not p.is_file():
            problems.append(f"{path}: файл с managed regions отсутствует")
            continue
        parsed, errs, lines = B.parse_regions(p.read_text(encoding="utf-8"))
        problems += [f"{path}: {e}" for e in errs]
        for r in regs:
            reg = parsed.get(r["id"])
            if reg is None:
                if not errs:
                    problems.append(f"{path}: нет маркеров региона {r['id']!r}")
            elif B.sha256_hex(reg.body(lines)) != r["sha256"]:
                warns.append(f"{path}#{r['id']}: блок изменён локально")
    print(f"digest поставки: {B.bundle_digest(m)}")
    print(f"content_release: {m['content_release']} · export_version: {m['export_version']} · source: {m['source_commit'][:12]}")
    print(f"проверено управляемых файлов: {len(m['files'])}, совпало: {ok}")
    for w in warns:
        print("  предупреждение:", w)
    for pr in problems:
        print("  ОШИБКА:", pr)
    if problems or (a.strict and warns):
        return 1
    return 0


def cmd_verify_bundle(a) -> int:
    root = Path(a.dir)
    m = B.load_manifest(root / B.MANIFEST_PATH)
    errs = B.validate_manifest(m)
    chk = B.verify_bundle_dir(root, m) if not errs else B.Check(errors=errs)
    d = B.bundle_digest(m) if not errs else None
    if a.expect_digest and d != a.expect_digest:
        chk.errors.append(f"digest {d} не равен ожидаемому {a.expect_digest}")
    print(f"digest: {d}")
    for w in chk.warnings:
        print("  предупреждение:", w)
    for e in chk.errors:
        print("  ОШИБКА:", e)
    return 0 if chk.ok else 1


def render_report(plan: B.Plan, new: dict, applied: bool) -> str:
    out = [f"# Обновление курса: {new['content_release']}", "",
           f"Статус: {'применено' if applied else 'план (запись не выполнялась)'}",
           f"digest поставки: `{B.bundle_digest(new)}`", ""]
    for kind, title in (("add", "Новые"), ("update", "Обновлены"), ("region", "Обновлены блоки"), ("remove", "Удалены")):
        items = [o for o in plan.ops if o.kind == kind]
        if items:
            out.append(f"## {title} ({len(items)})")
            out += [f"- `{o.path}`" + (f" ({o.detail})" if o.detail else "") for o in items]
            out.append("")
    if plan.kept_orphans:
        out += ["## Убраны из курса, оставлены у тебя", *[f"- `{p}`" for p in plan.kept_orphans], ""]
    if plan.conflicts:
        out += ["## Конфликты (запись остановлена)", *[f"- {c}" for c in plan.conflicts], ""]
    if plan.errors:
        out += ["## Ошибки", *[f"- {e}" for e in plan.errors], ""]
    if plan.warnings:
        out += ["## Предупреждения", *[f"- {w}" for w in plan.warnings], ""]
    if plan.seed_patches:
        out += ["## Исправления заготовок (не применены, патчи — рядом с отчётом)", *[f"- `{p}`" for p in plan.seed_patches], ""]
    if plan.compat:
        out += ["## Версии", "| поле | было | стало |", "|---|---|---|"]
        for k, v in plan.compat.items():
            out.append(f"| {k} | `{json.dumps(v['было'], ensure_ascii=False)}` | `{json.dumps(v['стало'], ensure_ascii=False)}` |")
        out.append("")
    out.append(f"Без изменений: {len(plan.unchanged)} файлов/блоков.")
    return "\n".join(out) + "\n"


def cmd_update(a) -> int:
    root = Path(a.root)
    bundle = Path(a.bundle)
    if (root / B.JOURNAL_PATH).exists():
        print("ОШИБКА: найден журнал прерванного обновления. Выполни: python3 tools/workspace/course.py recover")
        return 1
    try:
        old = B.load_manifest(root / B.MANIFEST_PATH)
        new = B.load_manifest(bundle / B.MANIFEST_PATH)
        manifest_bytes = (bundle / B.MANIFEST_PATH).read_bytes()
    except B.BundleError as e:
        print("ОШИБКА:", e)
        return 1
    if old.get("materials_release") or new.get("materials_release"):
        print("Обновление pinned материалов: используй python tools/study.py update --bundle <поставка> --expect-digest <проверенный digest>")
        return 1
    plan = B.plan_update(root, bundle, new, old, expect_digest=a.expect_digest, expect_commit=a.expect_commit)
    report = render_report(plan, new, applied=False)
    if plan.blocked:
        print(report)
        print("Ни один файл не изменён.")
        if a.report:
            Path(a.report).write_text(report, encoding="utf-8")
        return 2 if (plan.conflicts and not plan.errors) else 1
    if a.dry_run:
        print(report)
        if a.report:
            Path(a.report).write_text(report, encoding="utf-8")
        return 0
    fault = os.environ.get("COURSE_UPDATE_FAULT_AT")
    hard = os.environ.get("COURSE_UPDATE_HARD_CRASH") == "1"
    try:
        B.apply_plan(root, plan, manifest_bytes, new, fault_at=int(fault) if fault else None, hard_crash=hard)
    except B.InjectedFault as e:
        print("ОШИБКА (тестовый обрыв):", e)
        return 1
    except (B.BundleError, OSError) as e:
        print("ОШИБКА при применении, прежнее состояние восстановлено:", e)
        return 1
    tag = B.bundle_digest(new)[:12]
    udir = root / ".course" / "updates" / tag
    udir.mkdir(parents=True, exist_ok=True)
    report = render_report(plan, new, applied=True)
    (udir / "report.md").write_text(report, encoding="utf-8")
    for path, diff in plan.seed_patches.items():
        dst = udir / "seed-patches" / (path.replace("/", "__") + ".diff")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(diff, encoding="utf-8")
    if a.report:
        Path(a.report).write_text(report, encoding="utf-8")
    print(report)
    print(f"Отчёт: .course/updates/{tag}/report.md")
    return 0


def cmd_recover(a) -> int:
    print(B.recover(Path(a.root)))
    return 0


def cmd_status(a) -> int:
    root = Path(a.root)
    m = B.load_manifest(root / B.MANIFEST_PATH)
    B.require_manifest(m)
    print(f"установлен: content_release {m['content_release']} · export {m['export_version']} · digest {B.bundle_digest(m)[:16]}")
    if a.portal_release:
        try:
            pr = json.loads(Path(a.portal_release).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print("ОШИБКА: файл выпуска портала не читается:", e)
            return 1
        if pr.get("content_release") != m["content_release"]:
            print(f"РАСХОЖДЕНИЕ: портал на {pr.get('content_release')}, твой workspace на {m['content_release']}. "
                  "Начатые задания остаются на твоей версии; обновление — course.py update --bundle <поставка> "
                  f"--expect-digest {pr.get('bundle_digest', '<digest из release.json>')}")
            return 3
        print("версии совпадают")
    return 0


def cmd_check_program(a) -> int:
    d, probs = check_program(Path(a.root), Path(a.program) if a.program else None)
    if probs:
        for p in probs:
            print("ОШИБКА:", p)
        return 1
    print(f"program.json: {len(d['units'])} недель, проекция v{d['projection']['version']}, schema {d['schema_version']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(default_root()), help="корень workspace (по умолчанию — выше tools/workspace)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--strict", action="store_true", help="локальные изменения тоже считать ошибкой")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("verify-bundle")
    p.add_argument("dir")
    p.add_argument("--expect-digest")
    p.set_defaults(fn=cmd_verify_bundle)
    p = sub.add_parser("update")
    p.add_argument("--bundle", required=True)
    p.add_argument("--expect-digest")
    p.add_argument("--expect-commit")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report")
    p.set_defaults(fn=cmd_update)
    p = sub.add_parser("recover")
    p.set_defaults(fn=cmd_recover)
    p = sub.add_parser("status")
    p.add_argument("--portal-release")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("check-program")
    p.add_argument("--program")
    p.set_defaults(fn=cmd_check_program)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except B.BundleError as e:
        print("ОШИБКА:", e)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
