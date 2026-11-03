"""LibreOffice headless/UNO conversion, called only by the isolated worker."""

import subprocess
import time
from pathlib import Path
from uuid import uuid4

from enterprise_agent.tools.document_formats import CONVERSIONS, DocumentExtractionError

_FILTERS = {
    ".docx": "Office Open XML Text",
    ".pptx": "Impress MS PowerPoint 2007 XML",
    ".xlsx": "Calc MS Excel 2007 XML",
}
_SERVICES = {
    ".docx": "com.sun.star.text.TextDocument",
    ".pptx": "com.sun.star.presentation.PresentationDocument",
    ".xlsx": "com.sun.star.sheet.SpreadsheetDocument",
}


def convert_document(
    content: bytes, suffix: str, directory: Path, *, timeout: int, max_output_bytes: int
) -> tuple[bytes, str]:
    try:
        import uno
        from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
        from com.sun.star.document.UpdateDocMode import NO_UPDATE
    except ImportError:
        raise DocumentExtractionError("LibreOffice UNO conversion is unavailable") from None
    target = CONVERSIONS[suffix]
    source = directory / ("input" + suffix)
    output = directory / ("converted" + target)
    source.write_bytes(content)
    profile = directory / "profile"
    profile.mkdir()
    # Disable macros independently of per-load UNO policy. The profile has no trusted
    # locations/publishers and is discarded with this container's private tmpfs.
    (profile / "user").mkdir()
    (profile / "user" / "registrymodifications.xcu").write_text(
        '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        "</item></oor:items>",
        encoding="utf-8",
    )
    pipe = "enterprise_" + uuid4().hex
    process = None
    document = None
    try:
        process = subprocess.Popen(
            [
                "/usr/bin/libreoffice",
                "--headless",
                "--norestore",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile.as_uri()}",
                f"--accept=pipe,name={pipe};urp;StarOffice.ComponentContext",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=directory,
            start_new_session=True,
        )
        resolver = uno.getComponentContext().ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", uno.getComponentContext()
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                context = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                break
            except TimeoutError:
                raise
            except Exception:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise DocumentExtractionError(
                        "LibreOffice startup failed or timed out"
                    ) from None
                time.sleep(0.05)

        def properties(**values):
            result = []
            for name, value in values.items():
                item = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
                item.Name, item.Value = name, value
                result.append(item)
            return tuple(result)

        desktop = context.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", context
        )
        document = desktop.loadComponentFromURL(
            source.as_uri(),
            "_blank",
            0,
            properties(
                Hidden=True,
                ReadOnly=True,
                MacroExecutionMode=NEVER_EXECUTE,
                UpdateDocMode=NO_UPDATE,
                AllowLinkUpdate=False,
            ),
        )
        if document is None or not document.supportsService(_SERVICES[target]):
            raise DocumentExtractionError("Converted document type does not match its extension")
        document.storeToURL(
            output.as_uri(), properties(FilterName=_FILTERS[target], Overwrite=False)
        )
        if not output.is_file() or output.is_symlink() or output.stat().st_size > max_output_bytes:
            raise DocumentExtractionError("Converted document exceeds the output limit")
        return output.read_bytes(), target
    except DocumentExtractionError:
        raise
    except FileNotFoundError:
        raise DocumentExtractionError("LibreOffice conversion is unavailable") from None
    except Exception:
        raise DocumentExtractionError("LibreOffice could not convert the document safely") from None
    finally:
        if document is not None:
            try:
                document.close(True)
            except Exception:
                pass
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
