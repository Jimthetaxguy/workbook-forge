"""Matched validation-only probe; profile overhead is excluded from timed samples."""

import argparse
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
import statistics
import time

from workbook_forge import source_graph as g


def run(fixture, output, repeats):
    code, _ = g.compile_workbook(fixture, "python", 5)
    program = g.import_source(code, "python")
    samples = []
    for i in range(repeats + 2):
        start = time.perf_counter()
        dependencies = g.validate(program)
        elapsed = (time.perf_counter() - start) * 1000
        if i >= 2:
            samples.append(elapsed)
    original = g.range_keys
    calls = 0

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    g.range_keys = counted
    try:
        assert g.validate(program) == dependencies
    finally:
        g.range_keys = original
    profile = cProfile.Profile()
    profile.enable()
    for _ in range(10):
        g.validate(program)
    profile.disable()
    stats = pstats.Stats(profile)
    functions = sorted(
        [
            {
                "file": Path(file).name,
                "line": line,
                "function": name,
                "calls": v[1],
                "cumulative_seconds": v[3],
            }
            for (file, line, name), v in stats.stats.items()
        ],
        key=lambda x: x["cumulative_seconds"],
        reverse=True,
    )[:15]
    record = {
        "repetitions": repeats,
        "warmups": 2,
        "median_ms": statistics.median(samples),
        "samples_ms": samples,
        "range_expansions_per_validation": calls,
        "dependency_sha256": hashlib.sha256(
            json.dumps(dependencies, sort_keys=True).encode()
        ).hexdigest(),
        "source_sha256": hashlib.sha256(code.encode()).hexdigest(),
        "source_graph_sha256": hashlib.sha256(Path(g.__file__).read_bytes()).hexdigest(),
        "profile_ten_calls": functions,
        "scope": "validation-only, not a runtime/end-to-end benchmark or Excel evidence",
    }
    with output.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeats", type=int, choices=range(10, 101), default=40)
    args = parser.parse_args()
    run(args.fixture, args.output, args.repeats)
