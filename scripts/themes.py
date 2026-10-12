#!/usr/bin/env python3
"""The themes half of the registry's site.

Every theme listed in themes/entries, and every theme omarchy ships, checked
the way Obelus reads it, packed so that the same theme always makes the same
bytes, and named in one themes/index.json beside the packages:

    themes.py build --old OLD --out OUT \
        --check-theme BIN --template TPL [--omarchy DIR]

OLD is the site as it was last published -- the gh-pages branch, or an
empty directory the first time. An entry whose commit has not moved is
carried over from there rather than fetched again: a package is a copy of
the author's files, so a repository that has gone stops its theme being
updated and does not take it off the site.

omarchy's themes are made again on every run, because making one costs
nothing and the bytes come out the same when nothing changed. Without
--omarchy (the clone failed) the ones already published are carried over.

An entry that cannot be listed fails the run, because it is somebody's pull
request. An omarchy theme that cannot be made is said in the summary and
left out, because nobody here asked for it.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11
    import tomli as tomllib

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ENTRIES = ROOT / "themes" / "entries"
FILE = "theme.toml"
OMARCHY = "https://github.com/basecamp/omarchy"
PREFIX = "omarchy-"
# What Obelus will make a directory of (`obelus_themes::named_safely`).
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
COMMIT = re.compile(r"[0-9a-f]{40}")
PICTURES = {".jpg", ".jpeg", ".png", ".webp"}
LARGEST_SIDE = 3840
LARGEST_FILE = 5 * 1024 * 1024
# The built-in themes with a light page, which a theme building on one is
# too. Obelus's `obelus_theme::builtin::ALL`; a built-in added there with a
# light page belongs here.
LIGHT_BASES = {"light", "gruvbox-light", "catppuccin-latte", "solarized-light"}
SPDX = "https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json"
# The order the groups are listed in, which is the order Obelus shows them.
GROUPS = ["obelus", "omarchy"]


class Refused(Exception):
    """A theme that may not be listed, and why."""


@dataclass
class Made:
    """A theme on the site: its line in the index, and how it got there."""

    record: dict
    how: str  # "made" or "carried"
    picture: str = ""  # where to see its picture, for the summary
    notes: list = field(default_factory=list)


def run(*args, cwd=None):
    return subprocess.run(
        [str(arg) for arg in args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def string(table, key):
    value = table.get(key) if isinstance(table, dict) else None
    return value.strip() if isinstance(value, str) else ""


# -- entries -----------------------------------------------------------------


def entries():
    """Every entry, by name: what its file says, and what is wrong with it."""
    found = {}
    for path in sorted(ENTRIES.glob("*.toml")):
        name = path.stem
        problems = []
        if not NAME.fullmatch(name):
            problems.append(
                "A theme's name is letters, digits, '-', '_' and '.', and does not start with '.'"
            )
        if name.startswith(PREFIX):
            problems.append(f"Names that start with {PREFIX} are omarchy's own themes")
        try:
            said = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as error:
            problems.append(f"This is not TOML: {error}")
            said = {}
        for key in ("repo", "commit", "path"):
            if not string(said, key):
                problems.append(f"There is no {key}")
        for key in sorted(set(said) - {"repo", "commit", "path"}):
            problems.append(f"Nothing in an entry is called {key}")
        if string(said, "commit") and not COMMIT.fullmatch(said["commit"]):
            problems.append("The commit is a whole commit, forty hex digits -- not a branch or a tag")
        where = string(said, "path")
        if where and (Path(where).is_absolute() or ".." in Path(where).parts):
            problems.append("The path stays inside the repository")
        found[name] = (said, problems)
    return found


def fetch(repo, commit, into):
    """The repository as it was at a commit, and the day of that commit."""
    into.mkdir(parents=True)
    run("git", "init", "-q", cwd=into)
    run("git", "remote", "add", "origin", repo, cwd=into)
    try:
        run("git", "fetch", "-q", "--depth", "1", "origin", commit, cwd=into)
    except subprocess.CalledProcessError:
        # A server that will not hand over one commit by name hands over the
        # whole history instead.
        try:
            run("git", "fetch", "-q", "origin", cwd=into)
        except subprocess.CalledProcessError as error:
            raise Refused(f"Could not fetch {repo}: {error.stderr.strip()}")
    try:
        run("git", "checkout", "-q", commit, cwd=into)
    except subprocess.CalledProcessError:
        raise Refused(f"{repo} has no commit {commit}")
    return run("git", "show", "-s", "--format=%cs", commit, cwd=into).strip()


def stage_entry(name, said, work):
    """The entry's theme, copied out of its repository into a directory of
    its own name, and the day of its commit."""
    checkout = work / "checkouts" / name
    updated = fetch(said["repo"], said["commit"], checkout)
    source = checkout / said["path"]
    staged = work / "staged" / name
    if source.is_dir():
        if not (source / FILE).is_file():
            raise Refused(f"There is no {FILE} in {said['path']}")
        shutil.copytree(source, staged, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    elif source.is_file() and source.suffix == ".toml":
        # A theme that is one file: no picture can go with it, because the
        # picture would be one file among whatever else is beside it.
        staged.mkdir(parents=True)
        shutil.copyfile(source, staged / FILE, follow_symlinks=False)
    else:
        raise Refused(f"{said['path']} is neither a directory holding {FILE} nor a .toml file")
    for path in staged.rglob("*"):
        if path.is_symlink():
            raise Refused(f"{path.relative_to(staged)} is a link, and a package holds files")
    return staged, updated


# -- what Obelus and the registry ask of a theme -------------------------------


def obelus_reads(check_theme, staged):
    """Refuses a theme Obelus cannot read, with obelus-check-theme's words."""
    result = subprocess.run([check_theme, staged], capture_output=True, text=True)
    if result.returncode != 0:
        said = (result.stderr + result.stdout).strip().replace(f"{staged}/", "")
        raise Refused(said or f"obelus-check-theme said nothing and exited {result.returncode}")


