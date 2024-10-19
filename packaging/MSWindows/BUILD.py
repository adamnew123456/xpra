#!/bin/python
# -*- coding: utf-8 -*-
# This file is part of Xpra.
# Copyright (C) 2024 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import sys
import os.path
import shlex
from datetime import datetime
from collections import namedtuple
from collections.abc import Iterable
from importlib.util import find_spec, spec_from_file_location, module_from_spec

from glob import glob
from subprocess import getstatusoutput, check_output, Popen, PIPE
from shutil import which, rmtree, copyfile, move, copytree

KEY_FILE = "E:\\xpra.pfx"
DIST = "dist"
LIB_DIR = f"{DIST}/lib"

DEBUG = os.environ.get("XPRA_DEBUG", "0") != "0"
PYTHON = os.environ.get("PYTHON", "python3")
MINGW_PREFIX = os.environ.get("MINGW_PREFIX", "")
TIMESTAMP_SERVER = "http://timestamp.digicert.com"
# alternative:
# http://timestamp.comodoca.com/authenticode

PROGRAMFILES = "C:\\Program Files"
PROGRAMFILES_X86 = "C:\\Program Files (x86)"
SYSTEM32 = "C:\\Windows\\System32"

BUILD_INFO = "xpra/build_info.py"

LOG_DIR = "packaging/MSWindows/"

NPROCS = int(os.environ.get("NPROCS", os.cpu_count()))

BUILD_CUDA_KERNEL = "packaging\\MSWindows\\BUILD_CUDA_KERNEL.BAT"


def parse_command_line():
    from argparse import ArgumentParser, BooleanOptionalAction
    ap = ArgumentParser()

    # noinspection PyShadowingBuiltins
    def add(name: str, help: str, default=True):
        ap.add_argument(f"--{name}", default=default, action=BooleanOptionalAction, help=help)
    add("verbose", help="print extra diagnostic messages", default=False)
    add("clean", help="clean build directories")
    add("build", help="compile the source")
    add("install", help="run install step")
    add("fixups", help="run misc fixups")
    add("zip", help="generate a ZIP installation file")
    add("verpatch", help="run `verpatch` on the executables")
    add("light", help="trimmed down build")
    add("installer", help="create an EXE installer")
    add("run", help="run the installer")
    add("msi", help="create an MSI installer")
    add("sign", help="sign the EXE and MSI installers")
    add("tests", help="run the unit tests", default=False)
    add("zip-modules", help="zip up python modules")
    add("cuda", help="build CUDA kernels for nvidia codecs")
    add("service", help="build the system service")
    add("docs", help="generate the documentation")
    add("html5", help="bundle the `xpra-html5` client")
    add("manual", help="bundle the user manual")
    add("numpy", help="bundle `numpy`")
    add("putty", help="bundle putty `plink`")
    add("openssh", help="bundle the openssh client")
    add("openssl", help="bundle the openssl tools")
    add("paexec", help="bundle `paexec`")
    add("desktop-logon", help="build `desktop-logon` tool")

    args = ap.parse_args()
    if args.light:
        # disable many switches:
        args.cuda = args.numpy = args.service = args.docs = args.html5 = args.manual = False
        args.putty = args.openssh = args.openssl = args.paexec = args.desktop_logon = False
    if args.verbose:
        global DEBUG
        DEBUG = True
    return args


def step(message: str) -> None:
    now = datetime.now()
    ts = f"{now.hour:02}:{now.minute:02}:{now.second:02}"
    print(f"* {ts} {message}")


def debug(message: str) -> None:
    if DEBUG:
        print("    "+message)


def csv(values: Iterable) -> str:
    return ", ".join(str(x) for x in values)


def get_build_args(args) -> list[str]:
    xpra_args = []
    if args.light:
        for option in (
            "shadow", "server", "proxy", "rfb",
            "dbus",
            "encoders", "avif", "gstreamer_video",
            "nvfbc", "cuda_kernels",
            "csc_cython",
            "webcam",
            "win32_tools",
            "docs",
            "qt6_client",
        ):
            xpra_args.append(f"--without-{option}")
        xpra_args.append("--with-Os")
    else:
        xpra_args.append("--with-qt6_client")
    if not args.cuda:
        xpra_args.append("--without-nvidia")
    # we can't do 'docs' this way :(
    # for arg in ("docs", ):
    #    value = getattr(args, arg)
    #    xpra_args.append(f"--with-{arg}={value}")       #ie: "--with-docs=True"
    return xpra_args


def _find_command(name: str, env_name: str, *paths) -> str:
    cmd = os.environ.get(env_name, "")
    if cmd and os.path.exists(cmd):
        return cmd
    cmd = which(name)
    if cmd and os.path.exists(cmd):
        return cmd
    for path in paths:
        if os.path.exists(path) and os.path.isfile(path):
            return path
    return ""


def find_command(name: str, env_name: str, *paths) -> str:
    cmd = _find_command(name, env_name, *paths)
    if cmd:
        return cmd
    print(f"{name!r} not found")
    print(f" (you can set the {env_name!r} environment variable to point to it)")
    print(f" tried %PATH%={os.environ.get('PATH')}")
    print(f" tried {paths=}")
    raise RuntimeError(f"{name!r} not found")


