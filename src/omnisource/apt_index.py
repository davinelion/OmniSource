"""Read Cydia/ElleKit ``Packages`` indexes so tweaks can be collected upstream-first.

GitHub releases cover a lot of tweaks, but most single-purpose iOS tweaks are
published only to their developer's own apt repository (PoomSmart's
``…/repo/Packages``, rootless/ElleKit repos under ``dists/<suite>``).  This
module reads such an index *as data*: it never installs anything, it just finds
the newest release of one package, and returns the download URL plus the digest
the index itself publishes for it.  That digest is what makes the Tweak Factory's
provenance record meaningful — the SHA-256 comes from the developer's own index
rather than from whatever bytes happened to arrive.

Only the parts of the index the factory needs are understood, deliberately:
``Package``/``Version``/``Architecture``/``Filename``/``Size``/``SHA256`` and the
relationship fields (``Depends``/``Conflicts``/``Provides``).  Parsing is a
deb822 stanza reader, not a full ``dpkg`` implementation, and version ordering
follows dpkg's epoch/upstream/revision split so ``1.10.0-1`` beats ``1.10.0``
while ``1.0~beta1`` still loses to ``1.0``.

A repository index is untrusted input, so two rules are enforced here instead of
being left to the caller:

* ``Filename`` must stay on the same host as the index — an index cannot point
  the factory at a different site, and
* every download URL must be ``https``.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import cmp_to_key

USER_AGENT = "omnisource-apt-index (+https://iamsmmh.github.io/OmniSource)"
MAX_INDEX_BYTES = 64 * 1024 * 1024
DEBIAN_ARCHES = ("iphoneos-arm", "iphoneos-arm64", "iphoneos-arm64e", "iphoneos-armv7", "iphoneos-armv6")
#: Suffixes tried for one index path, in order. Plain first: it keeps the
#: parser honest and the digests directly comparable.
INDEX_SUFFIXES = ("", ".gz", ".bz2", ".xz")
_FIELD_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]*):[ \t]*(.*)$")
_BLOCK_RE = re.compile(r"\n[ \t]*\n")
#: Relationship names that never identify a tweak bundle id.
_SYSTEM_DEPS = frozenset(
    {
        "apt",
        "cydia",
        "coreutils",
        "debianutils",
        "dpkg",
        "elfutils",
        "firmware",
        "gnutar",
        "ldid",
        "mobilesubstrate",
        "perl",
        "preferenceloader",
        "sed",
        "shell-cmds",
        "sqlite3",
        "substrate",
        "tcpmux",
        "uikittools",
        "zip",
    }
)
_SYSTEM_PREFIXES = ("lib", "python", "swift", "mobile-", "gsc.", "org.openjdk", "com.opentrash.", "me.exlive.")


class AptIndexError(Exception):
    """An index that cannot be read, or that resolves to nothing usable."""


@dataclass(frozen=True)
class Relation:
    """One alternatives group of a ``Depends``/``Conflicts``/``Provides`` field."""

    names: tuple[str, ...]
    constraint: str = ""


@dataclass(frozen=True)
class DebFile:
    """A resolved ``.deb`` inside an apt index, with the digest the index states."""

    package: str
    version: str
    architecture: str
    url: str
    name: str
    sha256: str = ""
    size: int = 0
    depends: tuple[Relation, ...] = field(default_factory=tuple)
    conflicts: tuple[Relation, ...] = field(default_factory=tuple)
    provides: tuple[Relation, ...] = field(default_factory=tuple)

    @property
    def tweak_requires(self) -> tuple[str, ...]:
        """Tweak packages this deb needs, one entry per alternatives group.

        ``a | b`` stays one entry, joined with ``|``: an alternatives group is
        satisfied by *any* of its members, so reporting each name separately
        would turn a working bundle into a false warning.  System packages
        (``mobilesubstrate``, ``firmware``, shared libraries) are dropped —
        an injected app already has whatever it ships with.
        """
        wanted: list[str] = []
        for relation in self.depends:
            # A group that also offers a system alternative ("firmware (>= 14.0) |
            # com.ps.forceinpicture") is satisfied by the device, so only groups
            # made *entirely* of tweak packages can genuinely be missing.
            if not all(_is_tweak_package(name) for name in relation.names):
                continue
            names = tuple(relation.names)
            if not names:
                continue
            entry = "|".join(names)
            if entry not in wanted:
                wanted.append(entry)
        return tuple(wanted)


# ---------------------------------------------------------------------------
# Stanza parsing
# ---------------------------------------------------------------------------


def parse_index(text: str) -> list[dict[str, str]]:
    """Split a ``Packages`` index into field dictionaries (continuations folded)."""
    stanzas: list[dict[str, str]] = []
    for block in _BLOCK_RE.split(text.replace("\r\n", "\n").replace("\r", "\n")):
        fields: dict[str, str] = {}
        key: str | None = None
        for line in block.split("\n"):
            if not line.strip():
                continue
            if line[:1] in (" ", "\t") and key is not None:
                continuation = line.strip()
                if continuation == ".":  # dpkg's placeholder for a blank line in a field
                    continue
                fields[key] = f"{fields[key]} {continuation}"
                continue
            match = _FIELD_RE.match(line)
            if match is None:
                # Junk lines are skipped rather than guessing: a truncated or
                # hostile index must never invent a field out of an orphan line.
                key = None
                continue
            key = match.group(1)
            fields[key] = match.group(2).strip()
        if fields.get("Package"):
            stanzas.append(fields)
    return stanzas


def _version_chunks(value: str) -> list[tuple[int, str]]:
    """Tokenize a Debian version part the way ``dpkg --compare-versions`` does.

    ``~`` sorts before the end of the string, which sorts before any text,
    which sorts before any number: that is what makes ``1.0~rc1`` lose to
    ``1.0`` and ``1.0.10`` beat ``1.0.9``.
    """
    text = str(value or "")
    tokens: list[tuple[int, str]] = []
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char == "~":
            tokens.append((0, ""))
            pos += 1
            continue
        if char.isdigit():
            end = pos
            while end < len(text) and text[end].isdigit():
                end += 1
            tokens.append((3, text[pos:end]))
            pos = end
            continue
        end = pos
        while end < len(text) and not text[end].isdigit() and text[end] != "~":
            end += 1
        tokens.append((2, text[pos:end].casefold()))
        pos = end
    return tokens


def _cmp_part(left: str, right: str) -> int:
    a, b = _version_chunks(left), _version_chunks(right)
    for index in range(max(len(a), len(b))):
        # A missing chunk is the end of the string: above "~", below any text.
        weight_a, text_a = a[index] if index < len(a) else (1, "")
        weight_b, text_b = b[index] if index < len(b) else (1, "")
        if weight_a != weight_b:
            return -1 if weight_a < weight_b else 1
        if text_a != text_b:
            if weight_a == 3:
                return -1 if int(text_a or 0) < int(text_b or 0) else 1
            return -1 if text_a < text_b else 1
    return 0


def _version_cmp(left: str, right: str) -> int:
    """Compare two Debian versions (epoch, upstream, revision)."""

    def split(value: str) -> tuple[int, str, str]:
        text = str(value or "").strip()
        epoch, sep, rest = text.partition(":")
        number = int(epoch) if sep and epoch.isdigit() else 0
        if not sep:
            rest = text
        upstream, _, revision = rest.rpartition("-")
        return (number, upstream or rest, revision if _ else "")

    epoch_a, up_a, rev_a = split(left)
    epoch_b, up_b, rev_b = split(right)
    if epoch_a != epoch_b:
        return -1 if epoch_a < epoch_b else 1
    upstream = _cmp_part(up_a, up_b)
    if upstream:
        return upstream
    return _cmp_part(rev_a, rev_b)


def parse_relations(value: str) -> tuple[Relation, ...]:
    """``a (>= 1.0), b | c`` → alternatives groups, constraints preserved."""
    relations: list[Relation] = []
    for group in re.split(r",", str(value or "")):
        names: list[str] = []
        constraint = ""
        for alternative in re.split(r"\|", group):
            chunk = alternative.strip()
            if not chunk:
                continue
            match = re.match(r"^([^\s(]+)(?:\s*\(([^)]*)\))?", chunk)
            if match is None:
                continue
            names.append(match.group(1).strip())
            constraint = (match.group(2) or "").strip() or constraint
        if names:
            relations.append(Relation(names=tuple(names), constraint=constraint))
    return tuple(relations)


def _is_tweak_package(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SYSTEM_DEPS or lowered.startswith(_SYSTEM_PREFIXES):
        return False
    return re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z0-9-]+\.[a-z0-9.-]+$", lowered) is not None


def select_deb(
    stanzas: Iterable[dict[str, str]],
    *,
    package: str = "",
    architectures: Sequence[str] = (),
) -> dict[str, str] | None:
    """Newest stanza for ``package``, ties broken by the architecture order.

    Newest first, then the preferred slice *within* that version: a repository
    that publishes ``1.12.14`` for both ``iphoneos-arm`` and ``iphoneos-arm64``
    must not resolve to the arm one just because its index lists it first, while
    a package whose newest release only exists for another architecture still
    resolves rather than silently downgrading to an older build.
    """
    arches = tuple(architectures) or DEBIAN_ARCHES
    candidates = [
        stanza for stanza in stanzas if (not package or stanza.get("Package") == package) and stanza.get("Filename")
    ]
    if not candidates:
        return None

    def arch_rank(stanza: dict[str, str]) -> int:
        arch = str(stanza.get("Architecture", ""))
        return arches.index(arch) if arch in arches else len(arches)

    def compare(left: dict[str, str], right: dict[str, str]) -> int:
        return _version_cmp(str(right.get("Version", "")), str(left.get("Version", ""))) or (
            arch_rank(left) - arch_rank(right)
        )

    return sorted(candidates, key=cmp_to_key(compare))[0]


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------


def candidate_index_urls(
    *, index_url: str = "", repo: str = "", suite: str = "", component: str = "", architectures: Sequence[str] = ()
) -> list[str]:
    """Every index path worth trying for a repository, in preference order.

    ``index_url`` names one file directly (flat Cydia repos). ``repo`` +
    ``suite`` (+ ``component``) describe the ``dists/`` layout rootless repos
    use, and expand to one path per architecture and suffix.
    """
    urls: list[str] = []

    def add(base: str) -> None:
        for suffix in INDEX_SUFFIXES:
            candidate = base + suffix
            if candidate not in urls:
                urls.append(candidate)

    if index_url:
        add(index_url)
        return urls
    if not repo or not suite:
        raise AptIndexError("an apt member needs either indexUrl, or repo plus suite")
    root = repo.rstrip("/")
    arches = tuple(architectures) or DEBIAN_ARCHES
    components = (component,) if component else ("",)
    for arch in arches:
        for comp in components:
            path = f"{root}/dists/{suite}"
            if comp:
                path = f"{path}/{comp}"
            add(f"{path}/binary-{arch}/Packages")
    for arch in arches:
        add(f"{root}/dists/{suite}/binary-{arch}/Packages")
    add(f"{root}/Packages")
    return urls


def join_filename(index_url: str, filename: str) -> str:
    """Resolve an index ``Filename`` against the index URL, same-host only."""
    base = urllib.parse.urlsplit(index_url)
    if base.scheme != "https":
        raise AptIndexError(f"index URL must be https (got {index_url!r})")
    name = str(filename or "").strip()
    if not name or "://" in name or name.startswith("/"):
        raise AptIndexError(f"unsafe Filename in index: {name!r}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise AptIndexError(f"unsafe Filename in index: {name!r}")
    directory = [part for part in base.path.rsplit("/", 1)[0].split("/") if part not in ("", ".")]
    url = urllib.parse.urlunsplit((base.scheme, base.netloc, "/" + "/".join([*directory, *parts]), "", ""))
    resolved = urllib.parse.urlsplit(url)
    if resolved.netloc.casefold() != base.netloc.casefold():
        raise AptIndexError(f"index tried to leave its own host: {url}")
    return url


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_text(url: str, *, limit: int = MAX_INDEX_BYTES) -> str:
    """Download an index and transparently uncompress ``.gz``/``.bz2``/``.xz``."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise AptIndexError(f"index larger than {limit} bytes: {url}")
    if url.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif url.endswith(".bz2") or raw[:3] == b"BZh":
        raw = bz2.decompress(raw)
    elif url.endswith(".xz") or raw[:6] == b"\xfd7zXZ\x00":
        raw = lzma.decompress(raw)
    return raw.decode("utf-8", "replace")


