#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Unified tone across all textures (for initial setup before release). Uses Pillow.

Original PLATEAU textures are dark overall due to overcast-sky photography;
**bulk-convert the entire city** to reference tone (tools/tex_editor/tone_standard.json).

Design (role split with submission-time correction in tools/tex_editor):
- Bulk conversion (this script): create a **single transformation** from city-wide statistics
  and apply the same transformation to all images. Per-image adjustment would brighten even
  "genuinely dark exterior walls", erasing building-to-building character, so we uniformly
  shift the whole and preserve relative differences.
- Submission-time correction (texture editor): per-photo match to standard (shooting conditions differ per photo).

Transformation is done in YCbCr:
- Y (luminance): linear transformation y' = (y − μ_all) × (σ_ref/σ_all) + μ_ref (exposure, contrast)
- Cb/Cr (chrominance): mean offset only (white balance; do not touch saturation variance)

Since the public repository is "freshly initialized from the finalized state", this transformation
can **overwrite same-named files** (R1 immutable is the rule for the post-release collaboration phase).

Usage:
    # 1) See current distribution
    python scripts/retone_textures.py --stats
    # 2) Create standard from reference photo (one photo showing ideal appearance)
    python scripts/retone_textures.py --write-standard reference_photo.jpg
    # 3) Check appearance in sample (write before/after to out/)
    python scripts/retone_textures.py --preview out/ --sample 8
    # 4) Apply to all atlases (overwrite. Initial setup only, pre-release)
    python scripts/retone_textures.py --apply
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install Pillow (see requirements.txt)")

REPO_ROOT = Path(__file__).resolve().parent.parent
STANDARD_PATH = REPO_ROOT / "tools" / "tex_editor" / "tone_standard.json"
_IMG_SUFFIXES = {".jpg", ".jpeg", ".png"}
JPEG_QUALITY = 92


def iter_texture_files(bldg_dirs: list) -> list:
    out = []
    for d in bldg_dirs:
        for app_dir in sorted(d.glob("*_appearance")):
            out += [p for p in sorted(app_dir.iterdir()) if p.suffix.lower() in _IMG_SUFFIXES]
    return out


def image_stats(path: Path) -> "tuple[float, float, float, float, int]":
    """(Y mean, Y squared mean, Cb mean, Cr mean, pixel count). Downscaled for speed."""
    im = Image.open(path).convert("YCbCr")
    im.thumbnail((256, 256))
    y, cb, cr = im.split()
    hy = y.histogram()
    n = sum(hy)
    mean_y = sum(i * c for i, c in enumerate(hy)) / n
    mean_y2 = sum(i * i * c for i, c in enumerate(hy)) / n
    hcb = cb.histogram()
    hcr = cr.histogram()
    mean_cb = sum(i * c for i, c in enumerate(hcb)) / n
    mean_cr = sum(i * c for i, c in enumerate(hcr)) / n
    return mean_y, mean_y2, mean_cb, mean_cr, n


def aggregate_stats(files: list, sample: "int | None" = None) -> dict:
    """Weighted aggregate statistics over multiple images (Y mean/std dev, Cb/Cr means)."""
    if sample and len(files) > sample:
        files = random.Random(0).sample(files, sample)
    sy = sy2 = scb = scr = sn = 0.0
    for p in files:
        my, my2, mcb, mcr, n = image_stats(p)
        sy += my * n
        sy2 += my2 * n
        scb += mcb * n
        scr += mcr * n
        sn += n
    mean_y = sy / sn
    var_y = max(1.0, sy2 / sn - mean_y * mean_y)
    return {
        "y_mean": round(mean_y, 2),
        "y_std": round(var_y ** 0.5, 2),
        "cb_mean": round(scb / sn, 2),
        "cr_mean": round(scr / sn, 2),
        "files": len(files),
    }


def load_standard() -> dict:
    if not STANDARD_PATH.is_file():
        sys.exit(f"No standard found: {STANDARD_PATH} (create with --write-standard)")
    return json.loads(STANDARD_PATH.read_text(encoding="utf-8"))