def search_command(wholename: str, *dirs: str) -> str:
    debug(f"searching for {wholename!r} in {dirs}")
    for dirname in dirs:
        if not os.path.exists(dirname):
            continue
        cmd = ["find", dirname, "-wholename", wholename]
        r, output = getstatusoutput(cmd)
        debug(f"getstatusoutput({cmd})={r}, {output}")
        if r == 0:
            return output.splitlines()[0]
    raise RuntimeError(f"{wholename!r} not found in {dirs}")


def find_java() -> str:
    try:
        return _find_command("java", "JAVA")
    except RuntimeError as e:
        debug(f"`java` was not found: {e}")
    # try my hard-coded default first to save time:
    java = f"{PROGRAMFILES}\\Java\\jdk1.8.0_121\\bin\\java.exe"
    if java and getstatusoutput(f"{java} --version")[0] == 0:
        return java
    dirs = (f"{PROGRAMFILES}/Java", f"{PROGRAMFILES}", f"{PROGRAMFILES_X86}")
    for directory in dirs:
        r, output = getstatusoutput(f"find {directory!r} -name java.exe")
        if r == 0:
            return output[0]
    raise RuntimeError(f"java.exe was not found in {dirs}")


def check_html5() -> None:
    step("Verify `xpra-html5` is installed")
    if not os.path.exists("xpra-html5") or not os.path.isdir("xpra-html5"):
        print("html5 client not found")
        print(" perhaps run: `git clone https://github.com/Xpra-org/xpra-html5`")
        raise RuntimeError("`xpra-html5` client not found")

    # Find a java interpreter we can use for the html5 minifier
    os.environ["JAVA"] = find_java()


def check_signtool() -> None:
    step("locating `signtool`")
    try:
        signtool = find_command("signtool", "SIGNTOOL",
                                "./signtool.exe",
                                f"{PROGRAMFILES}\\Microsoft SDKs\\Windows\\v7.1\\Bin\\signtool.exe"
                                f"{PROGRAMFILES}\\Microsoft SDKs\\Windows\\v7.1A\\Bin\\signtool.exe"
                                f"{PROGRAMFILES_X86}\\Windows Kits\\8.1\\Bin\\x64\\signtool.exe"
                                f"{PROGRAMFILES_X86}\\Windows Kits\\10\\App Certification Kit\\signtool.exe")
    except RuntimeError:
        signtool = ""
    if not signtool:
        # try the hard (slow) way:
        signtool = find_vs_command("signtool.exe")
        if not signtool:
            raise RuntimeError("signtool not found")
    debug(f"{signtool=}")
    if signtool.lower() != "./signtool.exe":
        copyfile(signtool, "./signtool.exe")


def show_tail(filename: str) -> None:
    if os.path.exists(filename):
        print(f"showing the last 10 lines of {filename!r}:")
        os.system(f"tail -n 10 {filename}")


def command_args(cmd: str | list[str]) -> list[str]:
    # make sure we use an absolute path for the command:
    if isinstance(cmd, str):
        parts = shlex.split(cmd)
    else:
        parts = cmd
    cmd_exe = parts[0]
    if not os.path.isabs(cmd_exe):
        cmd_exe = which(cmd_exe)
        if cmd_exe:
            parts[0] = cmd_exe
    return parts


def log_command(cmd: str | list[str], log_filename: str, **kwargs) -> None:
    debug(f"running {cmd!r} and sending the output to {log_filename!r}")
    if not os.path.isabs(log_filename):
        log_filename = os.path.join(LOG_DIR, log_filename)
    delfile(log_filename)
    if not kwargs.get("shell"):
        cmd = command_args(cmd)
    with open(log_filename, "w") as f:
        ret = Popen(cmd, stdout=f, stderr=f, **kwargs).wait()
    if ret != 0:
        show_tail(log_filename)
        raise RuntimeError(f"{cmd!r} failed and returned {ret}, see {log_filename!r}")


def find_delete(path: str, name: str, mindepth=0) -> None:
    debug(f"deleting all instances of {name!r} from {path!r}")
    cmd = ["find", path]
    if mindepth > 0:
        cmd += ["-mindepth", str(mindepth)]
    if name:
        cmd += ["-name", "'"+name+"'"]
    cmd += ["-type", "f"]
    cmd = command_args(cmd)
    output = check_output(cmd)
    for filename in output.splitlines():
        delfile(filename)


def rmrf(path: str) -> None:
    if not os.path.exists(path):
        print(f"Warning: {path!r} does not exist")
        return
    rmtree(path)


def delfile(path: str) -> None:
    if os.path.exists(path):
        debug(f"removing {path!r}")
        os.unlink(path)


def clean() -> None:
    step("Cleaning output directories and generated files")
    debug("cleaning log files:")
    find_delete("packaging/MSWindows/", "*.log")
    for dirname in (DIST, "build"):
        rmrf(dirname)
        os.mkdir(dirname)
    # clean sometimes errors on removing pyd files,
    # so do it with rm instead:
    debug("removing compiled dll and pyd files:")
    find_delete("xpra", "*-cpython-*dll")
    find_delete("xpra", "*-cpython-*pyd")
    debug("python clean")
    log_command(f"{PYTHON} ./setup.py clean", "clean.log")
    debug("removing comtypes cache")
    # clean comtypes cache - it should not be included!
    check_output(command_args("clear_comtypes_cache.exe -y"))
    debug("ensure build info is regenerated")
    delfile(BUILD_INFO)


