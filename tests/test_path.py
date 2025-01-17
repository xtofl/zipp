import io
import itertools
import contextlib
import pathlib
import tempfile
import shutil
import pickle
import sys
import unittest
import zipfile

import jaraco.itertools
from jaraco.functools import compose

import zipp

from ._test_params import parameterize, Invoked


def add_dirs(zf):
    """
    Given a writable zip file zf, inject directory entries for
    any directories implied by the presence of children.
    """
    for name in zipp.CompleteDirs._implied_dirs(zf.namelist()):
        zf.writestr(name, b"")
    return zf


def build_alpharep_fixture():
    """
    Create a zip file with this structure:

    .
    ├── a.txt
    ├── b
    │   ├── c.txt
    │   ├── d
    │   │   └── e.txt
    │   └── f.txt
    ├── g
    │   └── h
    │       └── i.txt
    └── j
        ├── k.bin
        ├── l.baz
        └── m.bar

    This fixture has the following key characteristics:

    - a file at the root (a)
    - a file two levels deep (b/d/e)
    - multiple files in a directory (b/c, b/f)
    - a directory containing only a directory (g/h)
    - a directory with files of different extensions (j/klm)

    "alpha" because it uses alphabet
    "rep" because it's a representative example
    """
    data = io.BytesIO()
    zf = zipfile.ZipFile(data, "w")
    zf.writestr("a.txt", b"content of a")
    zf.writestr("b/c.txt", b"content of c")
    zf.writestr("b/d/e.txt", b"content of e")
    zf.writestr("b/f.txt", b"content of f")
    zf.writestr("g/h/i.txt", b"content of i")
    zf.writestr("j/k.bin", b"content of k")
    zf.writestr("j/l.baz", b"content of l")
    zf.writestr("j/m.bar", b"content of m")
    zf.filename = "alpharep.zip"
    return zf


@contextlib.contextmanager
def temp_dir():
    tmpdir = tempfile.mkdtemp()
    try:
        yield pathlib.Path(tmpdir)
    finally:
        shutil.rmtree(tmpdir)


alpharep_generators = [
    Invoked.wrap(build_alpharep_fixture),
    Invoked.wrap(compose(add_dirs, build_alpharep_fixture)),
]

pass_alpharep = parameterize(['alpharep'], alpharep_generators)


