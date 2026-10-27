from __future__ import annotations

import argparse
from pathlib import Path
import time

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.connection import NoConnectException


def _property(name: str, value: object) -> PropertyValue:
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def _connect(port: int, timeout_seconds: float = 60.0):
    context = uno.getComponentContext()
    resolver = context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", context
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return resolver.resolve(
                f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
            )
        except NoConnectException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()

    document_path = args.docx.resolve()
    pdf_path = args.pdf.resolve()
    remote = _connect(args.port)
    desktop = remote.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", remote
    )
    document = desktop.loadComponentFromURL(
        uno.systemPathToFileUrl(str(document_path)),
        "_blank",
        0,
        (_property("Hidden", True), _property("UpdateDocMode", 3)),
    )
    if document is None:
        raise RuntimeError(f"LibreOffice could not open DOCX: {document_path}")
    try:
        indexes = document.getDocumentIndexes()
        if indexes.getCount() == 0:
            raise RuntimeError("DOCX does not contain a document index")
        for index in range(indexes.getCount()):
            indexes.getByIndex(index).update()
        document.store()
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        document.storeToURL(
            uno.systemPathToFileUrl(str(pdf_path)),
            (
                _property("FilterName", "writer_pdf_Export"),
                _property(
                    "FilterData",
                    (
                        _property("UseTaggedPDF", True),
                        _property("ExportBookmarks", True),
                        _property("ExportBookmarksToPDFDestination", True),
                    ),
                ),
            ),
        )
    finally:
        document.close(True)
        desktop.terminate()


if __name__ == "__main__":
    main()