def picture_rules(path):
    problems = []
    if path.suffix.lower() not in PICTURES:
        problems.append(f"{path.name}: a wallpaper is a jpg, a png or a webp")
    if path.stat().st_size > LARGEST_FILE:
        problems.append(f"{path.name}: a wallpaper is at most 5 MB")
    try:
        with Image.open(path) as picture:
            width, height = picture.size
        if max(width, height) > LARGEST_SIDE:
            problems.append(
                f"{path.name}: a wallpaper is at most {LARGEST_SIDE} pixels a side, "
                f"and this is {width}x{height}"
            )
    except OSError:
        pass  # obelus-check-theme has already said it cannot be decoded
    return problems


def licensed(expression, known):
    """Whether a licence is an SPDX expression of identifiers SPDX lists."""
    words = [word for word in re.split(r"[\s()]+", expression) if word]
    if not words:
        return False
    after_with = False
    for word in words:
        if word in ("AND", "OR"):
            continue
        if word == "WITH":
            after_with = True
            continue
        if after_with:
            after_with = False  # an exception's name, from another list
            continue
        if word.rstrip("+") not in known:
            return False
    return True


def registry_rules(staged, theme, known):
    """What the registry asks of a theme beyond what Obelus does."""
    problems = []
    about = theme.get("about")
    for key in ("author", "description"):
        if not string(about, key):
            problems.append(f"[about] has no {key}, which the theme's card in Obelus shows")
    named = {FILE}
    wallpaper = theme.get("wallpaper")
    if isinstance(wallpaper, dict):
        file = string(wallpaper, "file")
        if file:
            named.add(Path(file).as_posix())
            if (staged / file).is_file():
                problems += picture_rules(staged / file)
        if not licensed(string(wallpaper, "license"), known):
            problems.append("[wallpaper] license is an SPDX identifier, like CC-BY-4.0")
        if not string(wallpaper, "source"):
            problems.append("[wallpaper] source says where the picture came from")
    for path in sorted(staged.rglob("*")):
        if path.is_file() and path.relative_to(staged).as_posix() not in named:
            problems.append(
                f"{path.relative_to(staged).as_posix()} is not named by {FILE}: "
                "a package holds the theme and its picture and nothing else"
            )
    return problems


