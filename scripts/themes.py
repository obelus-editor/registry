#!/usr/bin/env python3
"""The themes half of the registry's site.

Every theme listed in themes/entries, every theme omarchy ships, and every
theme omarchy's site lists from its community, checked the way Obelus reads
it, packed so that the same theme always makes the same bytes, and named in
one themes/index.json beside the packages:

    themes.py build --old OLD --out OUT \
        --check-theme BIN --template TPL [--omarchy DIR] [--site DIR]

OLD is the site as it was last published -- the gh-pages branch, or an
empty directory the first time. An entry whose commit has not moved is
carried over from there rather than fetched again: a package is a copy of
the author's files, so a repository that has gone stops its theme being
updated and does not take it off the site.

omarchy's themes are made again on every run, because making one costs
nothing and the bytes come out the same when nothing changed. Without
--omarchy (the clone failed) the ones already published are carried over.

The community's are made again on every run too, from each author's
repository as it is now -- which is also how a theme whose repository or
picture has gone stops being listed: it is not made, so it is not in the
index. Their list is themes/index.html in omarchy's site (--site), read from
a clone of it rather than from the page, because that file is the list --
contributors add a theme to it by a pull request -- and the page is only
what it renders to.

A community theme whose author says it may be copied is packed whole, as
omarchy's are. One whose author says nothing -- most of them -- has its
colours packed, which this script made, and not its picture, which nobody
may hand on: the index says where the author keeps it, at the commit read,
and what it hashes to, and Obelus fetches it from there. Without --site, or
without --omarchy (whose resolver makes the colours), the ones published
are carried over -- those whose picture is fetched only while it still
hashes to what the index says.

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
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
# The community's list: a figure for each theme, a link to its repository,
# and its picture on the site named for it (omacom/omarchy-site).
SITE_LIST = "themes/index.html"
SITE_FIGURE = '<figure class="themes__theme">'
SITE_THEME = re.compile(
    r'<figure class="themes__theme">\s*'
    r'<a href="https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)/?">'
    r'<img src="/assets/themes/([a-z0-9][a-z0-9-]*)\.webp"[^>]*></a>\s*'
    r'<figcaption><a href="[^"]*">([^<]+)</a></figcaption>\s*</figure>'
)
# How many authors' repositories are read at once.
AT_ONCE = 8
# How much of the page covers an omarchy picture, at most. Higher than
# Obelus's own default: omarchy's pictures are a desktop's -- bright and
# busy -- and at the default they are heavier under code than a picture
# chosen for it.
OMARCHY_OPACITY = 95
# And at least. A picture that is nearly all one colour -- lumon's,
# vantablack's -- would need the page at half or less to show as much,
# and what little it has would then be loud under code.
OMARCHY_LEAST_OPACITY = 70
# How much a picture under the page has to show: how far its colours stray
# from one another once the page is over it, as CIELAB's standard deviation.
# Not how far they stray from the page -- a picture that is all one tint
# reads as a page of another colour -- and the same number on a light page
# and a dark one, because CIELAB is spaced the way an eye sees. Found by
# looking: rose-pine's pale shapes on its pale page were nothing at 95
# (0.7), too faint at 91 (1.3) and 85 (2.1), too heavy at 75 (3.5), and
# right at 80 (2.8).
OMARCHY_SHOWING = 2.8
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
GROUPS = ["obelus", "omarchy", "omarchy-community"]


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


def registry_rules(staged, theme, known, fetched=False):
    """What the registry asks of a theme beyond what Obelus does -- of a
    picture Obelus fetches from its author, only that where it came from is
    said: it is not this registry's to hand on, so neither are its terms
    nor its size."""
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
            if (staged / file).is_file() and not fetched:
                problems += picture_rules(staged / file)
        if not fetched and not licensed(string(wallpaper, "license"), known):
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


def record(name, group, theme, commit, updated, package, sha256, fetched=None):
    line = {
        "name": name,
        "group": group,
        "mode": mode_of(theme),
        "author": string(theme.get("about"), "author"),
        "description": string(theme.get("about"), "description"),
    }
    wallpaper = theme.get("wallpaper")
    if isinstance(wallpaper, dict) and string(wallpaper, "file"):
        if fetched:
            # No licence, because there is none to give -- and where Obelus
            # fetches it from instead of the package.
            line["wallpaper"] = {
                "source": string(wallpaper, "source"),
                "file": string(wallpaper, "file"),
                "url": fetched["url"],
                "sha256": fetched["sha256"],
            }
        else:
            line["wallpaper"] = {
                "license": string(wallpaper, "license"),
                "source": string(wallpaper, "source"),
            }
    line.update(package=package, sha256=sha256, commit=commit, updated=updated)
    return line


def finish(staged, name, group, commit, updated, out, check_theme, known, strict, fetched=None):
    """Checks a staged theme, packs it into `out`, and says what the index
    says of it. `strict` holds it to the registry's rules as well as
    Obelus's: an entry is, and omarchy's are made to fit them. `fetched` is
    a picture the theme names that Obelus fetches from its author: checked
    here with the theme, and left out of the package."""
    obelus_reads(check_theme, staged)
    theme = tomllib.loads((staged / FILE).read_text())
    if strict:
        problems = registry_rules(staged, theme, known, fetched=bool(fetched))
        if problems:
            raise Refused("\n".join(problems))
    if fetched:
        (staged / string(theme.get("wallpaper"), "file")).unlink()
    package = f"{name}-{commit[:12]}.tar.gz"
    sha256 = pack(staged, name, out / package)
    return record(name, group, theme, commit, updated, package, sha256, fetched)


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
    backgrounds = theme / "backgrounds"
    pictures = sorted(
        path
        for path in (backgrounds.iterdir() if backgrounds.is_dir() else [])
        if path.is_file() and path.suffix.lower() in PICTURES
    )
    return stage_palette(
        omarchy,
        PREFIX + theme.name,
        theme / "colors.toml",
        template,
        [
            f"# Made by the Obelus registry from omarchy's {theme.name} theme, as",
            f"# basecamp/omarchy had it at {commit}, through Obelus's",
            "# contrib/omarchy/obelus.toml.tpl.",
        ],
        ("omarchy", f"The {theme.name} theme from omarchy"),
        pictures[0] if pictures else None,
        ("MIT", f"{OMARCHY}/blob/{commit}/themes/{theme.name}/backgrounds/"),
        work,
        notes,
    )


def stage_palette(omarchy, name, colors, template, header, about, picture, credit, work, notes,
                  whole=True):
    """An omarchy palette made into an Obelus theme: Obelus's template filled
    from it through omarchy's resolver, and `picture` as its wallpaper.

    `credit` is the picture's licence -- empty for one nobody may hand on --
    and the directory it came from, to which its file's name is added. A
    picture that is not `whole` is its author's to hand on and not this
    registry's: it is not made smaller, because a smaller one would be a
    copy, and it is beside the theme only to be checked and measured."""
    colours = palette(omarchy, colors)
    body = render(template, colours)
    staged = work / "staged" / name
    staged.mkdir(parents=True)
    author, description = about
    lines = header + [
        "",
        body.rstrip("\n"),
        "",
        "[about]",
        f"author = {json.dumps(author)}",
        f"description = {json.dumps(description)}",
    ]
    if picture:
        shutil.copyfile(picture, staged / picture.name)
        # omarchy's pictures are drawn for a desktop and many are larger
        # than the registry takes; Obelus draws one no larger than the
        # screen anyway, so the picture is made smaller rather than left out.
        if whole and picture_rules(staged / picture.name):
            smaller(staged / picture.name)
            notes.append(f"Wallpaper made smaller to fit {LARGEST_SIDE} pixels a side")
        problems = picture_rules(staged / picture.name) if whole else []
        if problems:
            (staged / picture.name).unlink()
            notes.append(f"No wallpaper: {'; '.join(problems)}")
        else:
            license, where = credit
            opacity = veiled_at(staged / picture.name, tomllib.loads(body).get("background"))
            notes.append(f"Wallpaper at opacity {opacity}")
            lines += [
                "",
                "[wallpaper]",
                f"file = {json.dumps(picture.name)}",
                f"opacity = {opacity}",
            ]
            if license:
                lines.append(f"license = {json.dumps(license)}")
            lines.append(f"source = {json.dumps(where + picture.name)}")
    (staged / FILE).write_text("\n".join(lines) + "\n")
    return staged


def veiled_at(path, page):
    """The most of the page that can be laid over a picture with the picture
    still showing OMARCHY_SHOWING, between OMARCHY_LEAST_OPACITY and
    OMARCHY_OPACITY.

    Laid the way the window lays it: the page's colour mixed in at the
    opacity in sRGB's own numbers, unconverted (obelus-gui's paint).

    Refuses the theme, rather than failing the run, where there is nothing
    to measure: this comes before obelus-check-theme, which would have said
    so of a picture that will not decode."""
    if not isinstance(page, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", page):
        raise Refused(f"The page's colour is {page!r}, and a wallpaper is measured against #rrggbb")
    page = tuple(int(page[i:i + 2], 16) for i in (1, 3, 5))
    try:
        with Image.open(path) as picture:
            picture = picture.convert("RGBA")
            # How much a picture shows does not need every pixel of it, and a
            # few thousand keep the whole of omarchy's themes to seconds.
            picture.thumbnail((128, 128), Image.BOX)
    except OSError as error:
        raise Refused(f"{path.name} cannot be decoded: {error}")
    # Where it is see-through, the window shows the black it clears to.
    ground = Image.new("RGBA", picture.size, (0, 0, 0, 255))
    pixels = list(Image.alpha_composite(ground, picture).convert("RGB").getdata())
    for opacity in range(OMARCHY_OPACITY, OMARCHY_LEAST_OPACITY, -1):
        if showing(pixels, page, opacity) >= OMARCHY_SHOWING:
            return opacity
    return OMARCHY_LEAST_OPACITY


def showing(pixels, page, opacity):
    """How far the colours of a picture under the page at `opacity` stray
    from one another: their standard deviation in CIELAB."""
    through = (100 - opacity) / 100
    seen = [
        lab(*(ground + (colour - ground) * through for colour, ground in zip(pixel, page)))
        for pixel in pixels
    ]
    middle = [sum(colour[i] for colour in seen) / len(seen) for i in range(3)]
    return (
        sum(sum((colour[i] - middle[i]) ** 2 for i in range(3)) for colour in seen) / len(seen)
    ) ** 0.5


def lab(red, green, blue):
    """An sRGB colour, each part out of 255, in CIELAB under D65."""

    def linear(part):
        part /= 255
        return part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    red, green, blue = linear(red), linear(green), linear(blue)
    x = f((0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047)
    y = f(0.2126 * red + 0.7152 * green + 0.0722 * blue)
    z = f((0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883)
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


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


# -- omarchy's community -------------------------------------------------------


@dataclass
class Listed:
    """A theme omarchy's site lists from its community."""

    repo: str  # owner/name on GitHub
    slug: str  # what the site calls its picture, and the theme's name here
    title: str


