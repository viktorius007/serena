"""
This file contains various utility functions like I/O operations, handling paths, etc.
"""

import base64
import gzip
import hashlib
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import uuid
import zipfile
from enum import Enum
from pathlib import Path, PurePath

import charset_normalizer
import requests

from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_types import UnifiedSymbolInformation

log = logging.getLogger(__name__)


class InvalidTextLocationError(Exception):
    pass


class TextUtils:
    """
    Utilities for text operations.
    """

    @staticmethod
    def get_line_col_from_index(text: str, index: int) -> tuple[int, int]:
        """
        Returns the zero-indexed line and column number of the given index in the given text
        """
        l = 0
        c = 0
        idx = 0
        while idx < index:
            if text[idx] == "\n":
                l += 1
                c = 0
            else:
                c += 1
            idx += 1

        return l, c

    @staticmethod
    def get_index_from_line_col(text: str, line: int, col: int) -> int:
        """
        Returns the index of the given zero-indexed line and column number in the given text
        """
        idx = 0
        while line > 0:
            if idx >= len(text):
                raise InvalidTextLocationError
            if text[idx] == "\n":
                line -= 1
            idx += 1
        idx += col
        return idx

    @staticmethod
    def _get_updated_position_from_line_and_column_and_edit(l: int, c: int, text_to_be_inserted: str) -> tuple[int, int]:
        """
        Utility function to get the position of the cursor after inserting text at a given line and column.
        """
        num_newlines_in_gen_text = text_to_be_inserted.count("\n")
        if num_newlines_in_gen_text > 0:
            l += num_newlines_in_gen_text
            c = len(text_to_be_inserted.split("\n")[-1])
        else:
            c += len(text_to_be_inserted)
        return (l, c)

    @staticmethod
    def delete_text_between_positions(text: str, start_line: int, start_col: int, end_line: int, end_col: int) -> tuple[str, str]:
        """
        Deletes the text between the given start and end positions.
        Returns the modified text and the deleted text.
        """
        del_start_idx = TextUtils.get_index_from_line_col(text, start_line, start_col)
        del_end_idx = TextUtils.get_index_from_line_col(text, end_line, end_col)

        deleted_text = text[del_start_idx:del_end_idx]
        new_text = text[:del_start_idx] + text[del_end_idx:]
        return new_text, deleted_text

    @staticmethod
    def insert_text_at_position(text: str, line: int, col: int, text_to_be_inserted: str) -> tuple[str, int, int]:
        """
        Inserts the given text at the given line and column.
        Returns the modified text and the new line and column.
        """
        try:
            change_index = TextUtils.get_index_from_line_col(text, line, col)
        except InvalidTextLocationError:
            num_lines_in_text = text.count("\n") + 1
            max_line = num_lines_in_text - 1
            if line == max_line + 1 and col == 0:  # trying to insert at new line after full text
                # insert at end, adding missing newline
                change_index = len(text)
                text_to_be_inserted = "\n" + text_to_be_inserted
            else:
                raise
        new_text = text[:change_index] + text_to_be_inserted + text[change_index:]
        new_l, new_c = TextUtils._get_updated_position_from_line_and_column_and_edit(line, col, text_to_be_inserted)
        return new_text, new_l, new_c


class PathUtils:
    """
    Utilities for platform-agnostic path operations.
    """

    @staticmethod
    def uri_to_path(uri: str) -> str:
        """
        Converts a URI to a file path. Works on both Linux and Windows.

        This method was obtained from https://stackoverflow.com/a/61922504
        """
        try:
            from urllib.parse import unquote, urlparse
            from urllib.request import url2pathname
        except ImportError:
            # backwards compatibility (Python 2)
            from urllib.parse import unquote as unquote_py2
            from urllib.request import url2pathname as url2pathname_py2

            from urlparse import urlparse as urlparse_py2

            unquote = unquote_py2
            url2pathname = url2pathname_py2
            urlparse = urlparse_py2
        parsed = urlparse(uri)
        host = f"{os.path.sep}{os.path.sep}{parsed.netloc}{os.path.sep}"
        path = os.path.normpath(os.path.join(host, url2pathname(unquote(parsed.path))))
        return path

    @staticmethod
    def path_to_uri(path: str) -> str:
        """
        Converts a file path to a file URI (file:///...).
        """
        return str(Path(path).absolute().as_uri())

    @staticmethod
    def is_glob_pattern(pattern: str) -> bool:
        """Check if a pattern contains glob-specific characters."""
        return any(c in pattern for c in "*?[]!")

    @staticmethod
    def get_relative_path(path: str, base_path: str) -> str | None:
        """
        Gets relative path if it's possible (paths should be on the same drive),
        returns `None` otherwise.
        """
        if PurePath(path).drive == PurePath(base_path).drive:
            rel_path = str(PurePath(os.path.relpath(path, base_path)))
            return rel_path
        return None


