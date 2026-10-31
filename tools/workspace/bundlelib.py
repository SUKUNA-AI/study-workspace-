"""bundlelib.py — общая библиотека bundle (контракт: docs/plans/study-workspace/decisions/bundle-contract.md).

Только стандартная библиотека: файл поставляется ученику в `tools/workspace/` и используется экспортёром портала.
Здесь: каноническая форма и digest manifest, проверка путей, glob, регионы, проверка bundle, план и применение
обновления с откатом. Ничего не пишет в рабочий каталог, пока весь план не проверен.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SUPPORTED_SCHEMA = (1,)
UPDATER_VERSION = 2
CONTRACT = "bundle-contract/1"
MANIFEST_PATH = ".course/manifest.json"
SEED_DIR = ".course/seed"
JOURNAL_PATH = ".course/.update-journal.json"
ROLES = ("material", "course_metadata", "tooling", "compatibility")
POLICIES = ("replace", "add_only")
EOLS = ("lf", "crlf", "bin")
VISIBILITIES = ("learner_public", "learner_tooling")
WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_PATH = 240


class BundleError(Exception):
    """Ошибка поставки или обновления. Сообщение — по-русски, с понятным способом исправления."""


# ----------------------------------------------------------------------------- хеши и каноническая форма
def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def to_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def to_crlf(data: bytes) -> bytes:
    return to_lf(data).replace(b"\n", b"\r\n")


def canonical_bytes(data: bytes, eol: str) -> bytes:
    """Байты payload в канонической форме: lf — LF, crlf — CRLF, bin — без изменений."""
    if eol == "lf":
        return to_lf(data)
    if eol == "crlf":
        return to_crlf(data)
    return data


def manifest_for_digest(manifest: dict) -> dict:
    return {k: v for k, v in manifest.items() if k != "generated_at"}


def bundle_digest(manifest: dict) -> str:
    """sha256 от канонического JSON manifest без generated_at (контракт §4)."""
    return sha256_hex(canonical_json(manifest_for_digest(manifest)))


def tooling_revision(files: list[dict]) -> str:
    rows = sorted(([f["path"], f["sha256"], f["bytes"], f["role"]] for f in files if f["role"] == "tooling"),
                  key=lambda r: r[0])
    return sha256_hex(canonical_json(rows))


# ----------------------------------------------------------------------------- пути
def path_error(p) -> str | None:
    """Причина, по которой путь недопустим, либо None."""
    if not isinstance(p, str) or p == "":
        return "пустой путь"
    if len(p) > MAX_PATH:
        return "путь длиннее 240 символов"
    if "\\" in p:
        return "обратный слэш в пути"
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or p.startswith("//"):
        return "абсолютный путь"
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        return "управляющий символ в пути"
    if unicodedata.normalize("NFC", p) != p:
        return "путь не в нормальной форме NFC (неоднозначный Юникод)"
    for seg in p.split("/"):
        if seg == "":
            return "пустой сегмент пути"
        if seg in (".", ".."):
            return "сегмент . или .. в пути (выход за корень)"
        if ":" in seg:
            return "двоеточие в имени (альтернативные потоки NTFS)"
        if seg.endswith((" ", ".")):
            return "имя оканчивается точкой или пробелом (Windows)"
        if seg.split(".")[0].lower() in WIN_RESERVED:
            return f"зарезервированное имя Windows: {seg}"
    return None


def check_path_set(paths: list[str]) -> list[str]:
    """Повторы, коллизии регистра и конфликт файл/каталог в наборе путей."""
    errs: list[str] = []
    seen: dict[str, str] = {}
    for p in paths:
        e = path_error(p)
        if e:
            errs.append(f"{p!r}: {e}")
            continue
        k = unicodedata.normalize("NFC", p).casefold()
        if p in seen and seen[k] == p:
            errs.append(f"{p}: повтор пути")
        elif k in seen:
            errs.append(f"{p}: коллизия имён с {seen[k]} (регистр на Windows)")
        seen.setdefault(k, p)
    dirs = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]).casefold())
    for p in paths:
        if p.casefold() in dirs:
            errs.append(f"{p}: путь одновременно файл и каталог")
    return errs


def _segments_match(pattern: list[str], name: list[str]) -> bool:
    while pattern:
        if pattern[0] == "**":
            if len(pattern) == 1:
                return True
            return any(_segments_match(pattern[1:], name[i:]) for i in range(len(name) + 1))
        if not name or not fnmatch.fnmatchcase(name[0], pattern[0]):
            return False
        pattern, name = pattern[1:], name[1:]
    return not name


def glob_match(pattern: str, path: str) -> bool:
    """`*` внутри сегмента, `**` — любое число сегментов (как в disclosure.yaml)."""
    return _segments_match(pattern.split("/"), path.split("/"))


def match_any(patterns, path: str) -> bool:
    return any(glob_match(p, path) for p in patterns)


def safe_target(root: Path, rel: str) -> Path:
    """Путь внутри root без symlink-компонентов; иначе BundleError."""
    e = path_error(rel)
    if e:
        raise BundleError(f"недопустимый путь {rel!r}: {e}")
    root = Path(os.path.realpath(root))
    cur = root
    for seg in rel.split("/"):
        cur = cur / seg
        if os.path.islink(cur) or (hasattr(os.path, "isjunction") and os.path.isjunction(cur)):
            raise BundleError(f"{rel}: компонент пути — символическая ссылка или junction (выход из workspace запрещён)")
    real = Path(os.path.realpath(cur))
    if real != root and root not in real.parents:
        raise BundleError(f"{rel}: путь выходит за корень workspace")
    return cur


# ----------------------------------------------------------------------------- регионы
_BEGIN = re.compile(r"^<!-- course:begin id=([a-z0-9][a-z0-9._-]*) -->$")
_END = re.compile(r"^<!-- course:end id=([a-z0-9][a-z0-9._-]*) -->$")


@dataclass
class Region:
    id: str
    begin: int  # индекс строки begin
    end: int  # индекс строки end

    def body(self, lines: list[str]) -> bytes:
        return "".join(lines[self.begin + 1:self.end]).encode("utf-8")


def parse_regions(text: str) -> tuple[dict[str, Region], list[str], list[str]]:
    """(регионы, ошибки, строки). Ошибки: сломанные, дублирующиеся, вложенные или перекрёстные маркеры."""
    lines = to_lf_str(text).splitlines(keepends=True)
    errs: list[str] = []
    regions: dict[str, Region] = {}
    open_id: str | None = None
    open_line = -1
    ends_seen: set[str] = set()
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        has_b, has_e = "course:begin" in line, "course:end" in line
        if not (has_b or has_e):
            continue
        mb, me = _BEGIN.match(line), _END.match(line)
        if mb:
            rid = mb.group(1)
            if open_id is not None:
                errs.append(f"строка {i + 1}: маркер begin {rid!r} внутри открытого региона {open_id!r} (вложенность)")
            elif rid in regions or rid in ends_seen:
                errs.append(f"строка {i + 1}: дубль маркера begin {rid!r}")
            else:
                open_id, open_line = rid, i
        elif me:
            rid = me.group(1)
            if open_id is None:
                errs.append(f"строка {i + 1}: маркер end {rid!r} без begin")
            elif rid != open_id:
                errs.append(f"строка {i + 1}: end {rid!r} не закрывает открытый регион {open_id!r} (перекрёстный порядок)")
                open_id = None
            else:
                if rid in regions:
                    errs.append(f"строка {i + 1}: дубль региона {rid!r}")
                regions[rid] = Region(rid, open_line, i)
                ends_seen.add(rid)
                open_id = None
        else:
            errs.append(f"строка {i + 1}: повреждённый маркер региона (ожидается строка целиком вида "
                        "<!-- course:begin id=имя --> или <!-- course:end id=имя -->)")
    if open_id is not None:
        errs.append(f"регион {open_id!r} (строка {open_line + 1}) не закрыт")
    return regions, errs, lines


def to_lf_str(text: str) -> str:
    return text.replace("\r\n", "\n")


def replace_region(text: str, region_id: str, new_body: bytes) -> str:
    regions, errs, lines = parse_regions(text)
    if errs or region_id not in regions:
        raise BundleError(f"регион {region_id!r}: " + ("; ".join(errs) or "не найден"))
    r = regions[region_id]
    new_lines = new_body.decode("utf-8").splitlines(keepends=True)
    return "".join(lines[:r.begin + 1] + new_lines + lines[r.end:])


# ----------------------------------------------------------------------------- manifest
FILE_KEYS = ("path", "sha256", "bytes", "role", "visibility", "update_policy", "source_path", "source_commit", "eol")
SEED_KEYS = ("path", "sha256", "bytes", "source_path", "source_commit")
REGION_KEYS = ("path", "id", "sha256", "bytes", "source_commit")
TOP_KEYS = ("schema_version", "course_id", "source_repository", "source_commit", "content_release", "export_version",
            "tooling_revision", "generated_at", "files", "routes")
ALLOWED_TOP_KEYS = set(TOP_KEYS) | {"contract", "profile", "content_digest", "compatibility", "seed_files",
                                  "student_paths", "managed_regions", "target_repository", "materials_release",
                                  "compatible_previous_releases", "task_schema_version", "material_files"}
ROUTE_KEYS = {"id", "kind", "source_path", "local_path", "local_anchor", "reader_url", "version", "package_kind",
              "std", "solution_file", "signature", "public_fingerprint"}
COMPATIBILITY_KEYS = {"layout", "program_schema", "projection", "min_updater", "helper_path"}
TARGET_KEYS = {"name", "id", "url"}
MATERIAL_PIN_KEYS = {"repository", "repository_id", "url", "commit", "digest", "path"}
MATERIAL_FILE_KEYS = {"path", "sha256", "bytes"}


def load_manifest(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BundleError(f"нет manifest: {path}. Это не workspace курса; выполни bootstrap (экспорт портала)")
    except (OSError, ValueError) as e:
        raise BundleError(f"manifest не читается ({path}): {e}")
    return data


def validate_manifest(m: dict) -> list[str]:
    """Структура, версия, пути, поля. Возвращает список ошибок (пусто — годен)."""
    errs: list[str] = []
    if not isinstance(m, dict):
        return ["manifest не объект JSON"]
    unknown = set(m) - ALLOWED_TOP_KEYS
    if unknown:
        errs.append("неклассифицированные поля manifest: " + ", ".join(sorted(unknown)))
    ver = m.get("schema_version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        errs.append("schema_version отсутствует или не целое")
    elif ver not in SUPPORTED_SCHEMA:
        errs.append(f"несовместимая schema_version {ver} (поддержаны {list(SUPPORTED_SCHEMA)}): обнови tools/workspace/course.py "
                    "из свежего bundle и повтори")
        return errs
    for k in TOP_KEYS:
        if k not in m:
            errs.append(f"нет обязательного поля manifest: {k}")
    if errs:
        return errs
    def declared_object(value, allowed, where):
        if not isinstance(value, dict):
            errs.append(f"{where}: нужен объект JSON")
            return
        extra = set(value) - set(allowed)
        if extra:
            errs.append(f"{where}: неклассифицированные поля: {', '.join(sorted(extra))}")
    for name, allowed in (("files", FILE_KEYS), ("seed_files", SEED_KEYS), ("managed_regions", REGION_KEYS),
                          ("routes", ROUTE_KEYS), ("material_files", MATERIAL_FILE_KEYS)):
        rows = m.get(name, [])
        if not isinstance(rows, list):
            errs.append(f"{name}: нужен список")
            continue
        for index, row in enumerate(rows):
            declared_object(row, allowed, f"{name}[{index}]")
    for name, allowed in (("compatibility", COMPATIBILITY_KEYS), ("target_repository", TARGET_KEYS), ("materials_release", MATERIAL_PIN_KEYS)):
        if name in m:
            declared_object(m[name], allowed, name)
    target = m.get("target_repository")
    if isinstance(target, dict):
        if (target.get("name") != "SUKUNA-AI/study-workspace-" or target.get("url") != "https://github.com/SUKUNA-AI/study-workspace-.git"
            or not isinstance(target.get("id"), int) or isinstance(target.get("id"), bool) or target["id"] != 1438472730):
            errs.append("target_repository: имя, URL и numeric ID не совпадают с разрешённым study-workspace-")
    if "materials_release" in m and target is None:
        errs.append("materials_release: требуется target_repository с проверенной идентичностью")
    if errs:
        return errs
    if m.get("profile") not in ("bootstrap", "update"):
        errs.append("profile должен быть bootstrap или update")
    if not re.fullmatch(r"[0-9a-f]{40}", str(m.get("source_commit"))):
        errs.append("source_commit должен быть полным SHA (40 hex)")
    all_paths: list[str] = []
    for kind, keys, rows in (("files", FILE_KEYS, m["files"]), ("seed_files", SEED_KEYS, m.get("seed_files", []))):
        if not isinstance(rows, list):
            errs.append(f"{kind} не список")
            continue
        last = None
        for row in rows:
            if not isinstance(row, dict):
                errs.append(f"{kind}: элемент не объект")
                continue
            for k in keys:
                if k not in row:
                    errs.append(f"{kind}[{row.get('path')}]: нет поля {k}")
            p = row.get("path")
            if last is not None and isinstance(p, str) and p < last:
                errs.append(f"{kind}: список не упорядочен по path ({p} после {last})")
            last = p if isinstance(p, str) else last
            if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256"))):
                errs.append(f"{kind}[{p}]: sha256 некорректен")
            if not isinstance(row.get("bytes"), int) or isinstance(row.get("bytes"), bool):
                errs.append(f"{kind}[{p}]: bytes не целое")
            if kind == "files":
                if row.get("role") not in ROLES:
                    errs.append(f"files[{p}]: неизвестная role {row.get('role')!r}")
                if row.get("visibility") not in VISIBILITIES:
                    errs.append(f"files[{p}]: неизвестная visibility {row.get('visibility')!r}")
                if row.get("update_policy") not in POLICIES:
                    errs.append(f"files[{p}]: неизвестная update_policy {row.get('update_policy')!r}")
                if row.get("eol") not in EOLS:
                    errs.append(f"files[{p}]: неизвестный eol {row.get('eol')!r}")
            all_paths.append(p)
    errs += check_path_set([p for p in all_paths if isinstance(p, str)])
    sp = m.get("student_paths", [])
    if not isinstance(sp, list) or not all(isinstance(x, str) and x for x in sp):
        errs.append("student_paths должен быть списком непустых масок")
        sp = []
    for f in m["files"]:
        if isinstance(f, dict) and isinstance(f.get("path"), str) and match_any(sp, f["path"]):
            errs.append(f"files[{f['path']}]: путь входит в student_paths — управляемый файл не может лежать в области ученика")
    ids = set()
    for r in m.get("managed_regions", []):
        if not isinstance(r, dict) or any(k not in r for k in REGION_KEYS):
            errs.append("managed_regions: неполная запись")
            continue
        err = path_error(r["path"])
        if err:
            errs.append(f"managed_regions[{r['path']}]: {err}")
        key = (r["path"], r["id"])
        if key in ids:
            errs.append(f"managed_regions: повтор региона {key}")
        ids.add(key)
    return errs


def require_manifest(m: dict, what: str = "manifest") -> None:
    errs = validate_manifest(m)
    if errs:
        raise BundleError(f"{what}: " + "; ".join(errs[:8]) + (f" (и ещё {len(errs) - 8})" if len(errs) > 8 else ""))


def physical_path(m: dict, rel: str, kind: str) -> str:
    """Где лежит файл в каталоге поставки. kind: file | seed (носитель seed/регионов)."""
    if kind == "seed" and m.get("profile") == "update":
        return f"{SEED_DIR}/{rel}"
    return rel


# ----------------------------------------------------------------------------- проверка bundle
@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_checked(root: Path, rel: str) -> bytes | None:
    p = root / rel
    if os.path.islink(p):
        raise BundleError(f"{rel}: символическая ссылка в поставке")
    if not p.is_file():
        return None
    return p.read_bytes()


def file_state(data: bytes, entry: dict) -> str:
    """ok | ok_eol (отличаются только концы строк) | modified."""
    canon = canonical_bytes(data, entry["eol"]) if "eol" in entry else data
    if "eol" not in entry or entry["eol"] == "bin":
        return "ok" if sha256_hex(data) == entry["sha256"] else "modified"
    if sha256_hex(data) == entry["sha256"]:
        return "ok"
    return "ok_eol" if sha256_hex(canon) == entry["sha256"] else "modified"


def verify_bundle_dir(root: Path, m: dict, *, strict: bool = True) -> Check:
    """Целостность каталога поставки: все файлы на месте, размеры и sha256, регионы, отсутствие лишнего и symlink."""
    c = Check()
    root = Path(root)
    listed: set[str] = {MANIFEST_PATH}
    try:
        for f in m["files"]:
            rel = f["path"]
            listed.add(rel)
            data = _read_checked(root, rel)
            if data is None:
                c.errors.append(f"{rel}: файл из manifest отсутствует в поставке")
                continue
            st = file_state(data, f)
            if st == "modified":
                c.errors.append(f"{rel}: sha256 не совпадает с manifest (файл повреждён или изменён)")
            else:
                if st == "ok_eol":
                    c.warnings.append(f"{rel}: line-endings: концы строк отличаются от LF (хеш сходится после нормализации)")
                if len(canonical_bytes(data, f["eol"])) != f["bytes"]:
                    c.errors.append(f"{rel}: размер не совпадает с manifest")
        for s in m.get("seed_files", []):
            rel = physical_path(m, s["path"], "seed")
            listed.add(rel)
            data = _read_checked(root, rel)
            if data is None:
                c.errors.append(f"{rel}: заготовка из seed_files отсутствует в поставке")
                continue
            ent = {"sha256": s["sha256"], "eol": "bin" if _looks_binary(data) else "lf"}
            if file_state(data, ent) == "modified":
                c.errors.append(f"{rel}: sha256 заготовки не совпадает с manifest")
        by_file: dict[str, list[dict]] = {}
        for r in m.get("managed_regions", []):
            by_file.setdefault(r["path"], []).append(r)
        for path, regs in by_file.items():
            rel = physical_path(m, path, "seed")
            data = _read_checked(root, rel)
            if data is None:
                c.errors.append(f"{rel}: файл с managed regions отсутствует в поставке")
                continue
            parsed, errs, lines = parse_regions(data.decode("utf-8", "replace"))
            for e in errs:
                c.errors.append(f"{rel}: {e}")
            for r in regs:
                reg = parsed.get(r["id"])
                if reg is None:
                    c.errors.append(f"{rel}: регион {r['id']!r} не найден по маркерам")
                    continue
                body = reg.body(lines)
                if sha256_hex(body) != r["sha256"] or len(body) != r["bytes"]:
                    c.errors.append(f"{rel}: регион {r['id']!r} не совпадает с manifest")
        if strict:
            for dirpath, dirnames, filenames in os.walk(root):
                for n in list(dirnames) + filenames:
                    full = Path(dirpath) / n
                    if os.path.islink(full):
                        c.errors.append(f"{full.relative_to(root).as_posix()}: символическая ссылка в поставке")
                for n in filenames:
                    rel = (Path(dirpath) / n).relative_to(root).as_posix()
                    if rel not in listed and not rel.startswith(".course/updates/"):
                        c.errors.append(f"{rel}: файл есть в поставке, но нет в manifest")
    except BundleError as e:
        c.errors.append(str(e))
    return c


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


# ----------------------------------------------------------------------------- план обновления
@dataclass
class Op:
    kind: str  # add | update | remove | region
    path: str
    new: bytes | None = None
    old_sha: str | None = None
    detail: str = ""


@dataclass
class Plan:
    ops: list[Op] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seed_patches: dict[str, str] = field(default_factory=dict)
    kept_orphans: list[str] = field(default_factory=list)
    compat: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.conflicts or self.errors)


def _decode(data: bytes) -> str:
    return data.decode("utf-8")


def plan_update(ws: Path, bundle: Path, new: dict, old: dict, *, expect_digest: str | None = None,
                expect_commit: str | None = None) -> Plan:
    """Полная проверка до записи. Ничего не пишет."""
    import difflib

    plan = Plan()
    ws, bundle = Path(ws), Path(bundle)
    errs = validate_manifest(new)
    if errs:
        plan.errors += [f"новый manifest: {e}" for e in errs]
        return plan
    plan.errors += [f"установленный manifest: {e}" for e in validate_manifest(old)]
    if plan.errors:
        return plan
    if new.get("course_id") != old.get("course_id"):
        plan.errors.append(f"course_id не совпадает: установлен {old.get('course_id')!r}, поставка {new.get('course_id')!r}")
    if new.get("source_repository") != old.get("source_repository"):
        plan.errors.append("source_repository не совпадает с установленным (чужая поставка)")
    digest = bundle_digest(new)
    if expect_digest and digest != expect_digest:
        plan.errors.append(f"digest поставки {digest} не равен ожидаемому {expect_digest}")
    if expect_commit and new["source_commit"] != expect_commit:
        plan.errors.append(f"source_commit поставки {new['source_commit']} не равен ожидаемому {expect_commit}")
    if new.get("profile") != "update":
        plan.errors.append("поставка для обновления должна иметь profile=update (bootstrap повторно ввёл бы заготовки)")
    mv = new.get("compatibility", {}).get("min_updater", 1)
    if isinstance(mv, int) and mv > UPDATER_VERSION:
        plan.errors.append(f"поставке нужен updater версии {mv}, установлен {UPDATER_VERSION}: обнови tools/workspace из поставки вручную")
    chk = verify_bundle_dir(bundle, new, strict=True)
    plan.errors += chk.errors
    plan.warnings += chk.warnings
    if plan.errors:
        return plan

    old_files = {f["path"]: f for f in old["files"]}
    new_files = {f["path"]: f for f in new["files"]}
    protected = list(dict.fromkeys(list(old.get("student_paths", [])) + list(new.get("student_paths", []))))
    for p in new_files:
        if match_any(protected, p):
            plan.errors.append(f"{p}: управляемый файл лежит в области ученика (student_paths) — поставка отклонена")
    if plan.errors:
        return plan

    for p, nf in new_files.items():
        try:
            tgt = safe_target(ws, p)
        except BundleError as e:
            plan.errors.append(str(e))
            continue
        payload = canonical_bytes((bundle / p).read_bytes(), nf["eol"])
        if not tgt.exists():
            plan.ops.append(Op("add", p, payload, None))
            continue
        if tgt.is_dir():
            plan.conflicts.append(f"{p}: на месте файла каталог")
            continue
        local = tgt.read_bytes()
        state_new = file_state(local, nf)
        if state_new != "modified":
            plan.unchanged.append(p)
            continue
        of = old_files.get(p)
        if of is None:
            plan.conflicts.append(f"{p}: путь занят твоим файлом, которого не было в курсе (path_taken_by_user_file); "
                                  "перенеси или переименуй его и повтори")
            continue
        if nf["update_policy"] == "add_only":
            plan.unchanged.append(p)
            continue
        if file_state(local, of) == "modified":
            plan.conflicts.append(f"{p}: управляемый файл изменён локально (locally_modified); верни его из Git "
                                  "(git checkout -- путь) или сохрани копию и повтори")
            continue
        plan.ops.append(Op("update", p, payload, sha256_hex(local)))

    for p, of in old_files.items():
        if p in new_files:
            continue
        if match_any(protected, p):
            plan.kept_orphans.append(p)
            continue
        try:
            tgt = safe_target(ws, p)
        except BundleError as e:
            plan.errors.append(str(e))
            continue
        if not tgt.exists():
            continue
        if file_state(tgt.read_bytes(), of) == "modified":
            plan.kept_orphans.append(p)
            plan.warnings.append(f"{p}: удалён из курса, но изменён локально — оставлен как есть")
        else:
            plan.ops.append(Op("remove", p, None, sha256_hex(tgt.read_bytes())))

    # регионы
    old_regions = {(r["path"], r["id"]): r for r in old.get("managed_regions", [])}
    by_file: dict[str, list[dict]] = {}
    for r in new.get("managed_regions", []):
        by_file.setdefault(r["path"], []).append(r)
    for path, regs in by_file.items():
        carrier = _decode(canonical_bytes((bundle / physical_path(new, path, "seed")).read_bytes(), "lf"))
        nparsed, _, nlines = parse_regions(carrier)
        try:
            tgt = safe_target(ws, path)
        except BundleError as e:
            plan.errors.append(str(e))
            continue
        if not tgt.is_file():
            plan.errors.append(f"{path}: нет локального файла с managed regions (восстанови его из Git или заготовки и повтори)")
            continue
        raw = tgt.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            plan.errors.append(f"{path}: файл не в UTF-8")
            continue
        lparsed, lerrs, llines = parse_regions(text)
        if lerrs:
            plan.errors += [f"{path}: {e}" for e in lerrs]
            continue
        current_text = to_lf_str(text)
        changed = False
        for r in regs:
            reg = lparsed.get(r["id"])
            if reg is None:
                plan.errors.append(f"{path}: маркеры региона {r['id']!r} отсутствуют (удалены или не добавлены) — обновление остановлено")
                continue
            local_body = reg.body(llines)
            if sha256_hex(local_body) == r["sha256"]:
                plan.unchanged.append(f"{path}#{r['id']}")
                continue
            orr = old_regions.get((path, r["id"]))
            if orr is None or sha256_hex(local_body) != orr["sha256"]:
                plan.conflicts.append(f"{path}#{r['id']}: управляемый блок изменён локально (region_modified); "
                                      "верни блок из Git или перенеси правку за маркеры")
                continue
            new_body = nparsed[r["id"]].body(nlines)
            current_text = replace_region(current_text, r["id"], new_body)
            changed = True
        if changed:
            crlf = b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
            out = current_text.replace("\n", "\r\n") if crlf else current_text
            plan.ops.append(Op("region", path, out.encode("utf-8"), sha256_hex(raw), detail=",".join(r["id"] for r in regs)))

    # seed-патчи (не применяются)
    old_seed = {s["path"]: s for s in old.get("seed_files", [])}
    for s in new.get("seed_files", []):
        os_ = old_seed.get(s["path"])
        if os_ is None or os_["sha256"] == s["sha256"]:
            continue
        sp = bundle / physical_path(new, s["path"], "seed")
        try:
            tgt = safe_target(ws, s["path"])
        except BundleError:
            continue
        if not tgt.is_file():
            continue
        try:
            a = _decode(tgt.read_bytes()).splitlines(keepends=True)
            b = _decode(sp.read_bytes()).splitlines(keepends=True)
        except UnicodeDecodeError:
            continue
        diff = "".join(difflib.unified_diff(a, b, f"a/{s['path']} (у тебя)", f"b/{s['path']} (новая заготовка)"))
        if diff:
            plan.seed_patches[s["path"]] = diff
    plan.compat = {k: {"было": old.get(k), "стало": new.get(k)} for k in
                   ("content_release", "export_version", "tooling_revision", "compatibility", "source_commit")}
    plan.ops.sort(key=lambda o: ({"add": 0, "update": 1, "region": 2, "remove": 3}[o.kind], o.path))
    return plan


# ----------------------------------------------------------------------------- применение с откатом
class InjectedFault(RuntimeError):
    pass


def _fsync_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


def apply_plan(ws: Path, plan: Plan, new_manifest_bytes: bytes, new: dict, *, fault_at: int | None = None, hard_crash: bool = False) -> dict:
    """Применяет проверенный план. `fault_at=N` — имитация обрыва после N-й операции (для тестов)."""
    if plan.blocked:
        raise BundleError("план заблокирован конфликтами или ошибками — запись не выполняется")
    ws = Path(ws)
    tag = bundle_digest(new)[:12]
    staging = ws / ".course" / f".staging-{tag}"
    backup = staging / "backup"
    journal = ws / JOURNAL_PATH
    if journal.exists():
        raise BundleError("найден журнал прерванного обновления: выполни `course.py recover`")
    # Compare actual bytes captured by planning, including EOL, before the first mutation.
    for op in plan.ops:
        target = safe_target(ws, op.path)
        if op.old_sha is None:
            if target.exists():
                raise BundleError(f"{op.path}: появился файл после предварительной проверки; повтори план")
        elif not target.is_file() or sha256_hex(target.read_bytes()) != op.old_sha:
            raise BundleError(f"{op.path}: файл изменился после предварительной проверки; работа сохранена")
    if staging.exists():
        shutil.rmtree(staging)
    # 1) подготовка: все новые байты в staging и повторная проверка хешей
    for i, op in enumerate(plan.ops):
        if op.new is not None:
            _fsync_write(staging / "new" / f"{i}", op.new)
            if sha256_hex((staging / "new" / f"{i}").read_bytes()) != sha256_hex(op.new):
                shutil.rmtree(staging, ignore_errors=True)
                raise BundleError(f"{op.path}: ошибка записи staging — рабочие файлы не тронуты")
    _fsync_write(staging / "new" / "manifest", new_manifest_bytes)
    ops = [{"i": i, "kind": o.kind, "path": o.path, "had": None, "done": False} for i, o in enumerate(plan.ops)]
    _fsync_write(journal, json.dumps({"state": "committing", "digest": tag, "ops": ops}, ensure_ascii=False,
                                     indent=1).encode("utf-8"))
    done: list[int] = []
    try:
        for i, op in enumerate(plan.ops):
            if fault_at is not None and i == fault_at:
                if hard_crash:
                    os._exit(99)  # имитация обрыва процесса: журнал и staging остаются на диске
                raise InjectedFault(f"обрыв перед операцией {i}")
            tgt = safe_target(ws, op.path)
            if tgt.exists():
                _fsync_write(backup / str(i), tgt.read_bytes())
                ops[i]["had"] = True
            else:
                ops[i]["had"] = False
            _fsync_write(journal, json.dumps({"state": "committing", "digest": tag, "ops": ops}, ensure_ascii=False,
                                             indent=1).encode("utf-8"))
            if op.kind == "remove":
                tgt.unlink()
            else:
                tgt.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / "new" / f"{i}", tgt)
            ops[i]["done"] = True
            done.append(i)
        if fault_at is not None and fault_at >= len(plan.ops):
            raise InjectedFault("обрыв перед записью manifest")
        mpath = safe_target(ws, MANIFEST_PATH)
        _fsync_write(backup / "manifest", mpath.read_bytes() if mpath.exists() else b"")
        os.replace(staging / "new" / "manifest", mpath)
    except BaseException:
        _rollback(ws, staging, ops, plan)
        raise
    _cleanup(ws, staging)
    return {"applied": len(plan.ops)}


def _rollback(ws: Path, staging: Path, ops: list[dict], plan_or_none) -> None:
    backup = staging / "backup"
    for o in reversed(ops):
        tgt = ws / o["path"]
        if o.get("had") is None:
            continue  # до этой операции дело не дошло
        if o["had"]:
            b = backup / str(o["i"])
            if b.exists():
                tgt.parent.mkdir(parents=True, exist_ok=True)
                os.replace(b, tgt)
        elif tgt.exists():
            tgt.unlink()
            _prune_dirs(ws, tgt.parent)
    _cleanup(ws, staging)


def _prune_dirs(ws: Path, d: Path) -> None:
    ws = Path(os.path.realpath(ws))
    d = Path(os.path.realpath(d))
    while d != ws and ws in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def _cleanup(ws: Path, staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)
    j = ws / JOURNAL_PATH
    if j.exists():
        j.unlink()


def recover(ws: Path) -> str:
    """Возвращает состояние до обновления по журналу, если обновление оборвалось."""
    ws = Path(ws)
    j = ws / JOURNAL_PATH
    if not j.exists():
        for s in (ws / ".course").glob(".staging-*") if (ws / ".course").exists() else []:
            shutil.rmtree(s, ignore_errors=True)
        return "журнала нет — восстанавливать нечего"
    data = json.loads(j.read_text(encoding="utf-8"))
    staging = ws / ".course" / f".staging-{data['digest']}"
    ops = data["ops"]
    mb = staging / "backup" / "manifest"
    # manifest мог быть уже заменён: вернуть из резервной копии, если она есть
    if mb.exists():
        mp = ws / MANIFEST_PATH
        if mb.read_bytes():
            os.replace(mb, mp)
    _rollback(ws, staging, ops, None)
    return "обновление отменено: восстановлено состояние до него"