def community(site):
    """Every theme the site's list names, in its order."""
    text = (Path(site) / SITE_LIST).read_text(encoding="utf-8")
    found = [Listed(repo, slug, title.strip()) for repo, slug, title in SITE_THEME.findall(text)]
    # A figure the pattern does not read is the site having changed shape:
    # said, rather than a theme quietly not listed.
    figures = text.count(SITE_FIGURE)
    if figures != len(found):
        raise Refused(f"{SITE_LIST} has {figures} themes and {len(found)} of them could be read")
    return found


def github(path):
    """What GitHub's API says at `path`, with the run's token where there is
    one -- a run asks once a theme, which is more than an hour allows a
    caller with none."""
    request = urllib.request.Request(f"https://api.github.com/{path}")
    request.add_header("Accept", "application/vnd.github+json")
    if os.environ.get("GITHUB_TOKEN"):
        request.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetched(url):
    """The bytes at `url`."""
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()
    except OSError as error:
        raise Refused(f"Could not fetch {url}: {error}")


def stage_community(listed, omarchy, template, known, work, notes):
    """One community theme made into an Obelus theme the way omarchy's are,
    from its author's repository as it is now: the staged theme, its commit
    and day, the picture Obelus is to fetch where it is not packed, and
    where to see the picture.

    The picture is read from where Obelus will fetch it, not from the
    clone, so that the address the index gives is one that answered with
    these bytes."""
    name = PREFIX + listed.slug
    try:
        about = github(f"repos/{listed.repo}")
    except OSError as error:
        raise Refused(f"GitHub says nothing of {listed.repo}: {error}")
    repo = about["full_name"]  # where a repository that was renamed is now
    checkout = work / "checkouts" / name
    # The commit and its tree and none of the blobs: the palette and one
    # picture are all that is read, and both are fetched on their own --
    # a shallow clone without blobs cannot fetch one it is missing.
    try:
        run("git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
            f"https://github.com/{repo}", checkout)
    except subprocess.CalledProcessError as error:
        raise Refused(f"Could not clone {repo}: {error.stderr.strip()}")
    commit = run("git", "rev-parse", "HEAD", cwd=checkout).strip()
    updated = run("git", "show", "-s", "--format=%cs", commit, cwd=checkout).strip()
    raw = f"https://raw.githubusercontent.com/{repo}/{commit}"
    if not run("git", "ls-tree", "--name-only", "HEAD", "colors.toml", cwd=checkout).strip():
        raise Refused(f"{repo} has no colors.toml at its top")
    (checkout / "colors.toml").write_bytes(fetched(f"{raw}/colors.toml"))
    # Unquoted, whatever the names are written in.
    pictures = sorted(
        path
        for path in run("git", "ls-tree", "-z", "--name-only", "HEAD", "backgrounds/", cwd=checkout).split("\0")
        if Path(path).suffix.lower() in PICTURES
    )
    license = (about.get("license") or {}).get("spdx_id") or ""
    whole = license in known
    picture = url = None
    if pictures:
        url = f"{raw}/{urllib.parse.quote(pictures[0])}"
        picture = work / "pictures" / name / Path(pictures[0]).name
        picture.parent.mkdir(parents=True)
        picture.write_bytes(fetched(url))
    staged = stage_palette(
        omarchy,
        name,
        checkout / "colors.toml",
        template,
        [
            f"# Made by the Obelus registry from {listed.title}, a theme omarchy's",
            f"# site lists from its community, as github.com/{repo} had it at",
            f"# {commit}, through Obelus's contrib/omarchy/obelus.toml.tpl.",
        ],
        (repo.split("/")[0], f"The {listed.title} theme from omarchy's community"),
        picture,
        (license if whole else "", f"https://github.com/{repo}/blob/{commit}/backgrounds/"),
        work,
        notes,
        whole=whole,
    )
    fetch = None
    if picture and not whole and (staged / picture.name).is_file():
        notes.append("Wallpaper fetched from its author, who gives no licence")
        fetch = {"url": url, "sha256": hashlib.sha256(picture.read_bytes()).hexdigest()}
    return staged, commit, updated, fetch, url or ""