def find_wk_command(name="mc") -> str:
    # the proper way would be to run vsvars64.bat
    # but we only want to locate 3 commands,
    # so we find them "by hand":
    ARCH_DIRS = ("x64", "x86")
    paths = []
    for prog_dir in (PROGRAMFILES, PROGRAMFILES_X86):
        for V in (8.1, 10):
            for ARCH in ARCH_DIRS:
                paths += glob(f"{prog_dir}\\Windows Kits\\{V}\\bin\\*\\{ARCH}\\{name}.exe")
    env_name = name.upper()   # ie: "MC"
    return find_command(name, env_name, *paths)


def find_vs_command(name="link") -> str:
    debug(f"find_vs_command({name})")
    dirs = []
    for prog_dir in (PROGRAMFILES, PROGRAMFILES_X86):
        for VSV in (14.0, 17.0, 19.0, 2019):
            vsdir = f"{prog_dir}\\Microsoft Visual Studio\\{VSV}"
            if os.path.exists(vsdir):
                dirs.append(f"{vsdir}\\VC\\bin")
                dirs.append(f"{vsdir}\\BuildTools\\VC\\Tools\\MSVC")
    return search_command(f"*/x64/{name}.exe", *dirs)


def build_service() -> None:
    step("* Compiling system service shim")
    XPRA_SERVICE_EXE = "Xpra-Service.exe"
    delfile(XPRA_SERVICE_EXE)
    SERVICE_SRC_DIR = os.path.join(os.path.abspath("."), "packaging", "MSWindows", "service")
    for filename in ("event_log.rc", "event_log.res", "MSG00409.bin", "Xpra-Service.exe"):
        path = os.path.join(SERVICE_SRC_DIR, filename)
        delfile(path)

    MC = find_wk_command("mc")
    RC = find_wk_command("rc")
    LINK = find_vs_command("link")

    log_command([MC, "-U", "event_log.mc"], "service-mc.log", cwd=SERVICE_SRC_DIR)
    log_command([RC, "event_log.rc"], "service-rc.log", cwd=SERVICE_SRC_DIR)
    log_command([LINK, "-dll", "-noentry", "-out:event_log.dll", "event_log.res"], "service-link.log",
                cwd=SERVICE_SRC_DIR)
    log_command(["g++", "-o", XPRA_SERVICE_EXE, "Xpra-Service.cpp", "-Wno-write-strings"], "service-gcc.log",
                cwd=SERVICE_SRC_DIR)
    os.rename(os.path.join(SERVICE_SRC_DIR, XPRA_SERVICE_EXE), XPRA_SERVICE_EXE)


VersionInfo = namedtuple("VersionInfo", "string,value,revision,full_string,arch_info,extra,padded")
version_info = VersionInfo("invalid", (0, 0), 0, "invalid", "arch", "extra", (0, 0, 0, 0))


def set_version_info(light: bool):
    step("Collecting version information")
    for filename in ("src_info.py", "build_info.py"):
        path = os.path.join("xpra", filename)
        delfile(path)
    log_command([PYTHON, "fs/bin/add_build_info.py", "src", "build"], "add-build-info.log")
    print("    Python " + sys.version)

    def load_module(src: str):
        spec = spec_from_file_location("xpra", src)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    xpra = load_module("xpra/__init__.py")
    src_info = load_module("xpra/src_info.py")

    revision = src_info.REVISION

    full_string = f"{xpra.__version__}-r{revision}"
    if src_info.LOCAL_MODIFICATIONS:
        full_string += "M"

    extra = "-Light" if light else ""
    # ie: "x86_64"
    arch_info = "-" + os.environ.get("MSYSTEM_CARCH", "")

    # for msi and verpatch:
    padded = (list(xpra.__version_info__) + [0, 0, 0])[:3] + [revision]
    padded = ".".join(str(x) for x in padded)

    print(f"    Xpra{extra} {full_string}")
    print(f"    using {NPROCS} cpus")
    global version_info
    version_info = VersionInfo(xpra.__version__, xpra.__version_info__, revision, full_string, arch_info, extra, padded)


################################################################################
# Build: clean, build extensions, generate exe directory


def build_cuda_kernels() -> None:
    step("Building CUDA kernels")
    for cupath in glob("fs/share/xpra/cuda/*.cu"):
        kname = os.path.splitext(os.path.basename(cupath))[0]
        cu = os.path.splitext(cupath)[0]
        # ie: "fs/share/xpra/cuda/BGRX_to_NV12.cu" -> "fs/share/xpra/cuda/BGRX_to_NV12"
        fatbin = f"{cu}.fatbin"
        if not os.path.exists(fatbin):
            debug(f"rebuilding {kname!r}: {fatbin!r} does not exist")
        else:
            ftime = os.path.getctime(fatbin)
            ctime = os.path.getctime(cupath)
            if ftime >= ctime:
                debug(f"{fatbin!r} ({ftime}) is already newer than {cupath!r} ({ctime})")
                continue
            debug(f"need to rebuild: {fatbin!r} ({ftime}) is older than {cupath!r} ({ctime})")
            os.unlink(fatbin)
        log_command([BUILD_CUDA_KERNEL, kname], f"nvcc-{kname}.log")