class TestPath(unittest.TestCase):
    def setUp(self):
        self.fixtures = contextlib.ExitStack()
        self.addCleanup(self.fixtures.close)

    def zipfile_ondisk(self, alpharep):
        tmpdir = pathlib.Path(self.fixtures.enter_context(temp_dir()))
        buffer = alpharep.fp
        alpharep.close()
        path = tmpdir / alpharep.filename
        with path.open("wb") as strm:
            strm.write(buffer.getvalue())
        return path

    @pass_alpharep
    def test_iterdir_and_types(self, alpharep):
        root = zipp.Path(alpharep)
        assert root.is_dir()
        a, b, g, j = root.iterdir()
        assert a.is_file()
        assert b.is_dir()
        assert g.is_dir()
        c, f, d = b.iterdir()
        assert c.is_file() and f.is_file()
        (e,) = d.iterdir()
        assert e.is_file()
        (h,) = g.iterdir()
        (i,) = h.iterdir()
        assert i.is_file()

    @pass_alpharep
    def test_is_file_missing(self, alpharep):
        root = zipp.Path(alpharep)
        assert not root.joinpath('missing.txt').is_file()

    @pass_alpharep
    def test_iterdir_on_file(self, alpharep):
        root = zipp.Path(alpharep)
        a, b, g, j = root.iterdir()
        with self.assertRaises(ValueError):
            a.iterdir()

    @pass_alpharep
    def test_subdir_is_dir(self, alpharep):
        root = zipp.Path(alpharep)
        assert (root / 'b').is_dir()
        assert (root / 'b/').is_dir()
        assert (root / 'g').is_dir()
        assert (root / 'g/').is_dir()

    @pass_alpharep
    def test_open(self, alpharep):
        root = zipp.Path(alpharep)
        a, b, g, j = root.iterdir()
        with a.open(encoding="utf-8") as strm:
            data = strm.read()
        self.assertEqual(data, "content of a")
        with a.open('r', "utf-8") as strm:  # not a kw, no gh-101144 TypeError
            data = strm.read()
        self.assertEqual(data, "content of a")

    def test_open_encoding_utf16(self):
        in_memory_file = io.BytesIO()
        zf = zipfile.ZipFile(in_memory_file, "w")
        zf.writestr("path/16.txt", "This was utf-16".encode("utf-16"))
        zf.filename = "test_open_utf16.zip"
        root = zipp.Path(zf)
        (path,) = root.iterdir()
        u16 = path.joinpath("16.txt")
        with u16.open('r', "utf-16") as strm:
            data = strm.read()
        assert data == "This was utf-16"
        with u16.open(encoding="utf-16") as strm:
            data = strm.read()
        assert data == "This was utf-16"

    def test_open_encoding_errors(self):
        in_memory_file = io.BytesIO()
        zf = zipfile.ZipFile(in_memory_file, "w")
        zf.writestr("path/bad-utf8.bin", b"invalid utf-8: \xff\xff.")
        zf.filename = "test_read_text_encoding_errors.zip"
        root = zipp.Path(zf)
        (path,) = root.iterdir()
        u16 = path.joinpath("bad-utf8.bin")

        # encoding= as a positional argument for gh-101144.
        data = u16.read_text("utf-8", errors="ignore")
        assert data == "invalid utf-8: ."
        with u16.open("r", "utf-8", errors="surrogateescape") as f:
            assert f.read() == "invalid utf-8: \udcff\udcff."

        # encoding= both positional and keyword is an error; gh-101144.
        with self.assertRaisesRegex(TypeError, "encoding"):
            data = u16.read_text("utf-8", encoding="utf-8")

        # both keyword arguments work.
        with u16.open("r", encoding="utf-8", errors="strict") as f:
            # error during decoding with wrong codec.
            with self.assertRaises(UnicodeDecodeError):
                f.read()

    @unittest.skipIf(
        not getattr(sys.flags, 'warn_default_encoding', 0),
        "Requires warn_default_encoding",
    )
    @pass_alpharep
    def test_encoding_warnings(self, alpharep):
        """EncodingWarning must blame the read_text and open calls."""
        assert sys.flags.warn_default_encoding
        root = zipp.Path(alpharep)
        with self.assertWarns(EncodingWarning) as wc:
            root.joinpath("a.txt").read_text()
        assert __file__ == wc.filename
        with self.assertWarns(EncodingWarning) as wc:
            root.joinpath("a.txt").open("r").close()
        assert __file__ == wc.filename

    def test_open_write(self):
        """
        If the zipfile is open for write, it should be possible to
        write bytes or text to it.
        """
        zf = zipp.Path(zipfile.ZipFile(io.BytesIO(), mode='w'))
        with zf.joinpath('file.bin').open('wb') as strm:
            strm.write(b'binary contents')
        with zf.joinpath('file.txt').open('w', encoding="utf-8") as strm:
            strm.write('text file')

    def test_open_extant_directory(self):
        """
        Attempting to open a directory raises IsADirectoryError.
        """
        zf = zipp.Path(add_dirs(build_alpharep_fixture()))
        with self.assertRaises(IsADirectoryError):
            zf.joinpath('b').open()

    @pass_alpharep
    def test_open_binary_invalid_args(self, alpharep):
        root = zipp.Path(alpharep)
        with self.assertRaises(ValueError):
            root.joinpath('a.txt').open('rb', encoding='utf-8')
        with self.assertRaises(ValueError):
            root.joinpath('a.txt').open('rb', 'utf-8')

    def test_open_missing_directory(self):
        """
        Attempting to open a missing directory raises FileNotFoundError.
        """
        zf = zipp.Path(add_dirs(build_alpharep_fixture()))
        with self.assertRaises(FileNotFoundError):
            zf.joinpath('z').open()

    @pass_alpharep
    def test_read(self, alpharep):
        root = zipp.Path(alpharep)
        a, b, g, j = root.iterdir()
        assert a.read_text(encoding="utf-8") == "content of a"
        # Also check positional encoding arg (gh-101144).
        assert a.read_text("utf-8") == "content of a"
        assert a.read_bytes() == b"content of a"

    @pass_alpharep
    def test_joinpath(self, alpharep):
        root = zipp.Path(alpharep)
        a = root.joinpath("a.txt")
        assert a.is_file()
        e = root.joinpath("b").joinpath("d").joinpath("e.txt")
        assert e.read_text(encoding="utf-8") == "content of e"

    @pass_alpharep
    def test_joinpath_multiple(self, alpharep):
        root = zipp.Path(alpharep)
        e = root.joinpath("b", "d", "e.txt")
        assert e.read_text(encoding="utf-8") == "content of e"

    @pass_alpharep
    def test_traverse_truediv(self, alpharep):
        root = zipp.Path(alpharep)
        a = root / "a.txt"
        assert a.is_file()
        e = root / "b" / "d" / "e.txt"
        assert e.read_text(encoding="utf-8") == "content of e"

    @pass_alpharep
    def test_pathlike_construction(self, alpharep):
        """
        zipp.Path should be constructable from a path-like object
        """
        zipfile_ondisk = self.zipfile_ondisk(alpharep)
        pathlike = pathlib.Path(str(zipfile_ondisk))
        zipp.Path(pathlike)

    @pass_alpharep
    def test_traverse_pathlike(self, alpharep):
        root = zipp.Path(alpharep)
        root / pathlib.Path("a")

    @pass_alpharep
    def test_parent(self, alpharep):
        root = zipp.Path(alpharep)
        assert (root / 'a').parent.at == ''
        assert (root / 'a' / 'b').parent.at == 'a/'

    @pass_alpharep
    def test_dir_parent(self, alpharep):
        root = zipp.Path(alpharep)
        assert (root / 'b').parent.at == ''
        assert (root / 'b/').parent.at == ''

    @pass_alpharep
    def test_missing_dir_parent(self, alpharep):
        root = zipp.Path(alpharep)
        assert (root / 'missing dir/').parent.at == ''

    @pass_alpharep
    def test_mutability(self, alpharep):
        """
        If the underlying zipfile is changed, the Path object should
        reflect that change.
        """
        root = zipp.Path(alpharep)
        a, b, g, j = root.iterdir()
        alpharep.writestr('foo.txt', 'foo')
        alpharep.writestr('bar/baz.txt', 'baz')
        assert any(child.name == 'foo.txt' for child in root.iterdir())
        assert (root / 'foo.txt').read_text(encoding="utf-8") == 'foo'
        (baz,) = (root / 'bar').iterdir()
        assert baz.read_text(encoding="utf-8") == 'baz'

    HUGE_ZIPFILE_NUM_ENTRIES = 2**13

    def huge_zipfile(self):
        """Create a read-only zipfile with a huge number of entries entries."""
        strm = io.BytesIO()
        zf = zipfile.ZipFile(strm, "w")
        for entry in map(str, range(self.HUGE_ZIPFILE_NUM_ENTRIES)):
            zf.writestr(entry, entry)
        zf.mode = 'r'
        return zf

    def test_joinpath_constant_time(self):
        """
        Ensure joinpath on items in zipfile is linear time.
        """
        root = zipp.Path(self.huge_zipfile())
        entries = jaraco.itertools.Counter(root.iterdir())
        for entry in entries:
            entry.joinpath('suffix')
        # Check the file iterated all items
        assert entries.count == self.HUGE_ZIPFILE_NUM_ENTRIES

    @pass_alpharep
    def test_read_does_not_close(self, alpharep):
        alpharep = self.zipfile_ondisk(alpharep)
        with zipfile.ZipFile(alpharep) as file:
            for rep in range(2):
                zipp.Path(file, 'a.txt').read_text(encoding="utf-8")

    @pass_alpharep
    def test_subclass(self, alpharep):
        class Subclass(zipp.Path):
            pass

        root = Subclass(alpharep)
        assert isinstance(root / 'b', Subclass)

    @pass_alpharep
    def test_filename(self, alpharep):
        root = zipp.Path(alpharep)
        assert root.filename == pathlib.Path('alpharep.zip')

    @pass_alpharep
    def test_root_name(self, alpharep):
        """
        The name of the root should be the name of the zipfile
        """
        root = zipp.Path(alpharep)
        assert root.name == 'alpharep.zip' == root.filename.name

    @pass_alpharep
    def test_suffix(self, alpharep):
        """
        The suffix of the root should be the suffix of the zipfile.
        The suffix of each nested file is the final component's last suffix, if any.
        Includes the leading period, just like pathlib.Path.
        """
        root = zipp.Path(alpharep)
        assert root.suffix == '.zip' == root.filename.suffix

        b = root / "b.txt"
        assert b.suffix == ".txt"

        c = root / "c" / "filename.tar.gz"
        assert c.suffix == ".gz"

        d = root / "d"
        assert d.suffix == ""

    @pass_alpharep
    def test_suffixes(self, alpharep):
        """
        The suffix of the root should be the suffix of the zipfile.
        The suffix of each nested file is the final component's last suffix, if any.
        Includes the leading period, just like pathlib.Path.
        """
        root = zipp.Path(alpharep)
        assert root.suffixes == ['.zip'] == root.filename.suffixes

        b = root / 'b.txt'
        assert b.suffixes == ['.txt']

        c = root / 'c' / 'filename.tar.gz'
        assert c.suffixes == ['.tar', '.gz']

        d = root / 'd'
        assert d.suffixes == []

        e = root / '.hgrc'
        assert e.suffixes == []

    @pass_alpharep
    def test_suffix_no_filename(self, alpharep):
        alpharep.filename = None
        root = zipp.Path(alpharep)
        assert root.joinpath('example').suffix == ""
        assert root.joinpath('example').suffixes == []

    @pass_alpharep
    def test_stem(self, alpharep):
        """
        The final path component, without its suffix
        """
        root = zipp.Path(alpharep)
        assert root.stem == 'alpharep' == root.filename.stem

        b = root / "b.txt"
        assert b.stem == "b"

        c = root / "c" / "filename.tar.gz"
        assert c.stem == "filename.tar"

        d = root / "d"
        assert d.stem == "d"

        assert (root / ".gitignore").stem == ".gitignore"

    @pass_alpharep
    def test_root_parent(self, alpharep):
        root = zipp.Path(alpharep)
        assert root.parent == pathlib.Path('.')
        root.root.filename = 'foo/bar.zip'
        assert root.parent == pathlib.Path('foo')

    @pass_alpharep
    def test_root_unnamed(self, alpharep):
        """
        It is an error to attempt to get the name
        or parent of an unnamed zipfile.
        """
        alpharep.filename = None
        root = zipp.Path(alpharep)
        with self.assertRaises(TypeError):
            root.name
        with self.assertRaises(TypeError):
            root.parent

        # .name and .parent should still work on subs
        sub = root / "b"
        assert sub.name == "b"
        assert sub.parent

    @pass_alpharep
    def test_match_and_glob(self, alpharep):
        root = zipp.Path(alpharep)
        assert not root.match("*.txt")

        assert list(root.glob("b/c.*")) == [zipp.Path(alpharep, "b/c.txt")]
        assert list(root.glob("b/*.txt")) == [
            zipp.Path(alpharep, "b/c.txt"),
            zipp.Path(alpharep, "b/f.txt"),
        ]

    @pass_alpharep
    def test_glob_recursive(self, alpharep):
        root = zipp.Path(alpharep)
        files = root.glob("**/*.txt")
        assert all(each.match("*.txt") for each in files)

        assert list(root.glob("**/*.txt")) == list(root.rglob("*.txt"))

    @pass_alpharep
    def test_glob_subdirs(self, alpharep):
        root = zipp.Path(alpharep)

        assert list(root.glob("*/i.txt")) == []
        assert list(root.rglob("*/i.txt")) == [zipp.Path(alpharep, "g/h/i.txt")]

    @pass_alpharep
    def test_glob_does_not_overmatch_dot(self, alpharep):
        root = zipp.Path(alpharep)

        assert list(root.glob("*.xt")) == []

    @pass_alpharep
    def test_glob_single_char(self, alpharep):
        root = zipp.Path(alpharep)

        assert list(root.glob("a?txt")) == [zipp.Path(alpharep, "a.txt")]
        assert list(root.glob("a[.]txt")) == [zipp.Path(alpharep, "a.txt")]
        assert list(root.glob("a[?]txt")) == []

    @pass_alpharep
    def test_glob_chars(self, alpharep):
        root = zipp.Path(alpharep)

        assert list(root.glob("j/?.b[ai][nz]")) == [
            zipp.Path(alpharep, "j/k.bin"),
            zipp.Path(alpharep, "j/l.baz"),
        ]

    def test_glob_empty(self):
        root = zipp.Path(zipfile.ZipFile(io.BytesIO(), 'w'))
        with self.assertRaises(ValueError):
            root.glob('')

    @pass_alpharep
    def test_eq_hash(self, alpharep):
        root = zipp.Path(alpharep)
        assert root == zipp.Path(alpharep)

        assert root != (root / "a.txt")
        assert (root / "a.txt") == (root / "a.txt")

        root = zipp.Path(alpharep)
        assert root in {root}

    @pass_alpharep
    def test_is_symlink(self, alpharep):
        """
        See python/cpython#82102 for symlink support beyond this object.
        """

        root = zipp.Path(alpharep)
        assert not root.is_symlink()

    @pass_alpharep
    def test_relative_to(self, alpharep):
        root = zipp.Path(alpharep)
        relative = root.joinpath("b", "c.txt").relative_to(root / "b")
        assert str(relative) == "c.txt"

        relative = root.joinpath("b", "d", "e.txt").relative_to(root / "b")
        assert str(relative) == "d/e.txt"

    @pass_alpharep
    def test_inheritance(self, alpharep):
        cls = type('PathChild', (zipp.Path,), {})
        file = cls(alpharep).joinpath('some dir').parent
        assert isinstance(file, cls)

    @parameterize(
        ['alpharep', 'path_type', 'subpath'],
        itertools.product(
            alpharep_generators,
            [str, pathlib.Path],
            ['', 'b/'],
        ),
    )
    def test_pickle(self, alpharep, path_type, subpath):
        zipfile_ondisk = path_type(self.zipfile_ondisk(alpharep))

        saved_1 = pickle.dumps(zipp.Path(zipfile_ondisk, at=subpath))
        restored_1 = pickle.loads(saved_1)
        first, *rest = restored_1.iterdir()
        assert first.read_text(encoding='utf-8').startswith('content of ')

    @pass_alpharep
    def test_extract_orig_with_implied_dirs(self, alpharep):
        """
        A zip file wrapped in a Path should extract even with implied dirs.
        """
        source_path = self.zipfile_ondisk(alpharep)
        zf = zipfile.ZipFile(source_path)
        # wrap the zipfile for its side effect
        zipp.Path(zf)
        zf.extractall(source_path.parent)

    @pass_alpharep
    def test_getinfo_missing(self, alpharep):
        """
        Validate behavior of getinfo on original zipfile after wrapping.
        """
        zipp.Path(alpharep)
        with self.assertRaises(KeyError):
            alpharep.getinfo('does-not-exist')
