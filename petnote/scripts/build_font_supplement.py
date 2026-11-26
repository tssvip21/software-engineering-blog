"""Rebuild the OFL-licensed supplement without changing runtime dependencies.

Use Python 3.13, fonttools==4.69.1 and brotli==1.2.1 in an isolated build env.
Download the pinned official original documented in static/care/fonts/README.md,
then run: python scripts/build_font_supplement.py --source ORIGINAL.woff2
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

APP = Path(__file__).resolve().parents[1]
FONTS = APP / "static/care/fonts"
SOURCE_SHA256 = "9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4"
MARKER = "/* Supplemental coverage"


def codepoints(value: str) -> set[int]:
    points: set[int] = set()
    for item in value.split(","):
        token = item.strip().upper().removeprefix("U+")
        if "-" in token:
            first, last = token.split("-")
        elif "?" in token:
            first, last = token.replace("?", "0"), token.replace("?", "F")
        else:
            first = last = token
        points.update(range(int(first, 16), int(last, 16) + 1))
    return points


def unicode_ranges(points: set[int]) -> str:
    ranges: list[tuple[int, int]] = []
    for point in sorted(points):
        if ranges and point == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], point)
        else:
            ranges.append((point, point))
    return ",".join(f"U+{first:X}" if first == last else f"U+{first:X}-{last:X}"
                    for first, last in ranges)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != SOURCE_SHA256:
        parser.error("Source must be the unchanged official Pretendard Variable v1.3.9")
    source = TTFont(args.source, recalcTimestamp=False)
    original = set(source.getBestCmap())
    original_axes = [(axis.axisTag, axis.minValue, axis.defaultValue, axis.maxValue)
                     for axis in source["fvar"].axes]
    css_path = FONTS / "pretendard.css"
    css = css_path.read_text(encoding="utf-8").split(MARKER)[0].rstrip() + "\n"
    covered: set[int] = set()
    faces = re.findall(r"@font-face\s*\{([^}]+)\}", css)
    if len(faces) != 92:
        parser.error("Expected the 92 unchanged official dynamic subset faces")
    for face in faces:
        filename = re.search(r"url\(['\"]?([^)'\"]+)", face).group(1)
        allowed = codepoints(re.search(r"unicode-range:\s*([^;]+)", face).group(1))
        with TTFont(FONTS / filename) as font:
            covered.update(set(font.getBestCmap()) & allowed)
    missing = original - covered
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=missing)
    subsetter.subset(source)

    # OFL Reserved Font Names cannot identify modified fonts. Preserve all
    # original copyright, license, trademark and designer attribution records.
    naming_ids = {1, 3, 4, 6, 16, 21, 25}
    naming_ids.update(instance.postscriptNameID for instance in source["fvar"].instances
                      if instance.postscriptNameID != 0xFFFF)
    for record in source["name"].names:
        if record.nameID in naming_ids:
            name = record.toUnicode().replace("Pretendard Variable", "Petnote Supplemental")
            name = name.replace("PretendardVariable", "PetnoteSupplemental")
            record.string = name.encode(record.getEncoding())
    source["name"].setName(
        "Petnote Supplemental: glyph coverage omitted from the official "
        "Pretendard v1.3.9 dynamic subsets. Modified under SIL OFL 1.1.",
        10, 3, 1, 0x409,
    )
    output = FONTS / "PetnoteSupplemental.woff2"
    source.flavor = "woff2"
    source.save(output)
    with TTFont(output) as generated:
        actual = set(generated.getBestCmap())
        assert actual == missing, "Supplement must preserve every omitted character"
        assert [(axis.axisTag, axis.minValue, axis.defaultValue, axis.maxValue)
                for axis in generated["fvar"].axes] == original_axes
        assert not any("Pretendard" in item.toUnicode()
                       for item in generated["name"].names if item.nameID in naming_ids)
    assert covered | actual == original, "Combined coverage must match the original"
    css += (MARKER + " for characters absent from the official dynamic subsets. */\n"
            "@font-face { font-family: 'Petnote Supplemental'; font-weight: 100 900; "
            "font-display: swap; font-style: normal; "
            "src: url('PetnoteSupplemental.woff2') format('woff2-variations'); "
            "unicode-range: " + unicode_ranges(missing) + "; }\n")
    css_path.write_text(css, encoding="utf-8")
    print(f"Original: {len(original)} codepoints; dynamic subsets: {len(covered)}; "
          f"supplement: {len(actual)}; supplement bytes: {output.stat().st_size}")
    print("Output SHA256:", hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