def build_ext(args) -> None:
    step("Building Cython modules")
    build_args = get_build_args(args) + ["--inplace"]
    if NPROCS > 0:
        build_args += ["-j", str(NPROCS)]
    args_str = " ".join(build_args)
    log_command(f"{PYTHON} ./setup.py build_ext {args_str}", "build.log")


def run_tests() -> None:
    step("Running unit tests")
    env = os.environ.copy()
    env["PYTHONPATH"] = ".:./tests/unittests"
    env["XPRA_COMMAND"] = "./fs/bin/xpra"
    log_command(f"{PYTHON} ./setup.py unittests", "unittest.log", env=env)


def install_exe(args) -> None:
    step("Generating installation directory")
    args_str = " ".join(get_build_args(args))
    log_command(f"{PYTHON} ./setup.py install_exe {args_str} --install={DIST}", "install.log")


def install_docs() -> None:
    step("Generating the documentation")
    if not os.path.exists(f"{DIST}/doc"):
        os.mkdir(f"{DIST}/doc")
    env = os.environ.copy()
    env["PANDOC"] = find_command("pandoc", "PANDOC", f"{PROGRAMFILES}\\Pandoc\\pandoc.exe")
    log_command(f"{PYTHON} ./setup.py doc", "pandoc.log", env=env)


def fixups(light: bool) -> None:
    step("Fixups: paths, etc")
    # fix case sensitive mess:
    gi_dir = f"{LIB_DIR}/girepository-1.0"
    debug("Glib misspelt")
    os.rename(f"{gi_dir}/Glib-2.0.typelib", f"{gi_dir}/GLib-2.0.typelib.tmp")
    os.rename(f"{gi_dir}/GLib-2.0.typelib.tmp", f"{gi_dir}/GLib-2.0.typelib")

    debug("cx_Logging")
    # fixup cx_Logging, required by the service class before we can patch sys.path to find it:
    if os.path.exists(f"{LIB_DIR}/cx_Logging.pyd"):
        os.rename(f"{LIB_DIR}/cx_Logging.pyd", f"{DIST}/cx_Logging.pyd")
    debug("comtypes")
    # fixup cx freeze wrongly including an empty dir:
    gen = f"{LIB_DIR}/comtypes/gen"
    if os.path.exists(gen):
        rmrf(gen)
    debug("gdk loaders")
    if light:
        lpath = os.path.join(LIB_DIR, "gdk-pixbuf-2.0", "2.10.0", "loaders")
        KEEP_LOADERS = ("jpeg", "png", "xpm", "svg", "wmf")
        for filename in os.listdir(lpath):
            if not any(filename.find(keep) for keep in KEEP_LOADERS):
                debug(f"removing {filename!r}")
                os.unlink(os.path.join(lpath, filename))
    debug("remove ffmpeg libraries")
    for libname in ("avcodec", "avformat", "avutil", "swscale", "swresample", "zlib1", "xvidcore"):
        find_delete(LIB_DIR, libname)
    debug("move lz4")


def add_numpy(bundle: bool) -> None:
    step(f"numpy: {bundle}")
    lib_numpy = f"{LIB_DIR}/numpy"
    if not bundle:
        debug("removed")
        rmrf(f"{lib_numpy}")
        delete_libs("libopenblas*", "libgfortran*", "libquadmath*")
        return
    debug("moving libraries to lib")
    for libname in ("openblas", "gfortran", "quadmath"):
        for dll in glob(f"{lib_numpy}/core/lib{libname}*.dll"):
            move(dll, LIB_DIR)
    debug("trim tests")


def move_lib(frompath: str, todir: str) -> None:
    topath = os.path.join(todir, os.path.basename(frompath))
    if os.path.exists(topath):
        # should compare that they are the same file!
        debug(f"removing {frompath!r}, already found in {topath!r}")
        os.unlink(frompath)
        return
    debug(f"moving {frompath!r} to {topath!r}")
    move(frompath, topath)


def fixup_gstreamer() -> None:
    step("Fixup GStreamer")
    lib_gst = f"{LIB_DIR}/gstreamer-1.0"
    # these are not modules, so they belong in "lib/":
    for dllname in ("gstreamer*", "gst*-1.0-*", "wavpack*", "*-?"):
        for gstdll in glob(f"{lib_gst}/lib{dllname}.dll"):
            move_lib(gstdll, f"{LIB_DIR}")
    # all the remaining libgst* DLLs are gstreamer elements:
    for gstdll in glob(f"{LIB_DIR}gst*.dll"):
        move(gstdll, lib_gst)
    # these are not needed at all for now:
    for elementname in ("basecamerabinsrc", "photography"):
        for filename in glob(f"{LIB_DIR}/libgst{elementname}*"):
            os.unlink(filename)