def mode_of(theme):
    return "light" if string(theme, "base") in LIGHT_BASES else "dark"


# -- packing -----------------------------------------------------------------


def pack(staged, name, into):
    """Packs a theme's directory as `name/...`, the same bytes every time:
    the entries sorted, every time and owner the same, no time in the gzip
    header."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            paths = [staged] + sorted(staged.rglob("*"), key=lambda p: p.relative_to(staged).as_posix())
            for path in paths:
                relative = path.relative_to(staged).as_posix()
                info = tarfile.TarInfo(name if relative == "." else f"{name}/{relative}")
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if path.is_dir():
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                else:
                    info.mode = 0o644
                    info.size = path.stat().st_size
                    with path.open("rb") as file:
                        tar.addfile(info, file)
    into.write_bytes(buffer.getvalue())
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def record(name, group, theme, commit, updated, package, sha256):
    line = {
        "name": name,
        "group": group,
        "mode": mode_of(theme),
        "author": string(theme.get("about"), "author"),
        "description": string(theme.get("about"), "description"),
    }
    wallpaper = theme.get("wallpaper")
    if isinstance(wallpaper, dict) and string(wallpaper, "file"):
        line["wallpaper"] = {
            "license": string(wallpaper, "license"),
            "source": string(wallpaper, "source"),
        }
    line.update(package=package, sha256=sha256, commit=commit, updated=updated)
    return line


def finish(staged, name, group, commit, updated, out, check_theme, known, strict):
    """Checks a staged theme, packs it into `out`, and says what the index
    says of it. `strict` holds it to the registry's rules as well as
    Obelus's: an entry is, and omarchy's are made to fit them."""
    obelus_reads(check_theme, staged)
    theme = tomllib.loads((staged / FILE).read_text())
    if strict:
        problems = registry_rules(staged, theme, known)
        if problems:
            raise Refused("\n".join(problems))
    package = f"{name}-{commit[:12]}.tar.gz"
    sha256 = pack(staged, name, out / package)
    return record(name, group, theme, commit, updated, package, sha256)


# -- omarchy -----------------------------------------------------------------


def palette(omarchy, colors):
    """Every colour of an omarchy theme by name, the way omarchy resolves
    them -- its own resolver, so that a colour a theme leaves out comes out
    as omarchy's own templates have it."""
    if os.environ.get("OMARCHY_COLORS") == "raw":
        # For a machine without bash 4, where the resolver does not run:
        # what the file says and nothing it leaves to the resolver.
        said = tomllib.loads(colors.read_text())
        return {key: value for key, value in said.items() if isinstance(value, str)}
    out = run("bash", omarchy / "bin" / "omarchy-theme-color", "--file", colors, "--all")
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def render(template, colours):
    missing = []

    def fill(match):
        key = match.group(1)
        if key not in colours:
            missing.append(key)
            return match.group(0)
        return colours[key]

    body = re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", fill, template)
    if missing:
        raise Refused(f"omarchy gives no colour for {', '.join(sorted(set(missing)))}")
    return body


