"""Execute validated generated source from pinned bytes, never a mutable path."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .source_contract import SourceDiagnostic

# Optimised rustc on a cold toolchain cache, or on a loaded machine, can take
# well over a minute for a large generated program. A timeout here is reported
# as resource_limit, so a too-tight default turns machine load into a spurious
# refusal. Both budgets are overridable for slow or constrained hosts.
RUSTC_TIMEOUT_ENV = "FORGE_RUSTC_TIMEOUT_SECONDS"
PROGRAM_TIMEOUT_ENV = "FORGE_PROGRAM_TIMEOUT_SECONDS"
DEFAULT_RUSTC_TIMEOUT_SECONDS = 180.0
DEFAULT_PROGRAM_TIMEOUT_SECONDS = 60.0


def _timeout_seconds(variable, default):
    """A positive finite number of seconds from the environment, else default."""
    raw = os.environ.get(variable)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not (value > 0 and value != float("inf")):
        raise ValueError(f"{variable} must be a positive number of seconds, got {raw!r}")
    return value


def rustc_timeout_seconds():
    return _timeout_seconds(RUSTC_TIMEOUT_ENV, DEFAULT_RUSTC_TIMEOUT_SECONDS)


def program_timeout_seconds():
    return _timeout_seconds(PROGRAM_TIMEOUT_ENV, DEFAULT_PROGRAM_TIMEOUT_SECONDS)


def run_validated_source(source, language, arguments, output, *, stdin=None):
    """Caller must first validate its complete versioned source scaffold."""
    output = Path(output).absolute()
    compile_timeout = rustc_timeout_seconds() if language=="rust" else None
    run_timeout = program_timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="forge-source-pinned-") as directory:
        pinned = Path(directory)/("program.rs" if language=="rust" else "program.py")
        pinned.write_text(source, encoding="utf-8")
        if language=="rust":
            executable = Path(directory)/"program"
            try:
                subprocess.run(["rustc","--edition=2024","--crate-name","forge_generated","-O",str(pinned),"-o",str(executable)],
                               check=True,capture_output=True,timeout=compile_timeout,cwd=directory)
            except OSError as error:
                raise SourceDiagnostic("missing_toolchain","Rust compiler could not be launched") from error
            except subprocess.TimeoutExpired as error:
                raise SourceDiagnostic("resource_limit","Rust compilation exceeded its timeout") from error
            except subprocess.CalledProcessError as error:
                raise SourceDiagnostic("compilation_failed","generated Rust did not compile") from error
            try:
                with output.open("xb") as artifact, executable.open("rb") as built:
                    shutil.copyfileobj(built,artifact)
                    os.fchmod(artifact.fileno(),0o700)
            except FileExistsError as error:
                raise SourceDiagnostic("artifact_exists","binary artifact path must be new") from error
            # Execute the private binary, never the externally retained artifact.
            command=[str(executable),*arguments]
        else:
            command=[sys.executable,"-I",str(pinned),*arguments]
        try:
            return subprocess.run(command,check=True,capture_output=True,text=True,input=stdin,timeout=run_timeout,cwd=directory)
        except subprocess.TimeoutExpired as error:
            raise SourceDiagnostic("resource_limit","generated program exceeded its timeout") from error
        except subprocess.CalledProcessError as error:
            stderr = error.stderr or ""
            code = (
                "resource_limit" if "resource_limit:" in stderr
                else "unsupported_formula" if "unsupported_formula:" in stderr
                else "execution_failed"
            )
            message = (
                "generated runtime requested an unimplemented formula mode"
                if code == "unsupported_formula"
                else "generated runtime failed or exceeded a declared budget"
            )
            raise SourceDiagnostic(code, message) from error