def fixup_dlls() -> None:
    step("Fixup DLLs")
    debug("remove dll.a")
    # why is it shipping those files??
    find_delete(DIST, "*dll.a")
    debug("moving most DLLs to lib/")
    # but keep the core DLLs in the root (python, gcc, etc):
    exclude = ("msvcrt", "libpython", "libgcc", "libwinpthread", "pdfium")
    for dll in glob(f"{DIST}/*.dll"):
        if any(dll.find(excl) >= 0 for excl in exclude):
            continue
        move_lib(dll, LIB_DIR)
    debug("fixing cx_Freeze duplication")
    # remove all the pointless cx_Freeze duplication:
    for dll in glob(f"{DIST}/*.dll") + glob(f"{LIB_DIR}/*dll"):
        filename = os.path.basename(dll)
        # delete from any sub-directories:
        find_delete(LIB_DIR, filename, mindepth=2)


def delete_dist_files(*exps: str) -> None:
    for exp in exps:
        matches = glob(f"{DIST}/{exp}")
        if not matches:
            print(f"Warning: glob {exp!r} did not match any files!")
            continue
        for path in matches:
            if os.path.isdir(path):
                debug(f"removing tree at: {path!r}")
                rmtree(path)
            else:
                debug(f"removing {path!r}")
                os.unlink(path)


def delete_libs(*exps: str) -> None:
    delete_dist_files(*(f"lib/{exp}" for exp in exps))


def delete_dlls(light: bool) -> None:
    step("Deleting unnecessary DLLs")
    delete_libs(
        "libjasper*", "lib2to3*", "xdg*", "olefile*", "pygtkcompat*", "jaraco*",
        "p11-kit*", "lz4",
    )
    # remove codecs we don't need:
    delete_libs("libx265*", "libjxl*", "libde265*", "libkvazaar*")
    if light:
        delete_libs(
            # kerberos / gss libs:
            "libshishi*", "libgss*",
            # no dbus:
            "libdbus*",
            # no AV1:
            "libaom*", "rav1e*", "libdav1d*", "libheif*",
            # no avif:
            "libavif*", "libSvt*",
            # remove h264 encoder:
            "libx264*",
            # should not be needed:
            "libsqlite*", "libp11-kit*",
            # extra audio codecs (we just keep vorbis and opus):
            "libmp3*", "libwavpack*", "libmpdec*", "libFLAC*", "libmpg123*", "libfaad*", "libfaac*",
        )

        def delgst(*exps: str) -> None:
            gstlibs = tuple(f"gstreamer-1.0/libgst{exp}*" for exp in exps)
            delete_libs(*gstlibs)
        # matching gstreamer modules:
        delgst("flac", "wavpack", "wavenc", "lame", "mpg123", "faac", "faad", "wav")
        # these started causing packaging problems with GStreamer 1.24:
        delgst("isomp4")


def trim_pillow() -> None:
    # remove PIL loaders and modules we don't need:
    step("Removing unnecessary PIL plugins")
    KEEP = (
        "Bmp", "Ico", "Jpeg", "Tiff", "Png", "Ppm", "Xpm", "WebP",
        "Image.py", "ImageChops", "ImageCms", "ImageWin", "ImageChops", "ImageColor", "ImageDraw", "ImageFile.py",
        "ImageFilter", "ImageFont", "ImageGrab", "ImageMode", "ImageOps", "ImagePalette", "ImagePath", "ImageSequence",
        "ImageStat", "ImageTransform",
    )
    NO_KEEP = ("Jpeg2K", )
    kept = []
    removed = []
    for filename in glob(f"{LIB_DIR}/PIL/*Image*"):
        infoname = os.path.splitext(os.path.basename(filename))[0]
        if any(filename.find(keep) >= 0 for keep in KEEP) and not any(filename.find(nokeep) >= 0 for nokeep in NO_KEEP):
            kept.append(infoname)
            continue
        removed.append(infoname)
        os.unlink(filename)
    debug(f"removed: {csv(removed)}")
    debug(f"kept: {csv(kept)}")


def trim_python_libs() -> None:
    step("Removing unnecessary Python modules")
    # remove test bits we don't need:
    delete_libs(
        "pywin*",
        "win32com",
        "backports",
        "importlib_resources/compat",
        "importlib_resources/tests",
        "yaml",
        # no need for headers:
        # "cairo/include"
    )
    step("Removing unnecessary files")
    for ftype in (
        # no runtime type checks:
        "py.typed",
        # remove source:
        "*.bak",
        "*.orig",
        "*.pyx",
        "*.c",
        "*.cpp",
        "*.m",
        "constants.txt",
        "*.h",
        "*.html",
        "*.pxd",
        "*.cu",
    ):
        find_delete(DIST, ftype)


def fixup_zeroconf() -> None:
    lib_zeroconf = f"{LIB_DIR}/zeroconf"
    rmrf(lib_zeroconf)
    # workaround for zeroconf - just copy it wholesale
    # since I have no idea why cx_Freeze struggles with it:
    zc = find_spec("zeroconf")
    if not zc:
        print("Warning: zeroconf not found for Python %s" % sys.version)
        return
    zeroconf_dir = os.path.dirname(zc.origin)
    debug(f"adding zeroconf from {zeroconf_dir!r} to {lib_zeroconf!r}")
    copytree(zeroconf_dir, lib_zeroconf)


def rm_empty_dir(dirpath: str) -> None:
    cmd = ["find", dirpath, "-type", "d", "-empty"]
    output = check_output(command_args(cmd))
    for path in output.splitlines():
        os.rmdir(path)