def read_index(
    *,
    index_url: str = "",
    repo: str = "",
    suite: str = "",
    component: str = "",
    architectures: Sequence[str] = (),
    fetch: Callable[[str], str] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Return ``(url_used, stanzas)`` for the first readable index."""
    getter = fetch or fetch_text
    tried: list[str] = []
    for candidate in candidate_index_urls(
        index_url=index_url, repo=repo, suite=suite, component=component, architectures=architectures
    ):
        tried.append(candidate)
        try:
            text = getter(candidate)
        except Exception as error:  # a missing compression variant is normal
            if isinstance(error, AptIndexError):
                raise
            continue
        stanzas = parse_index(text)
        if stanzas:
            return candidate, stanzas
    raise AptIndexError("no readable Packages index at: " + ", ".join(tried[:6]))


def resolve_deb(
    *,
    package: str,
    index_url: str = "",
    repo: str = "",
    suite: str = "",
    component: str = "",
    architectures: Sequence[str] = (),
    pin_version: str = "",
    fetch: Callable[[str], str] | None = None,
) -> DebFile:
    """Find ``package`` in a repository index and describe its newest deb."""
    if not re.match(r"^[a-z0-9][a-z0-9.+-]*$", package or ""):
        raise AptIndexError(f"package name is not a Debian package name: {package!r}")
    used, stanzas = read_index(
        index_url=index_url,
        repo=repo,
        suite=suite,
        component=component,
        architectures=architectures,
        fetch=fetch,
    )
    wanted = [stanza for stanza in stanzas if stanza.get("Package") == package]
    if pin_version:
        wanted = [stanza for stanza in wanted if str(stanza.get("Version")) == pin_version]
    if not wanted:
        suffix = f" pinned to {pin_version}" if pin_version else ""
        raise AptIndexError(f"{package}{suffix} is not published in {used}")
    chosen = select_deb(wanted, architectures=tuple(architectures) or DEBIAN_ARCHES)
    if chosen is None:
        raise AptIndexError(f"{package}: no usable stanza in {used}")
    url = join_filename(used, str(chosen.get("Filename", "")))
    size_raw = str(chosen.get("Size", "")).strip()
    sha_raw = str(chosen.get("SHA256", "")).strip().lower()
    sha256 = sha_raw if re.fullmatch(r"[0-9a-f]{64}", sha_raw) else ""
    return DebFile(
        package=package,
        version=str(chosen.get("Version", "")),
        architecture=str(chosen.get("Architecture", "")),
        url=url,
        name=url.rsplit("/", 1)[-1],
        sha256=sha256,
        size=int(size_raw) if size_raw.isdigit() else 0,
        depends=parse_relations(str(chosen.get("Depends", ""))),
        conflicts=parse_relations(str(chosen.get("Conflicts", ""))),
        provides=parse_relations(str(chosen.get("Provides", ""))),
    )