def build_luts(src: dict, ref: dict) -> "tuple[list, list, list]":
    """Build the Y/Cb/Cr LUTs (0-255) applied identically to all images."""
    gain = ref["y_std"] / max(1e-6, src["y_std"])
    # Cap extreme contrast amplification, which would cause blown highlights
    gain = min(gain, 1.6)
    lut_y = [min(255, max(0, round((i - src["y_mean"]) * gain + ref["y_mean"]))) for i in range(256)]
    dcb = ref["cb_mean"] - src["cb_mean"]
    dcr = ref["cr_mean"] - src["cr_mean"]
    lut_cb = [min(255, max(0, round(i + dcb))) for i in range(256)]
    lut_cr = [min(255, max(0, round(i + dcr))) for i in range(256)]
    return lut_y, lut_cb, lut_cr


def _monotone_cubic_lut(xs: list, ys: list) -> list:
    """Build a 0-255 LUT via monotone cubic interpolation (Fritsch–Carlson) through the control points."""
    n = len(xs)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [(d[i - 1] + d[i]) / 2 if d[i - 1] * d[i] > 0 else 0.0 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:  # guarantee monotonicity
                t = 3.0 / (s ** 0.5)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    lut = []
    j = 0
    for x in range(256):
        while j < n - 2 and x > xs[j + 1]:
            j += 1
        h = xs[j + 1] - xs[j]
        t = (x - xs[j]) / h
        h00 = (1 + 2 * t) * (1 - t) ** 2
        h10 = t * (1 - t) ** 2
        h01 = t * t * (3 - 2 * t)
        h11 = t * t * (t - 1)
        y = h00 * ys[j] + h10 * h * m[j] + h01 * ys[j + 1] + h11 * h * m[j + 1]
        lut.append(min(255, max(0, round(y))))
    return lut


def build_lift_luts(lift: float) -> "tuple[list, list, list]":
    """Shadow lift: a curve that raises only the dark tones and leaves bright tones nearly untouched.

    The source textures were shot in sunny weather, so sunlit faces are properly exposed while
    shaded faces are extremely dark; therefore lift only the shadows instead of shifting exposure
    (this also works when sun and shade are mixed within one image).
    lift = "the value Y=60 (representative shade value) is raised to". Sunlit tones (Y≥150) are unchanged.
    """
    xs = [0, 60, 150, 255]
    ys = [min(20.0, lift / 4), float(lift), 152.0, 255.0]
    lut_y = _monotone_cubic_lut(xs, ys)
    ident = list(range(256))
    return lut_y, ident, ident


def retone_image(path: Path, luts, out_path: Path) -> None:
    im = Image.open(path)
    icc = im.info.get("icc_profile")
    ycc = im.convert("YCbCr")
    ycc = ycc.point(luts[0] + luts[1] + luts[2])
    rgb = ycc.convert("RGB")
    kwargs = {"quality": JPEG_QUALITY}
    if icc:
        kwargs["icc_profile"] = icc
    if out_path.suffix.lower() == ".png":
        rgb.save(out_path)
    else:
        rgb.save(out_path, "JPEG", **kwargs)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, default=REPO_ROOT)
    p.add_argument("--data", help="partial data package name (example: 13101)")
    p.add_argument("--stats", action="store_true", help="show current tone distribution")
    p.add_argument("--write-standard", type=Path, metavar="PHOTO",
                   help="generate standard (tone_standard.json) from reference photo")
    p.add_argument("--preview", type=Path, metavar="OUTDIR",
                   help="write and verify sample before/after")
    p.add_argument("--sample", type=int, default=8, help="sample count for --preview / --compare")
    p.add_argument("--compare", type=Path, metavar="OUT.jpg",
                   help="write one comparison sheet with candidate standards (Y mean) arranged")
    p.add_argument("--candidates", default="105,120,135,150",
                   help="Y mean candidates to arrange in --compare (comma-separated, default 105,120,135,150)")
    p.add_argument("--set-mean", type=float, metavar="Y",
                   help="write standard (tone_standard.json) with selected Y mean from comparison")
    p.add_argument("--make-variant", type=Path, metavar="OUTDIR",
                   help="generate set of converted textures in another directory (for 3D comparison)")
    p.add_argument("--mean", type=float, help="Y mean for --make-variant (exposure shift method)")
    p.add_argument("--lift", type=float,
                   help="shadow lift amount for --make-variant (raise dark areas only, example 110)")
    p.add_argument("--set-lift", type=float, metavar="L",
                   help="determine standard (tone_standard.json) by shadow lift method")
    p.add_argument("--mode", choices=["mean", "lift"], default="lift",
                   help="interpretation of --compare candidates (default lift=shadow lift)")
    p.add_argument("--mesh", help="filter --make-variant targets by prefix match of appearance folder name (example: 53394653)")
    p.add_argument("--apply", action="store_true",
                   help="convert all atlases to standard tone and overwrite (pre-release initial setup only)")
    args = p.parse_args()

    bldg_dirs = sorted(args.repo.glob("*/udx/bldg"))
    if args.data:
        bldg_dirs = [d for d in bldg_dirs if args.data in d.parent.parent.name]
    if not bldg_dirs:
        sys.exit("udx/bldg not found")
    files = iter_texture_files(bldg_dirs)
    if not files:
        sys.exit("No texture images found")

    if args.write_standard:
        st = aggregate_stats([args.write_standard])
        st["source"] = args.write_standard.name
        st["note"] = "City's target tone. Update by replacing reference photo + run this script with --write-standard"
        STANDARD_PATH.write_text(
            json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote standard: {STANDARD_PATH}\n{json.dumps(st, ensure_ascii=False, indent=2)}")
        return 0

    if args.set_lift is not None:
        st = {
            "mode": "shadow_lift",
            "shadow_lift": args.set_lift,
            "y_mean": 128.0,  # Interim target for submission-time correction (recommend updating from measurements after the batch conversion)
            "y_std": 56.0,
            "cb_mean": 128.0,
            "cr_mean": 128.0,
            "source": f"(--set-lift {args.set_lift}: selected from 3D comparison)",
            "note": "City's target tone (shadow lift method: raise only dark areas in shadows, leave sunny areas unchanged)",
        }
        STANDARD_PATH.write_text(
            json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote standard: {STANDARD_PATH}\n{json.dumps(st, ensure_ascii=False, indent=2)}")
        return 0

    if args.set_mean is not None:
        st = {
            "y_mean": args.set_mean,
            "y_std": 56.0,
            "cb_mean": 128.0,
            "cr_mean": 128.0,
            "source": f"(--set-mean {args.set_mean}: selected from comparison sheet)",
            "note": "City's target tone. Referenced by both bulk conversion (retone_textures.py) and submission-time correction (tex_editor)",
        }
        STANDARD_PATH.write_text(
            json.dumps(st, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote standard: {STANDARD_PATH}\n{json.dumps(st, ensure_ascii=False, indent=2)}")
        return 0

    print(f"Target textures: {len(files)} ({', '.join(d.parent.parent.name for d in bldg_dirs)})")
    src = aggregate_stats(files, sample=400)
    print(f"Current: Y mean {src['y_mean']} / Y std dev {src['y_std']} / "
          f"Cb {src['cb_mean']} / Cr {src['cr_mean']} ({src['files']} texture samples)")

    if args.stats:
        if STANDARD_PATH.is_file():
            ref = load_standard()
            print(f"Standard: Y mean {ref['y_mean']} / Y std dev {ref['y_std']} / "
                  f"Cb {ref['cb_mean']} / Cr {ref['cr_mean']}")
        return 0

    if args.compare:
        # Compose a single comparison sheet with the candidates (Y means) side by side
        from PIL import ImageDraw

        cands = [float(x) for x in str(args.candidates).split(",")]
        # Prefer wall textures as judgment material (take more than half from Wall-type files)
        walls_f = [f for f in files if "wall" in f.name.lower()]
        others_f = [f for f in files if "wall" not in f.name.lower()]
        rng = random.Random(1)
        n_wall = min(len(walls_f), max(args.sample * 2 // 3, args.sample - len(others_f)))
        picks = rng.sample(walls_f, n_wall) + rng.sample(others_f, min(len(others_f), args.sample - n_wall))
        # Prefer larger faces (wide textures) for easier viewing
        picks.sort(key=lambda f: -Image.open(f).size[0])
        cell_h = 200
        label_h = 26
        if args.mode == "lift":
            cols = [("Current", None)] + [(f"lift={c:.0f}", build_lift_luts(c)) for c in cands]
        else:
            cols = [("Current", None)] + [
                (f"Y={c:.0f}", build_luts(src, {"y_mean": c, "y_std": 56.0, "cb_mean": 128.0, "cr_mean": 128.0}))
                for c in cands
            ]
        rows = []
        for f in picks:
            im0 = Image.open(f).convert("RGB")
            w = max(1, round(im0.width * cell_h / im0.height))
            cells = []
            for _, luts_c in cols:
                if luts_c is None:
                    cells.append(im0.resize((w, cell_h)))
                else:
                    ycc = im0.convert("YCbCr").point(luts_c[0] + luts_c[1] + luts_c[2])
                    cells.append(ycc.convert("RGB").resize((w, cell_h)))
            rows.append(cells)
        col_w = max(c.width for r in rows for c in r) + 8
        sheet = Image.new("RGB", (col_w * len(cols), label_h + (cell_h + 8) * len(rows)), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for j, (label, _) in enumerate(cols):
            draw.text((j * col_w + 8, 6), label, fill=(255, 255, 255))
        for i, cells in enumerate(rows):
            for j, cell in enumerate(cells):
                sheet.paste(cell, (j * col_w + 4, label_h + i * (cell_h + 8)))
        args.compare.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(args.compare, quality=90)
        print(f"Wrote comparison sheet: {args.compare}")
        print(f"  Columns: Current (Y{src['y_mean']}) / " + " / ".join(f"Y={c:.0f}" for c in cands))
        print("  After selecting: python scripts/retone_textures.py --set-mean <value> to finalize the standard")
        return 0

    if args.make_variant:
        # Write a full set converted with the candidate tone to a separate directory (preserving bldg-relative paths)
        # -> display it via the editor's --textures swap and compare in 3D
        if args.lift is not None:
            luts_v = build_lift_luts(args.lift)
            tone_desc = f"lift={args.lift:.0f}"
        elif args.mean is not None:
            luts_v = build_luts(src, {"y_mean": args.mean, "y_std": 56.0,
                                      "cb_mean": 128.0, "cr_mean": 128.0})
            tone_desc = f"Y={args.mean:.0f}"
        else:
            sys.exit("specify --lift or --mean for --make-variant")
        targets = files
        if args.mesh:
            targets = [f for f in files if f.parent.name.startswith(args.mesh)]
            if not targets:
                sys.exit(f"No matches: appearance folder {args.mesh}*")
        bldg_root = {d: d for d in bldg_dirs}
        done = 0
        for f in targets:
            # Keep the path relative to the bldg directory
            base = next(d for d in bldg_dirs if str(f).startswith(str(d)))
            out = args.make_variant / f.relative_to(base)
            out.parent.mkdir(parents=True, exist_ok=True)
            retone_image(f, luts_v, out)
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(targets)}")
        print(f"Generated variants: {args.make_variant} ({tone_desc}, {done} textures)")
        print(f"  Display: python3 tools/tex_editor/app.py --textures {args.make_variant} --port <different-port>")
        return 0

    ref = load_standard()
    if ref.get("mode") == "shadow_lift":
        luts = build_lift_luts(ref["shadow_lift"])
        print(f"Standard: shadow lift method lift={ref['shadow_lift']}")
    else:
        luts = build_luts(src, ref)

    if args.preview:
        args.preview.mkdir(parents=True, exist_ok=True)
        picks = random.Random(1).sample(files, min(args.sample, len(files)))
        for f in picks:
            before = args.preview / f"before_{f.name}"
            after = args.preview / f"after_{f.name}"
            Image.open(f).save(before)
            retone_image(f, luts, after)
        print(f"Wrote preview: {args.preview} ({len(picks)} pairs)")
        return 0

    if args.apply:
        print("Will convert and overwrite all atlases (pre-release setup only; can undo with git)")
        for i, f in enumerate(files, 1):
            retone_image(f, luts, f)
            if i % 200 == 0:
                print(f"  {i}/{len(files)}")
        print(f"Done: {len(files)} textures converted to standard tone")
        return 0

    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
