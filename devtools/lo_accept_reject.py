#!/usr/bin/env python3
"""Apply LibreOffice's Accept All / Reject All to a tracked-changes DOCX and report the result.

Usage: lo_accept_reject.py FILE.docx OUTPREFIX

Writes OUTPREFIX.{asis,accept,reject}.txt (plain text export), .struct (one line per paragraph:
style|text, tables as TABLE rows=N) and .pdf (accept/reject), and prints the number of formula
objects, tables and remaining redlines for each mode. Run it with another installation's Python
(e.g. /opt/libreoffice26.8/program/python) to use that LibreOffice version.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import uno
from com.sun.star.beans import PropertyValue


def pv(name, value):
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def main() -> int:
    src, prefix = sys.argv[1], sys.argv[2]
    port = 2000 + os.getpid() % 5000
    profile = tempfile.mkdtemp(prefix="lo-ar-")
    soffice = Path(uno.__file__).resolve().parent / "soffice"
    proc = subprocess.Popen([str(soffice) if soffice.exists() else "soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--invisible",
                             "--norestore", f"--accept=socket,host=localhost,port={port};urp;"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
    for _ in range(60):
        try:
            ctx = resolver.resolve(f"uno:socket,host=localhost,port={port};urp;StarOffice.ComponentContext")
            break
        except Exception:
            time.sleep(1)
    desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
    dispatcher = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.DispatchHelper", ctx)
    try:
        for mode in ("asis", "accept", "reject"):
            doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(os.path.abspath(src)), "_blank", 0,
                                               (pv("Hidden", True),))
            frame = doc.getCurrentController().getFrame()
            if mode == "accept":
                dispatcher.executeDispatch(frame, ".uno:AcceptAllTrackedChanges", "", 0, ())
            elif mode == "reject":
                dispatcher.executeDispatch(frame, ".uno:RejectAllTrackedChanges", "", 0, ())
            base = f"{prefix}.{mode}"
            doc.storeToURL(uno.systemPathToFileUrl(os.path.abspath(base + ".txt")),
                           (pv("FilterName", "Text (encoded)"), pv("FilterOptions", "UTF8,LF,,,")))
            if mode != "asis":
                doc.storeToURL(uno.systemPathToFileUrl(os.path.abspath(base + ".pdf")),
                               (pv("FilterName", "writer_pdf_Export"),))
            info = []
            enum = doc.getText().createEnumeration()
            while enum.hasMoreElements():
                element = enum.nextElement()
                if element.supportsService("com.sun.star.text.TextTable"):
                    info.append(f"TABLE rows={element.getRows().getCount()}")
                else:
                    info.append(f"{element.ParaStyleName}|{element.getString()[:90]}")
            with open(base + ".struct", "w") as handle:
                handle.write("\n".join(info) + "\n")
            redlines = doc.Redlines.getCount() if hasattr(doc, "Redlines") else "?"
            print(f"{mode:6} formulas={doc.getEmbeddedObjects().getCount()} tables={doc.getTextTables().getCount()} "
                  f"images={doc.getGraphicObjects().getCount()} redlines={redlines}")
            doc.close(True)
    finally:
        try:
            desktop.terminate()
        except Exception:
            pass
        time.sleep(1)
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