def rm_empty_dirs() -> None:
    step("Removing empty directories")
    for _ in range(3):
        rm_empty_dir(DIST)


def zip_modules(light: bool) -> None:
    step("zipping up some Python modules")
    # these modules contain native code or data files,
    # so they will require special treatment:
    # xpra numpy cryptography PIL nacl cffi gtk gobject glib aioquic pylsqpack > /dev/null
    ZIPPED = [
        "OpenGL", "encodings", "future", "paramiko", "html",
        "pyasn1", "asn1crypto", "async_timeout",
        "certifi", "OpenSSL", "pkcs11", "keyring",
        "ifaddr", "pyaes", "browser_cookie3", "service_identity",
        "re", "platformdirs", "attr", "setproctitle", "pyvda", "zipp",
        "distutils", "comtypes", "email", "multiprocessing", "packaging",
        "pkg_resources", "pycparser", "idna", "ctypes", "json",
        "http", "enum", "winreg", "copyreg", "_thread", "_dummythread",
        "builtins", "importlib",
        "logging", "queue", "urllib", "xml", "xmlrpc", "pyasn1_modules",
        "concurrent", "collections",
    ]
    EXTRAS = ["asyncio", "unittest", "gssapi", "pynvml", "ldap", "ldap3", "pyu2f", "sqlite3", "psutil"]
    if light:
        delete_libs(*EXTRAS)
    else:
        ZIPPED += EXTRAS
    log_command(["zip", "--move", "-ur", "library.zip"] + ZIPPED, "zip.log", cwd=LIB_DIR)


def setup_share(light: bool) -> None:
    step("Deleting unnecessary `share/` files")
    delete_dist_files(
        "share/xml",
        "share/glib-2.0/codegen",
        "share/glib-2.0/gdb",
        "share/glib-2.0/gettext",
        "share/locale",
        "share/gstreamer-1.0",
        "share/gst-plugins-base",
        "share/p11-kit",
        "share/themes/*/gtk-2.0*",
    )
    if light:
        # remove extra bits that take up a lot of space:
        delete_dist_files(
            "share/icons/Adwaita/cursors",
            "share/fonts/gsfonts",
            "share/fonts/adobe*",
            "share/fonts/cantarell",
        )
    step("Removing empty icon directories")
    # remove empty icon directories
    for _ in range(4):
        rm_empty_dir(f"{DIST}/share/icons")


def add_manifests() -> None:
    step("Adding EXE manifests")
    EXES = [
        "Bug_Report", "Xpra-Launcher", "Xpra", "Xpra_cmd",
        # these are only included in full builds:
        "GTK_info", "NativeGUI_info", "Screenshot", "Xpra-Shadow",
    ]
    for exe in EXES:
        if os.path.exists(f"{DIST}/{exe}.exe"):
            copyfile("packaging/MSWindows/exe.manifest", f"{DIST}/{exe}.exe.manifest")


def gen_caches() -> None:
    step("Generating gdk pixbuf loaders cache")
    cmd = 'gdk-pixbuf-query-loaders.exe "lib/gdk-pixbuf-2.0/2.10.0/loaders/*"'
    with open(f"{LIB_DIR}/gdk-pixbuf-2.0/2.10.0/loaders.cache", "w") as cache:
        if Popen(cmd, cwd=os.path.abspath(DIST), stdout=cache, shell=True).wait() != 0:
            raise RuntimeError("gdk-pixbuf-query-loaders.exe failed")
    step("Generating icons and theme cache")
    for itheme in glob(f"{DIST}/share/icons/*"):
        log_command(["gtk-update-icon-cache.exe", "-t", "-i", itheme], "icon-cache.log")


def bundle_manual() -> None:
    step("Generating HTML Manual Page")
    manual = os.path.join(DIST, "manual.html")
    delfile(manual)
    with open("fs/share/man/man1/xpra.1", "rb") as f:
        man = f.read()
    proc = Popen(["groff", "-mandoc", "-Thtml"], stdin=PIPE, stdout=PIPE, stderr=PIPE)
    out, err = proc.communicate(man)
    if proc.returncode != 0:
        raise RuntimeError(f"groff failed and returned {proc.returncode}: {err!r}")
    debug(f"groff warnings: {err!r}")
    with open(manual, "wb") as manual_file:
        manual_file.write(out)


def bundle_html5() -> None:
    step("Installing the HTML5 client")
    www = os.path.join(os.path.abspath("."), DIST, "www")
    if not os.path.exists(www):
        os.mkdir(www)
    html5 = os.path.join(os.path.abspath("."), "xpra-html5")
    debug(f"running html5 install step in {html5!r}")
    log_command([PYTHON, "./setup.py", "install", www], "html5.log", cwd=html5)


def bundle_putty() -> None:
    step("Bundling TortoisePlink")
    tortoiseplink = find_command("TortoisePlink", "TORTOISEPLINK",
                                 f"{PROGRAMFILES}\\TortoiseSVN\\bin\\TortoisePlink.exe")
    copyfile(tortoiseplink, f"{DIST}/Plink.exe")
    for dll in ("vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"):
        copyfile(f"{SYSTEM32}/{dll}", f"{DIST}/{dll}")