def still_there(line):
    """Whether a published community theme's picture, where Obelus fetches
    it, is still the picture the index says -- for carrying one over in a
    run that could not make it again."""
    wallpaper = line.get("wallpaper") or {}
    if not wallpaper.get("url"):
        return True
    try:
        return hashlib.sha256(fetched(wallpaper["url"])).hexdigest() == wallpaper.get("sha256")
    except Refused:
        return False


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

        listing, community_refused = None, {}
        if arguments.site and arguments.omarchy and Path(arguments.omarchy, "themes").is_dir():
            try:
                listing = community(arguments.site)
            except (OSError, Refused) as why:
                print(f"::warning::Not reading omarchy's community: {why}")
        if listing is not None:
            omarchy = Path(arguments.omarchy)
            # Taken before the threads start, which `made` is written under.
            shipped = set(made)

            def make(listed):
                name = PREFIX + listed.slug
                if name in shipped:
                    return name, None, f"omarchy ships a theme called {name}, which is the one listed"
                notes = []
                try:
                    staged, commit, updated, fetch, picture = stage_community(
                        listed, omarchy, template, known, work, notes)
                    line = finish(staged, name, "omarchy-community", commit, updated, out,
                                  arguments.check_theme, known, strict=True, fetched=fetch)
                    return name, Made(line, "made", picture, notes), None
                except Refused as why:
                    return name, None, str(why)
                except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as error:
                    # Somebody else's repository, in a shape nothing here
                    # foresaw: that theme is left out, not the run failed.
                    return name, None, f"{type(error).__name__}: {error}"

            # Each theme has directories of its own under `work`, so they
            # are made side by side; most of the time is the network's.
            with ThreadPoolExecutor(AT_ONCE) as pool:
                for name, kept, why in pool.map(make, listing):
                    if kept:
                        made[name] = kept
                    else:
                        community_refused[name] = why
        else:
            for name, line in before.items():
                if line.get("group") == "omarchy-community" and still_there(line):
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
    summary = summarise(made, refused, omarchy_refused, community_refused, before)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as file:
            file.write(summary + "\n")
    return 1 if refused else 0


def summarise(made, refused, omarchy_refused, community_refused, before):
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
            picture = (f"{wallpaper.get('license') or 'Fetched from its author'}, "
                       f"[source]({wallpaper['source']})" if wallpaper else "None")
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
    if community_refused:
        rows += ["### Community themes left out", ""]
        for name, why in sorted(community_refused.items()):
            rows.append(f"- **{name}**: {why.splitlines()[0] if why else why}")
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
    building.add_argument("--site", help="a clone of omacom/omarchy-site, for its community's list")
    arguments = parser.parse_args()
    sys.exit(build(arguments))


if __name__ == "__main__":
    main()
