"""Patch the Ubuntu live-server GRUB configuration for autoinstall."""

from __future__ import annotations

AUTOINSTALL_ARG = "autoinstall ds=nocloud\\;s=/cdrom/maas-deploy/autoinstall/"


def patch_grub(text: str) -> str:
    """Add the nocloud autoinstall argument to casper kernel lines.

    GRUB treats an unescaped semicolon as a command separator, so the cloud-init
    datasource argument is written with a backslash.
    """
    lines = []
    for line in text.splitlines(keepends=True):
        # Ubuntu 24.04.5's grub.cfg contains a stray word here. GRUB tries to
        # execute it and prints an error before the menu. The following test is
        # the real platform check, so the stray word is removed.
        if line.strip() == "grub_platform":
            continue
        stripped = line.lstrip()
        if stripped.startswith("linux") and "/casper/" in stripped and "vmlinuz" in stripped and "autoinstall" not in stripped:
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            if " ---" in body:
                body = body.replace(" ---", f" {AUTOINSTALL_ARG} ---", 1)
            else:
                body = body.rstrip() + " " + AUTOINSTALL_ARG
            line = body + newline
        lines.append(line)
    return "".join(lines)
