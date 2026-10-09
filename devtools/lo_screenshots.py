#!/usr/bin/env python3
"""Screenshot a document page by page in the LibreOffice GUI (light or dark application colors).

Usage: lo_screenshots.py FILE OUTDIR [--mode light|dark] [--pages 1-5,9] [--zoom page|width|NN]
                                     [--show-changes yes|no] [--size WxH]

To use a different LibreOffice installation, run this script with that installation's Python
(e.g. /opt/libreoffice26.8/program/python lo_screenshots.py ...): the soffice binary next to the
imported uno module is used.

Runs LibreOffice under a private Xvfb server with its own throw-away user profile (so several
instances can run in parallel) and drives it through UNO: every requested page is scrolled
into view at "entire page" zoom (or the given zoom) and captured. "dark" mimics a typical
dark-mode setup: dark document background and a light automatic font color. Text with an
explicit (non-automatic) color keeps that color, which is what makes dark text unreadable.

Writes OUTDIR/page-001.png, ... and prints their paths (and the page count) to stdout.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time

SCHEME = "/org.openoffice.Office.UI/ColorScheme/ColorSchemes/org.openoffice.Office.UI:ColorScheme['LibreOffice']"


def item(path: str, prop: str, value: str) -> str:
    return f'<item oor:path="{path}"><prop oor:name="{prop}" oor:op="fuse"><value>{value}</value></prop></item>'


def make_profile(root: Path, mode: str) -> None:
    user = root / "user"
    user.mkdir(parents=True, exist_ok=True)
    items = [
        item("/org.openoffice.Setup/Office", "ooSetupInstCompleted", "true"),
        item("/org.openoffice.Setup/Office", "FirstStartWizardCompleted", "true"),
        item("/org.openoffice.Office.Common/Misc", "ShowTipOfTheDay", "false"),
        item("/org.openoffice.Office.Common/Misc", "FirstRun", "false"),
        item("/org.openoffice.Office.Common/Misc", "UseLocking", "false"),
        item("/org.openoffice.Office.Recovery/RecoveryInfo", "Enabled", "false"),
        item("/org.openoffice.Office.Recovery/AutoSave", "Enabled", "false"),
        item("/org.openoffice.Office.Common/Misc", "ShowLinkWarningDialog", "false"),
    ]
    if mode == "dark":
        items += [
            # LibreOffice 24.8 and later: dark application colors, and let them change the page color
            item("/org.openoffice.Office.Common/Appearance", "ApplicationAppearance", "2"),
            item("/org.openoffice.Office.Common/Appearance", "UseOnlyWhiteDocBackground", "false"),
            item("/org.openoffice.Office.Common/Misc", "ApplicationAppearance", "2"),  # (24.2 - 24.x)
            item(SCHEME + "/DocColor", "Color", str(0x1E1E1E)),
            item(SCHEME + "/AppBackground", "Color", str(0x333333)),
            item(SCHEME + "/FontColor", "Color", str(0xEEEEEE)),
            item(SCHEME + "/DocBoundaries", "IsVisible", "false"),
        ]
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
           'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
           + "\n".join(items) + "\n</oor:items>\n")
    (user / "registrymodifications.xcu").write_text(xml, encoding="utf-8")


def parse_pages(spec: str, total: int) -> list[int]:
    if not spec or spec == "all":
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), min(int(b), total) + 1))
        elif part:
            if int(part) <= total:
                pages.append(int(part))
    return pages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--mode", choices=["light", "dark"], default="light")
    ap.add_argument("--pages", default="all", help="e.g. 1-5,9 (default: all, max 120)")
    ap.add_argument("--zoom", default="page", help="page, width or a percentage")
    ap.add_argument("--show-changes", choices=["yes", "no"], default="yes")
    ap.add_argument("--size", default="1200x1600")
    args = ap.parse_args()

    import uno  # noqa: F401  (python3-uno)
    from com.sun.star.beans import PropertyValue  # type: ignore

    args.outdir.mkdir(parents=True, exist_ok=True)
    display = f":{random.randint(200, 30000)}"
    tmp = Path(tempfile.mkdtemp(prefix="loshot-"))
    make_profile(tmp / "profile", args.mode)
    work = tmp / args.file.name
    shutil.copy(args.file, work)  # private copy: no lock files next to the original
    pipe = f"loshot{random.randint(0, 10**9)}"
    xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", f"{args.size}x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = dict(os.environ, DISPLAY=display, SAL_USE_VCLPLUGIN="gen")
    office = None
    try:
        time.sleep(1.0)
        soffice = Path(uno.__file__).resolve().parent / "soffice"
        office = subprocess.Popen([str(soffice) if soffice.exists() else "soffice", f"-env:UserInstallation=file://{tmp / 'profile'}", "--nologo",
                                   "--norestore", "--nodefault", f"--accept=pipe,name={pipe};urp;"],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
        ctx = None
        for _ in range(240):
            try:
                ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(0.5)
        if ctx is None:
            print("ERROR: could not connect to LibreOffice", file=sys.stderr)
            return 1
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)

        def prop(name, value):
            p = PropertyValue()
            p.Name, p.Value = name, value
            return p

        doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(str(work)), "_blank", 0,
                                           (prop("ReadOnly", True),))
        controller = doc.getCurrentController()
        frame = controller.getFrame()
        window = frame.getContainerWindow()
        w, h = (int(x) for x in args.size.split("x"))
        window.setPosSize(0, 0, w, h, 15)
        time.sleep(3)
        try:
            doc.ShowChanges = args.show_changes == "yes"
        except Exception:
            pass
        settings = controller.getViewSettings()
        from com.sun.star.view.DocumentZoomType import ENTIRE_PAGE, PAGE_WIDTH, BY_VALUE  # type: ignore

        if args.zoom == "page":
            settings.ZoomType = ENTIRE_PAGE
        elif args.zoom == "width":
            settings.ZoomType = PAGE_WIDTH
        else:
            settings.ZoomType = BY_VALUE
            settings.ZoomValue = int(args.zoom)
        try:
            settings.ShowOnlineLayout = False
        except Exception:
            pass
        view_cursor = controller.getViewCursor()
        view_cursor.jumpToLastPage()
        total = view_cursor.getPage()
        pages = parse_pages(args.pages, total)[:120]
        print(f"pages: {total}")
        for page in pages:
            view_cursor.jumpToPage(page)
            view_cursor.jumpToStartOfPage()
            time.sleep(0.2)
            # Scroll so that the page is fully visible (entire-page zoom centers it).
            settings.ZoomType = settings.ZoomType
            time.sleep(1.2)
            out = args.outdir / f"page-{page:03d}.png"
            subprocess.run(["import", "-window", "root", str(out)], env=env, capture_output=True)
            print(out)
        try:
            doc.close(True)
        except Exception:
            pass
        return 0
    finally:
        if office is not None:
            office.terminate()
            try:
                office.wait(15)
            except subprocess.TimeoutExpired:
                office.kill()
        xvfb.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