def stage_omarchy(omarchy, theme, template, commit, work, notes):
    """One omarchy theme made into an Obelus theme: Obelus's template filled
    from its palette, and the first of its pictures as the wallpaper."""
    name = PREFIX + theme.name
    colours = palette(omarchy, theme / "colors.toml")
    body = render(template, colours)
    staged = work / "staged" / name
    staged.mkdir(parents=True)
    lines = [
        f"# Made by the Obelus registry from omarchy's {theme.name} theme, as",
        f"# basecamp/omarchy had it at {commit}, through Obelus's",
        "# contrib/omarchy/obelus.toml.tpl.",
        "",
        body.rstrip("\n"),
        "",
        "[about]",
        'author = "omarchy"',
        f"description = {json.dumps(f'The {theme.name} theme from omarchy')}",
    ]
    backgrounds = theme / "backgrounds"
    pictures = sorted(
        path
        for path in (backgrounds.iterdir() if backgrounds.is_dir() else [])
        if path.is_file() and path.suffix.lower() in PICTURES
    )
    if pictures:
        picture = pictures[0]
        shutil.copyfile(picture, staged / picture.name)
        # omarchy's pictures are drawn for a desktop and many are larger
        # than the registry takes; Obelus draws one no larger than the
        # screen anyway, so the picture is made smaller rather than left out.
        if picture_rules(staged / picture.name):
            smaller(staged / picture.name)
            notes.append(f"Wallpaper made smaller to fit {LARGEST_SIDE} pixels a side")
        problems = picture_rules(staged / picture.name)
        if problems:
            (staged / picture.name).unlink()
            notes.append(f"No wallpaper: {'; '.join(problems)}")
        else:
            source =f"{OMARCHY}/blob/{commit}/themes/{theme.name}/backgrounds/{picture.name}"
            lines += [
                "",
                "[wallpaper]",
                f"file = {json.dumps(picture.name)}",
                'license = "MIT"',
                f"source = {json.dumps(source)}",
            ]
    (staged / FILE).write_text("\n".join(lines) + "\n")
    return staged


def smaller(path):
    """Makes a picture fit the registry's largest side, in its own format."""
    with Image.open(path) as picture:
        picture.load()
        kind = picture.format
        picture.thumbnail((LARGEST_SIDE, LARGEST_SIDE), Image.LANCZOS)
        if kind == "JPEG" and picture.mode != "RGB":
            picture = picture.convert("RGB")
        options = {"quality": 90} if kind in ("JPEG", "WEBP") else {}
        picture.save(path, format=kind, **options)


def omarchy_themes(omarchy):
    """Every omarchy theme with a palette, and the last commit that touched
    it -- its own, so that a commit to another theme is not an update to
    this one."""
    for theme in sorted((omarchy / "themes").iterdir()):
        if not (theme / "colors.toml").is_file():
            continue
        commit = run("git", "log", "-1", "--format=%H", "--", f"themes/{theme.name}", cwd=omarchy).strip()
        updated = run("git", "show", "-s", "--format=%cs", commit, cwd=omarchy).strip()
        yield theme, commit, updated


# -- the site ----------------------------------------------------------------


def published(old):
    """What the site said last time, by name."""
    index = old / "themes" / "index.json"
    if not index.is_file():
        return {}
    return {line["name"]: line for line in json.loads(index.read_text()).get("themes", [])}


def carried(line, old, out):
    """A theme from the last site, package and all, where its package is
    still there to carry."""
    package = old / "themes" / line["package"]
    if not package.is_file():
        return None
    shutil.copyfile(package, out / line["package"])
    return Made(line, "carried")


def spdx():
    with urllib.request.urlopen(SPDX, timeout=30) as response:
        listed = json.load(response)["licenses"]
    return {licence["licenseId"] for licence in listed}


def raw_url(repo, commit, path):
    """Where a file of a GitHub repository can be seen, for the summary."""
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", repo)
    return f"https://raw.githubusercontent.com/{match.group(1)}/{commit}/{path}" if match else ""


