"""Narrow safe RAR4 import using the installed trusted extraction backend."""
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import zlib


def unpack_rar4(path, destination, max_bytes):
    path, destination = Path(path).resolve(), Path(destination).resolve()
    entries, total_size, seen = [], 0, set()
    source_size = path.stat().st_size
    with path.open("rb") as handle:
        if handle.read(7) != b"Rar!\x1a\x07\x00":
            raise ValueError("Only non-solid, unencrypted RAR4 is supported; pre-extract other RAR variants")
        while handle.tell() < source_size:
            start = handle.tell()
            base = handle.read(7)
            if len(base) != 7:
                raise ValueError("Truncated RAR4 archive header")
            checksum, kind, flags, size = struct.unpack("<HBHH", base)
            if size < 7 or size > 65535:
                raise ValueError("Invalid RAR4 header size")
            header = base + handle.read(size - 7)
            if len(header) != size or zlib.crc32(header[2:]) & 0xffff != checksum:
                raise ValueError("RAR4 archive header checksum failed")
            packed = struct.unpack_from("<I", header, 7)[0] if flags & 0x8000 and size >= 11 else 0
            if kind == 0x73:
                if flags & 0x89:
                    raise ValueError("Solid, encrypted and multi-volume RAR imports require trusted pre-extraction")
            elif kind == 0x74:
                if flags & 0x317 or size < 32:
                    raise ValueError("Split, encrypted, solid, large or Unicode RAR entries require trusted pre-extraction")
                unpacked = struct.unpack_from("<I", header, 11)[0]
                host, name_size, attributes = header[15], struct.unpack_from("<H", header, 26)[0], struct.unpack_from("<I", header, 28)[0]
                if 32 + name_size > size:
                    raise ValueError("Invalid RAR4 filename size")
                name = header[32:32 + name_size].decode("utf-8", errors="strict").replace("\\", "/")
                relative = PurePosixPath(name)
                target = (destination / name).resolve()
                if not name or relative.is_absolute() or ".." in relative.parts or ":" in name or "\x00" in name or not target.is_relative_to(destination):
                    raise ValueError(f"Unsafe archive member: {name}")
                unix_mode = attributes & 0o170000 if host == 3 else 0
                if unix_mode not in (0, 0o100000, 0o040000) or attributes & 0x400:
                    raise ValueError(f"Unsafe archive link or special file: {name}")
                is_directory = bool(attributes & 0x10) if host != 3 else unix_mode == 0o040000
                identity = str(target).casefold()
                if identity in seen or target.exists():
                    raise FileExistsError(f"Archive import would overwrite an existing or duplicate file: {target}")
                seen.add(identity)
                total_size += unpacked
                if total_size > max_bytes or len(entries) >= 100000:
                    raise ValueError("Archive exceeds the explicit import size limit")
                entries.append((name, target, is_directory, unpacked))
            elif kind == 0x7b:
                if flags & 1:
                    raise ValueError("Multi-volume RAR import is not supported")
                break
            elif kind not in (0x75, 0x76, 0x77, 0x78, 0x79, 0x7a):
                raise ValueError("Unknown RAR4 block type")
            next_offset = start + size + packed
            if next_offset > source_size:
                raise ValueError("Truncated RAR4 file payload")
            handle.seek(next_offset)
    if not entries:
        raise ValueError("RAR4 archive contains no supported regular files")
    extractor = shutil.which("tar")
    if extractor is None:
        raise ValueError("Local RAR4 requires a trusted tar build with RAR support; pre-extract to a MAT directory")
    # Enumeration and overwrite checks complete before the first file is created.
    destination.mkdir(parents=True, exist_ok=True)
    for name, target, is_directory, unpacked in entries:
        if is_directory:
            target.mkdir(parents=True, exist_ok=True)
            continue
        result = subprocess.run([extractor, "-xf", str(path), "-C", str(destination), "--", name],
                                capture_output=True, text=True, timeout=120)
        if result.returncode or not target.is_file() or target.stat().st_size != unpacked:
            raise ValueError(f"RAR4 extraction failed: {result.stderr.strip()}")
    return destination