class FileUtils:
    """
    Utility functions for file operations.
    """

    @staticmethod
    def read_file(file_path: str, encoding: str) -> str:
        """
        Reads the file at the given path using the given encoding and returns the contents as a string.
        If decoding fails, tries to detect the encoding using charset_normalizer.

        Raises FileNotFoundError if the file does not exist.
        """
        if not os.path.exists(file_path):
            log.error(f"Failed to read '{file_path}': File does not exist.")
            raise FileNotFoundError(f"File read '{file_path}' failed: File does not exist.")
        try:
            try:
                with open(file_path, encoding=encoding) as inp_file:
                    return inp_file.read()
            except UnicodeDecodeError as ude:
                results = charset_normalizer.from_path(file_path)
                match = results.best()
                if match:
                    log.warning(
                        f"Could not decode {file_path} with encoding='{encoding}'; using best match '{match.encoding}' instead",
                    )
                    return match.raw.decode(match.encoding)
                raise ude
        except Exception as exc:
            log.error(f"Failed to read '{file_path}' with encoding '{encoding}': {exc}")
            raise exc

    @staticmethod
    def download_file(url: str, target_path: str) -> None:
        """
        Downloads the file from the given URL to the given {target_path}
        """
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        try:
            response = requests.get(url, stream=True, timeout=60)
            if response.status_code != 200:
                log.error(f"Error downloading file '{url}': {response.status_code} {response.text}")
                raise SolidLSPException("Error downloading file.")
            with open(target_path, "wb") as f:
                shutil.copyfileobj(response.raw, f)
        except Exception as exc:
            log.error(f"Error downloading file '{url}': {exc}")
            raise SolidLSPException("Error downloading file.") from None

    @staticmethod
    def _hash_file(path: str, algo: str, chunk_size: int = 1024 * 1024) -> str:
        """Compute a hex digest for the file using the given hashlib algorithm (e.g., 'sha256', 'sha512')."""
        h = hashlib.new(algo)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _verify_hash_auto(path: str, expected: str) -> None:
        """Verify the file digest, auto-detecting SHA-256 vs SHA-512.
        Accepts expected as hex, or base64 (will be normalized to hex).
        """
        exp = expected.strip()
        # Normalize base64 to hex if applicable
        try:
            if "/" in exp or "+" in exp or "=" in exp:
                raw = base64.b64decode(exp, validate=True)
                exp = raw.hex()
        except Exception:
            pass
        algo = "sha256" if len(exp) == 64 else ("sha512" if len(exp) == 128 else None)
        if not algo:
            raise SolidLSPException(f"Unsupported expected hash length ({len(exp)}); expected 64 or 128 hex chars")
        actual = FileUtils._hash_file(path, algo)
        if actual.lower() != exp.lower():
            raise SolidLSPException(
                f"{algo.upper()} mismatch for {path}: expected {exp}, got {actual}"
            )

    @staticmethod
    def _fetch_expected_hash_from_url(checksum_url: str, filename: str) -> str:
        """Download a checksum file and extract the sha256 for the given filename.

        Supports common formats like:
        - "<sha256>  <filename>"
        - "<sha256> *<filename>"
        - Files that contain only a single hex digest (common for per‑asset .sha256sum files)
        Ignores comment lines starting with '#'.
        """
        try:
            resp = requests.get(checksum_url, timeout=60)
            resp.raise_for_status()
            text = resp.text.strip()
            lines = text.splitlines()
            # Try mapping style first (e.g., SHA256SUMS)
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    sha, name = parts[0], parts[-1]
                    if name.startswith("*"):
                        name = name[1:]
                    if name == filename:
                        return sha
            # Fallback: single-hash file
            # If the file appears to contain a single hex digest, use it.
            if len(lines) == 1 and all(c in "0123456789abcdefABCDEF" for c in lines[0].strip()):
                return lines[0].strip()
            raise SolidLSPException(f"Checksum for '{filename}' not found in {checksum_url}")
        except Exception as exc:
            raise SolidLSPException(f"Failed to fetch or parse checksum file {checksum_url}: {exc}") from exc

    @staticmethod
    def download_and_extract_archive(
        url: str,
        target_path: str,
        archive_type: str,
        expected_sha256: str | None = None,
        checksum_url: str | None = None,
    ) -> None:
        """
        Downloads the archive from the given URL having format {archive_type} and extracts it to the given {target_path}.
        Optionally verifies the SHA256 of the downloaded file before extraction, either via an explicit
        expected hash or by fetching an official checksum file.
        """
        try:
            tmp_files = []
            tmp_file_name = str(PurePath(os.path.expanduser("~"), "solidlsp_tmp", uuid.uuid4().hex))
            tmp_files.append(tmp_file_name)
            os.makedirs(os.path.dirname(tmp_file_name), exist_ok=True)
            FileUtils.download_file(url, tmp_file_name)

            # Determine expected sha256 if not provided explicitly
            if expected_sha256 is None and checksum_url is not None:
                from urllib.parse import urlparse

                download_filename = os.path.basename(urlparse(url).path)
                expected_sha256 = FileUtils._fetch_expected_hash_from_url(checksum_url, download_filename)

            if expected_sha256:
                FileUtils._verify_hash_auto(tmp_file_name, expected_sha256)

            # Ensure target directory exists
            if archive_type in ["tar", "gztar", "bztar", "xztar", "zip", "zip.gz"]:
                os.makedirs(target_path, exist_ok=True)

            def _is_within_directory(base: str, path: str) -> bool:
                base_abs = os.path.abspath(base)
                path_abs = os.path.abspath(path)
                return os.path.commonprefix([base_abs + os.sep, path_abs + os.sep]) == base_abs + os.sep

            if archive_type in ["tar", "gztar", "bztar", "xztar"]:
                # Use tarfile with path traversal protection
                with tarfile.open(tmp_file_name, mode="r:*") as tar:
                    for member in tar.getmembers():
                        dest = os.path.join(target_path, member.name)
                        if not _is_within_directory(target_path, dest):
                            raise SolidLSPException(f"Unsafe path detected in tar archive: {member.name}")
                    tar.extractall(target_path)  # safe due to pre-check
            elif archive_type == "zip":
                with zipfile.ZipFile(tmp_file_name, "r") as zip_ref:
                    for zip_info in zip_ref.infolist():
                        dest = os.path.join(target_path, zip_info.filename)
                        if not _is_within_directory(target_path, dest):
                            raise SolidLSPException(f"Unsafe path detected in zip archive: {zip_info.filename}")
                        extracted_path = zip_ref.extract(zip_info, target_path)
                        ZIP_SYSTEM_UNIX = 3  # zip file created on Unix system
                        if zip_info.create_system == ZIP_SYSTEM_UNIX:
                            # Preserve Unix permissions
                            attrs = (zip_info.external_attr >> 16) & 0o777
                            if attrs:
                                os.chmod(extracted_path, attrs)
            elif archive_type == "zip.gz":
                tmp_file_name_ungzipped = tmp_file_name + ".zip"
                tmp_files.append(tmp_file_name_ungzipped)
                with gzip.open(tmp_file_name, "rb") as f_in, open(tmp_file_name_ungzipped, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                with zipfile.ZipFile(tmp_file_name_ungzipped, "r") as zip_ref:
                    for zip_info in zip_ref.infolist():
                        dest = os.path.join(target_path, zip_info.filename)
                        if not _is_within_directory(target_path, dest):
                            raise SolidLSPException(f"Unsafe path detected in zip.gz archive: {zip_info.filename}")
                        extracted_path = zip_ref.extract(zip_info, target_path)
                        ZIP_SYSTEM_UNIX = 3
                        if zip_info.create_system == ZIP_SYSTEM_UNIX:
                            attrs = (zip_info.external_attr >> 16) & 0o777
                            if attrs:
                                os.chmod(extracted_path, attrs)
            elif archive_type == "gz":
                with gzip.open(tmp_file_name, "rb") as f_in, open(target_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            elif archive_type == "binary":
                # For single binary files, just move to target without extraction
                shutil.move(tmp_file_name, target_path)
            else:
                log.error(f"Unknown archive type '{archive_type}' for extraction")
                raise SolidLSPException(f"Unknown archive type '{archive_type}'")
        except Exception as exc:
            log.error(f"Error extracting archive '{tmp_file_name}' obtained from '{url}': {exc}")
            raise SolidLSPException("Error extracting archive.") from exc
        finally:
            for tmp_file_name in tmp_files:
                if os.path.exists(tmp_file_name):
                    Path.unlink(Path(tmp_file_name))


class PlatformId(str, Enum):
    WIN_x86 = "win-x86"
    WIN_x64 = "win-x64"
    WIN_arm64 = "win-arm64"
    OSX = "osx"
    OSX_x64 = "osx-x64"
    OSX_arm64 = "osx-arm64"
    LINUX_x86 = "linux-x86"
    LINUX_x64 = "linux-x64"
    LINUX_arm64 = "linux-arm64"
    LINUX_MUSL_x64 = "linux-musl-x64"
    LINUX_MUSL_arm64 = "linux-musl-arm64"

    def is_windows(self) -> bool:
        return self.value.startswith("win")


class DotnetVersion(str, Enum):
    V4 = "4"
    V6 = "6"
    V7 = "7"
    V8 = "8"
    V9 = "9"
    VMONO = "mono"


class PlatformUtils:
    """
    This class provides utilities for platform detection and identification.
    """

    @classmethod
    def get_platform_id(cls) -> PlatformId:
        """
        Returns the platform id for the current system
        """
        system = platform.system()
        machine = platform.machine()
        bitness = platform.architecture()[0]
        if system == "Windows" and machine == "":
            machine = cls._determine_windows_machine_type()
        system_map = {"Windows": "win", "Darwin": "osx", "Linux": "linux"}
        machine_map = {
            "AMD64": "x64",
            "x86_64": "x64",
            "i386": "x86",
            "i686": "x86",
            "aarch64": "arm64",
            "arm64": "arm64",
            "ARM64": "arm64",
        }
        if system in system_map and machine in machine_map:
            platform_id = system_map[system] + "-" + machine_map[machine]
            if system == "Linux" and bitness == "64bit":
                libc = platform.libc_ver()[0]
                if libc != "glibc":
                    platform_id += "-" + libc
            return PlatformId(platform_id)
        else:
            raise SolidLSPException(f"Unknown platform: {system=}, {machine=}, {bitness=}")

    @staticmethod
    def _determine_windows_machine_type() -> str:
        import ctypes
        from ctypes import wintypes

        class SYSTEM_INFO(ctypes.Structure):
            class _U(ctypes.Union):
                class _S(ctypes.Structure):
                    _fields_ = [("wProcessorArchitecture", wintypes.WORD), ("wReserved", wintypes.WORD)]

                _fields_ = [("dwOemId", wintypes.DWORD), ("s", _S)]
                _anonymous_ = ("s",)

            _fields_ = [
                ("u", _U),
                ("dwPageSize", wintypes.DWORD),
                ("lpMinimumApplicationAddress", wintypes.LPVOID),
                ("lpMaximumApplicationAddress", wintypes.LPVOID),
                ("dwActiveProcessorMask", wintypes.LPVOID),
                ("dwNumberOfProcessors", wintypes.DWORD),
                ("dwProcessorType", wintypes.DWORD),
                ("dwAllocationGranularity", wintypes.DWORD),
                ("wProcessorLevel", wintypes.WORD),
                ("wProcessorRevision", wintypes.WORD),
            ]
            _anonymous_ = ("u",)

        sys_info = SYSTEM_INFO()
        ctypes.windll.kernel32.GetNativeSystemInfo(ctypes.byref(sys_info))  # type: ignore

        arch_map = {
            9: "AMD64",
            5: "ARM",
            12: "arm64",
            6: "Intel Itanium-based",
            0: "i386",
        }

        return arch_map.get(sys_info.wProcessorArchitecture, f"Unknown ({sys_info.wProcessorArchitecture})")

    @staticmethod
    def get_dotnet_version() -> DotnetVersion:
        """
        Returns the dotnet version for the current system
        """
        try:
            result = subprocess.run(["dotnet", "--list-runtimes"], capture_output=True, check=True)
            available_version_cmd_output = []
            for line in result.stdout.decode("utf-8").split("\n"):
                if line.startswith("Microsoft.NETCore.App"):
                    version_cmd_output = line.split(" ")[1]
                    available_version_cmd_output.append(version_cmd_output)

            if not available_version_cmd_output:
                raise SolidLSPException("dotnet not found on the system")

            # Check for supported versions in order of preference (latest first)
            for version_cmd_output in available_version_cmd_output:
                if version_cmd_output.startswith("9"):
                    return DotnetVersion.V9
                if version_cmd_output.startswith("8"):
                    return DotnetVersion.V8
                if version_cmd_output.startswith("7"):
                    return DotnetVersion.V7
                if version_cmd_output.startswith("6"):
                    return DotnetVersion.V6
                if version_cmd_output.startswith("4"):
                    return DotnetVersion.V4

            # If no supported version found, raise exception with all available versions
            raise SolidLSPException(
                f"No supported dotnet version found. Available versions: {', '.join(available_version_cmd_output)}. Supported versions: 4, 6, 7, 8"
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            try:
                result = subprocess.run(["mono", "--version"], capture_output=True, check=True)
                return DotnetVersion.VMONO
            except (FileNotFoundError, subprocess.CalledProcessError):
                raise SolidLSPException("dotnet or mono not found on the system")


class SymbolUtils:
    @staticmethod
    def symbol_tree_contains_name(roots: list[UnifiedSymbolInformation], name: str) -> bool:
        for symbol in roots:
            if symbol["name"] == name:
                return True
            if SymbolUtils.symbol_tree_contains_name(symbol["children"], name):
                return True
        return False
