"""The Python engine builds independently; the Rust bridge is explicit opt-in."""

import os

from setuptools import setup


if os.environ.get("WORKBOOK_FORGE_BUILD_NATIVE") == "1":
    from setuptools_rust import Binding, RustExtension

    setup(
        rust_extensions=[
            RustExtension(
                "workbook_forge._native",
                path="native/Cargo.toml",
                binding=Binding.PyO3,
                py_limited_api=True,
            )
        ],
        zip_safe=False,
    )
else:
    setup()