def build(arguments):
    old, out = Path(arguments.old), Path(arguments.out) / "themes"
    out.mkdir(parents=True, exist_ok=True)
    before = published(old)
    known = spdx()
    template = Path(arguments.template).read_text()
    made, refused = {}, {}
    work = Path(tempfile.mkdtemp(prefix="themes-"))
    try:
        for name, (said, problems) in entries().items():
            if problems:
                refused[name] = "\n".join(problems)
                continue
            line = before.get(name)
            if line and line.get("group") == "obelus" and line.get("commit") == said["commit"]:
                kept = carried(line, old, out)
                if kept:
                    made[name] = kept
                    continue
            try:
                staged, updated = stage_entry(name, said, work)
                line = finish(staged, name, "obelus", said["commit"], updated, out,
                              arguments.check_theme, known, strict=True)
                theme = tomllib.loads((staged / FILE).read_text())
                file = string(theme.get("wallpaper"), "file")
                where = f"{said['path'].rstrip('/')}/{file}" if file else ""
                made[name] = Made(line, "made", raw_url(said["repo"], said["commit"], where) if where else "")
            except Refused as why:
                refused[name] = str(why)

        omarchy_refused = {}
        if arguments.omarchy and Path(arguments.omarchy, "themes").is_dir():
            omarchy = Path(arguments.omarchy)
            for theme, commit, updated in omarchy_themes(omarchy):
                name = PREFIX + theme.name
                notes = []
                try:
                    staged = stage_omarchy(omarchy, theme, template, commit, work, notes)
                    line = finish(staged, name, "omarchy", commit, updated, out,
                                  arguments.check_theme, known, strict=True)
                    made[name] = Made(line, "made", line.get("wallpaper", {}).get("source", ""), notes)
                except Refused as why:
                    omarchy_refused[name] = str(why)
        else:
            for name, line in before.items():
                if line.get("group") == "omarchy":
                    kept = carried(line, old, out)
                    if kept:
                        made[name] = kept
    finally:
        shutil.rmtree(work, ignore_errors=True)

    lines = sorted(
        (kept.record for kept in made.values()),
        key=lambda line: (GROUPS.index(line["group"]), line["name"]),
    )
    (out / "index.json").write_text(
        json.dumps({"themes": lines}, indent=1, ensure_ascii=False) + "\n"
    )
    summary = summarise(made, refused, omarchy_refused, before)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as file:
            file.write(summary + "\n")
    return 1 if refused else 0


def summarise(made, refused, omarchy_refused, before):
    rows = ["## Themes", ""]
    if refused:
        rows += ["### Refused", ""]
        for name, why in sorted(refused.items()):
            rows += [f"**{name}**", "", "```", why, "```", ""]
    fresh = {name: kept for name, kept in made.items() if kept.how == "made"
             and before.get(name, {}).get("sha256") != kept.record["sha256"]}
    if fresh:
        rows += ["### New or changed", "", "| Theme | Mode | Author | Wallpaper |", "|---|---|---|---|"]
        for name, kept in sorted(fresh.items()):
            line = kept.record
            wallpaper = line.get("wallpaper")
            picture = (f"{wallpaper['license']}, [source]({wallpaper['source']})"
                       if wallpaper else "None")
            if kept.picture and kept.picture.startswith("https://raw.githubusercontent.com/"):
                picture += f"<br><img src=\"{kept.picture}\" width=\"240\">"
            notes = "".join(f"<br>{note}" for note in kept.notes)
            rows.append(f"| {name} | {line['mode']} | {line['author']} | {picture}{notes} |")
        rows.append("")
    gone = sorted(set(before) - set(made))
    if gone:
        rows += ["### No longer listed", "", ", ".join(gone), ""]
    if omarchy_refused:
        rows += ["### omarchy themes left out", ""]
        for name, why in sorted(omarchy_refused.items()):
            rows.append(f"- **{name}**: {why}")
        rows.append("")
    rows.append(f"{len(made)} themes listed, {sum(k.how == 'carried' for k in made.values())} carried over unchanged.")
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    building = commands.add_parser("build", help="make the themes half of the site")
    building.add_argument("--old", required=True, help="the site as last published")
    building.add_argument("--out", required=True, help="where the new site goes")
    building.add_argument("--check-theme", required=True, help="the obelus-check-theme binary")
    building.add_argument("--template", required=True, help="Obelus's contrib/omarchy/obelus.toml.tpl")
    building.add_argument("--omarchy", help="a clone of basecamp/omarchy, with its history")
    arguments = parser.parse_args()
    sys.exit(build(arguments))


if __name__ == "__main__":
    main()