def bundle_dlls(*expr: str) -> None:
    for exp in expr:
        matches = glob(f"{exp}.dll")
        if not matches:
            print(f"Warning: no dll matching {exp!r}")
            continue
        for match in matches:
            name = os.path.basename(match)
            copyfile(match, f"{DIST}/{name}")


def bundle_openssh() -> None:
    step("Bundling OpenSSH")
    for exe_name in ("ssh", "sshpass", "ssh-keygen"):
        exe = which(exe_name)
        if not exe:
            raise RuntimeError(f"{exe_name!r} not found!")
        copyfile(exe, f"{DIST}/{exe_name}.exe")
    bin_dir = os.path.dirname(which("ssh"))
    debug(f"looking for msys DLLs in {bin_dir!r}")
    msys_dlls = tuple(
        f"{bin_dir}/msys-{dllname}*" for dllname in (
            "2.0", "gcc_s", "crypto", "z", "gssapi", "asn1", "com_err", "roken",
            "crypt", "heimntlm", "krb5", "heimbase", "wind", "hx509", "hcrypto", "sqlite3",
        )
    )
    bundle_dlls(*msys_dlls)


def bundle_openssl() -> None:
    step("Bundling OpenSSL")
    copyfile(f"{MINGW_PREFIX}/bin/openssl.exe", f"{DIST}/openssl.exe")
    ssl_dir = f"{DIST}/etc/ssl"
    if not os.path.exists(ssl_dir):
        os.mkdir(ssl_dir)
    copyfile(f"{MINGW_PREFIX}/etc/ssl/openssl.cnf", f"{ssl_dir}/openssl.cnf")
    # we need those libraries at the top level:
    bundle_dlls(f"{LIB_DIR}/libssl-*", f"{LIB_DIR}/libcrypto-*")


def bundle_paxec() -> None:
    step("Bundling paexec")
    copyfile(f"{MINGW_PREFIX}/bin/paexec.exe", f"{DIST}/paexec.exe")


def bundle_desktop_logon() -> None:
    step("Bundling desktop_logon")
    dl_dlls = tuple(f"{MINGW_PREFIX}/bin/{dll}" for dll in ("AxMSTSCLib", "MSTSCLib", "DesktopLogon"))
    bundle_dlls(*dl_dlls)


def add_cuda(enabled: bool) -> None:
    step(f"cuda: {enabled}")
    if not enabled:
        delete_libs("pycuda*")
        find_delete(DIST, "pycuda*")
        find_delete(DIST, "libnv*")
        find_delete(DIST, "cuda.conf")
        delete_libs("curand*")
        cuda_dir = os.path.join(LIB_DIR, "cuda")
        if os.path.exists(cuda_dir):
            rmtree(cuda_dir)
        return
    # pycuda wants a CUDA_PATH with "/bin" in it:
    if not os.path.exists(f"{DIST}/bin"):
        os.mkdir(f"{DIST}/bin")
    # keep the cuda bits at the root:
    for nvdll in glob(f"{LIB_DIR}/libnv*.dll"):
        move_lib(nvdll, DIST)


def rec_options(args) -> None:
    info = dict((k, getattr(args, k)) for k in dir(args) if not k.startswith("_"))
    with open("xpra/build_info.py", "a") as f:
        f.write(f"\nBUILD_OPTIONS={info!r}\n")


def verpatch() -> None:
    EXCLUDE = ("plink", "openssh", "openssl", "paexec")

    def run_verpatch(filename: str, descr: str):
        log_command(["verpatch", filename,
                     "/s", "desc", descr,
                     "/va", version_info.padded,
                     "/s", "company", "xpra.org",
                     "/s", "copyright", "(c) xpra.org 2024",
                     "/s", "product", "xpra",
                     "/pv", version_info.padded,
                     ], "verpatch.log")

    for exe in glob(f"{DIST}/*.exe"):
        if any(exe.lower().find(excl) >= 0 for excl in EXCLUDE):
            continue
        exe_name = os.path.basename(exe)
        if exe_name in ("Xpra_cmd.exe", "Xpra.exe", "Xpra-Proxy.exe"):
            # handled separately below
            continue
        assert exe_name.endswith(".exe")
        tool_name = exe_name[:-3].replace("Xpra_", "").replace("_", " ").replace("-", " ")
        run_verpatch(exe, f"Xpra {tool_name}")
    run_verpatch(f"{DIST}/Xpra_cmd.exe", "Xpra command line")
    run_verpatch(f"{DIST}/Xpra.exe", "Xpra")
    if os.path.exists(f"{DIST}/Xpra-Proxy.exe"):
        run_verpatch(f"{DIST}/Xpra-Proxy.exe", "Xpra Proxy Server")


################################################################################
# packaging: ZIP / EXE / MSI

def create_zip() -> None:
    step("Creating ZIP file:")
    ZIP_DIR = f"Xpra{version_info.extra}{version_info.arch_info}_{version_info.full_string}"
    ZIP_FILENAME = f"{ZIP_DIR}.zip"
    if os.path.exists(ZIP_DIR):
        rmrf(ZIP_DIR)
    delfile(ZIP_FILENAME)
    copytree(DIST, ZIP_DIR)
    log_command(["zip", "-9mr", ZIP_FILENAME, ZIP_DIR], "zip.log")
    os.system(f"du -sm {ZIP_FILENAME!r}")


def create_installer(args) -> str:
    step("Creating the installer using InnoSetup")
    innosetup = find_command("innosetup", "INNOSETUP",
                             f"{PROGRAMFILES}\\Inno Setup 6\\ISCC.exe",
                             f"{PROGRAMFILES_X86}\\Inno Setup 6\\ISCC.exe",
                             )
    SETUP_EXE = f"{DIST}/Xpra_Setup.exe"
    INSTALLER_FILENAME = f"Xpra{version_info.extra}{version_info.arch_info}_Setup_{version_info.full_string}.exe"
    XPRA_ISS = "xpra.iss"
    INNOSETUP_LOG = "innosetup.log"
    for filename in (XPRA_ISS, INNOSETUP_LOG, INSTALLER_FILENAME, SETUP_EXE):
        delfile(filename)
    with open("packaging/MSWindows/xpra.iss", "r") as f:
        contents = f.readlines()
    lines = []
    subs = {
        "AppId": "Xpra_is1",
        "AppName": f"Xpra {version_info.string}",
        "UninstallDisplayName": f"Xpra {version_info.string}",
        "AppVersion": version_info.full_string,
    }
    for line in contents:
        if line.startswith("    PostInstall()") and args.light:
            # don't run post-install openssl:
            line = "Log('skipped post-install');"
        elif line.find("Xpra Shadow Server") >= 0 and args.light:
            # no shadow server in light builds
            continue
        elif line.find("Command Manual") >= 0 and not args.docs:
            # remove link to the manual:
            continue
        if line.find("=") > 0:
            parts = line.split("=", 1)
            if parts[0] in subs:
                line = parts[0] + "=" + subs[parts[0]]+"\n"
        lines.append(line)
    with open(XPRA_ISS, "w") as f:
        f.writelines(lines)

    log_command([innosetup, XPRA_ISS], INNOSETUP_LOG)
    os.unlink(XPRA_ISS)

    os.rename(SETUP_EXE, INSTALLER_FILENAME)
    print()
    os.system(f"du -sm {INSTALLER_FILENAME!r}")
    print()
    return INSTALLER_FILENAME


def sign_file(filename: str) -> None:
    log_command(["signtool.exe", "sign", "/v", "/f", KEY_FILE, "/t", TIMESTAMP_SERVER, filename], "signtool.log")


def create_msi(installer: str) -> str:
    msiwrapper = find_command("msiwrapper", "MSIWRAPPER",
                              f"{PROGRAMFILES}\\MSI Wrapper\\MsiWrapper.exe",
                              f"{PROGRAMFILES_X86}\\MSI Wrapper\\MsiWrapper.exe")
    MSI_FILENAME = f"Xpra{version_info.extra}{version_info.arch_info}_{version_info.full_string}.msi"
    # search and replace in the template file:
    subs: dict[str, str] = {
        "CWD": os.getcwd(),
        "INPUT": installer,
        "OUTPUT": MSI_FILENAME,
        "ZERO_PADDED_VERSION": version_info.padded,
        "FULL_VERSION": version_info.full_string,
    }
    with open("packaging\\MSWindows\\msi.xml", "r") as template:
        msi_data = template.read()
    for varname, value in subs.items():
        msi_data = msi_data.replace(f"${varname}", value)
    MSI_XML = "msi.xml"
    with open(MSI_XML, "w") as f:
        f.write(msi_data)
    log_command([msiwrapper, MSI_XML], "msiwrapper.log")
    os.system(f"du -sm {MSI_FILENAME}")
    return MSI_FILENAME


def build(args) -> None:
    set_version_info(args.light)
    if args.html5:
        check_html5()
    if args.sign:
        check_signtool()

    if args.clean:
        clean()
    if args.service:
        build_service()
    if args.cuda:
        build_cuda_kernels()
    if args.build:
        build_ext(args)
    if args.tests:
        run_tests()
    if args.install:
        install_exe(args)

    if args.fixups:
        fixups(args.light)
        fixup_gstreamer()
        fixup_dlls()
        delete_dlls(args.light)
        trim_python_libs()
        trim_pillow()
        fixup_zeroconf()
        rm_empty_dirs()

    add_cuda(args.cuda)
    add_numpy(args.numpy)

    if args.zip_modules:
        zip_modules(args.light)

    setup_share(args.light)
    add_manifests()
    gen_caches()

    if args.docs:
        bundle_manual()
        install_docs()
    if args.html5:
        bundle_html5()
    if args.putty:
        bundle_putty()
    if args.openssh:
        bundle_openssh()
    if args.openssl:
        bundle_openssl()
    if args.paexec:
        bundle_paxec()
    if args.desktop_logon:
        bundle_desktop_logon()
    rec_options(args)

    if args.verpatch:
        verpatch()

    os.system(f"du -sm {DIST!r}")
    if args.zip:
        create_zip()
    if args.installer:
        installer = create_installer(args)
        if args.sign:
            step("Signing EXE")
            sign_file(installer)
        if args.run:
            step("Running the new installer")
            os.system(installer)
        if args.msi:
            msi = create_msi(installer)
            if args.sign:
                step("Signing MSI")
                sign_file(msi)


def main():
    args = parse_command_line()
    build(args)


if __name__ == "__main__":
    main()
