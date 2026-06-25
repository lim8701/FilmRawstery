# Film Rawstery

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6-Qt-41CD52?logo=qt&logoColor=white)
![RAW](https://img.shields.io/badge/RAW-Fuji%20X100V%20(.RAF)-EB0A1E)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555)

A GPU-accelerated RAW developer and film-simulation editor for the **Fujifilm X100V** (`.RAF`), built with **PySide6 (QML) + GLSL shaders**.

Edit interactively on a real-time, shader-driven preview, then export at full resolution through a numpy pipeline that mirrors the shader exactly — *what you see is what you get*.

<p align="center">
  <img src="screenshot.png" alt="Film Rawstery — develop: file explorer, real-time preview, and develop panel" width="100%">
  <br><br>
  <img src="screenshot2.png" alt="Film Rawstery — masking: AI multi-class selection (people masked) with per-mask adjustments" width="100%">
</p>

> ⚠️ Tuned specifically for the Fuji X100V (X-Trans sensor + lens profile). Other cameras may decode but won't match the lens/color tuning.

---

## Why I built this

A hobby project, built for my own use.

I shoot a lot with the **Fujifilm X100V** and edit in Lightroom — but I only use a few of its features, so paying for a subscription felt hard to justify. Film Rawstery bundles just the features I actually need into a workflow tuned the way I like it.

If I pick up another camera down the line, I plan to add support for it too.

### The name

*Film Rawstery* is a play on **roastery**. A coffee roastery takes raw beans and roasts them into something worth drinking; this app does the same with **RAW** files — raw sensor data developed and refined into a finished photo — hence **Raw**stery. The *Film* nods to the Fujifilm film-simulation looks at its heart.

---

## Features

### Develop
- **Scene-linear + filmic** tone pipeline — physically-grounded base render with a single highlight-rolloff tone curve (no per-scene heuristics)
- **White balance** — absolute Kelvin + tint via the Planckian locus, with as-shot estimation for off-locus illuminants
- **Light** — exposure (scene-linear stops), contrast, highlights / shadows / whites / blacks (Lightroom-style local tone zones)
- **Tone curve** — Catmull-Rom editor with **per-channel RGB** curves (master + R/G/B) for color grading
- **HSL color mixer** — 8 hue bands × hue / saturation / luminance
- **Color** — vibrance & saturation
- **Detail** — texture, clarity, dehaze, and **sharpening** (amount / radius / detail / masking)
- **Effects** — film grain, vignette
- **Highlight reconstruction** — hue-aware desaturation that neutralizes clipped-highlight color casts (e.g. a fire core) while preserving saturated colored light sources (neon, signs)

### Masking (local adjustments)
- **AI selection** — ONNX semantic segmentation (SegFormer-B2 / ADE20K) detects regions to mask; the model auto-downloads on first use
- **Multi-class composite** — tick any combination of **Sky / Vegetation / Building / Ground / Water / Mountain / Person**; the mask is their union, recomposed live from a single cached inference
- **Edge-refined soft mask** — guided-filter refinement against image luminance for clean branch/edge boundaries, plus invert and a red mask overlay
- **Per-mask develop** — Exposure / Temp / Tint / Highlights / Shadows / Texture / Clarity / Dehaze / Saturation, applied only to the masked region in both preview and export
- Masks persist per-image (regenerated from the saved classes on reopen)

### Film Simulations
Fujifilm looks as 3D LUTs: Provia, Velvia, Astia, Classic Chrome, Classic Negative, Nostalgic Neg, PRO Neg. Hi/Std, Eterna, Reala Ace, Bleach Bypass — with adjustable strength. The list is driven by the `.cube` files present in `luts/`, so any known LUT you drop in (e.g. B&W ACROS / Monochrome / Sepia) appears automatically, and missing ones are hidden. See [`luts/README.md`](luts/README.md) for the key filenames and where to get the B&W LUTs.

### Geometry
Crop (aspect-ratio presets + free drag), rotate / straighten, flip, and perspective (vertical / horizontal keystone + scale) — applied identically in preview and export.

### Lens Corrections
Built-in X100V profile: distortion, vignetting, and chromatic aberration.

### Workflow
- **Before / After compare** — toggle the unedited original (button or `\` key)
- **Undo / redo** — snapshot history of all adjustments (`Ctrl+Z` / `Ctrl+Shift+Z`)
- **Non-destructive, per-image persistence** — edits autosave to a `.filmrawsteryedits/<file>.json` sidecar and restore when you reopen the image
- **File explorer** with RAF thumbnails and a likes/favorites filter
- **Film date stamp** — DSEG7 seven-segment date back overlay
- **Live histogram** reflecting current adjustments
- **Full-resolution export** to JPEG / PNG / TIFF (background-threaded, UI stays responsive)

---

## How it works

```
RAF ──rawpy──► camera-native proxy (≤2560px, headroom-encoded)
                     │
       QML ShaderEffect pipeline (GPU, proxy-resolution FBO → scaled to screen)
       headroom-decode → WB → cam→sRGB matrix → ×2^exposure → filmic
       → tone zones → texture/clarity/dehaze → sharpen → film-sim LUT
       → vibrance/sat → HSL mixer → contrast → tone curve → mask local adjust → vignette → grain
                     │
   live preview (GPU)        Export: pipeline.py (full-res numpy, same steps)
```

Key design decisions:
- **Processing resolution ≠ display resolution** — the pipeline always renders at a fixed proxy resolution and scales to screen, so GPU load is independent of monitor size.
- **Preview = Export parity** — the GLSL shaders (`shaders/adjust.frag`) and the numpy export (`pipeline.py`) implement the same steps and formulas; strength coefficients live in a single `coeffs.py`, injected into the shader as uniforms so a change updates preview and export together.
- **Color science first, look-matching second** — algorithms are physically/colorimetrically correct; strengths and curves are then tuned to feel like Adobe Lightroom.

---

## Requirements

- Python 3.13 (3.11+ should work)
- `PySide6`, `rawpy`, `numpy`, `scipy`, `exifread`, `onnxruntime` (see [`requirements.txt`](requirements.txt))
- A GPU/driver supporting the Qt RHI (OpenGL / Direct3D / Metal / Vulkan)
- The masking model (SegFormer-B2 ONNX, ~105 MB) auto-downloads to `models/` on first use — needs an internet connection the first time (see [`models/README.md`](models/README.md))

## Install & Run

```bash
pip install -r requirements.txt
python main.py
```

Open a `.RAF` from the left file explorer (double-click). Shaders auto-recompile from `shaders/*.frag` on launch when changed.

---

## Project structure

| Path | Role |
|------|------|
| `main.py` | App entry point, controller, image providers (raw / lut / curve / stamp / thumb) |
| `raw_loader.py` | RAF → display proxy (X-Trans-safe decode, headroom encoding, lens correction) |
| `pipeline.py` | Full-resolution export — numpy reproduction of the shader pipeline |
| `sky_seg.py` | ML masking engine — ONNX SegFormer multi-class segmentation → composite soft mask |
| `coeffs.py` | Single source of truth for adjustment strength coefficients (shader uniforms + pipeline) |
| `wb.py` | White balance (Kelvin/tint), cam→sRGB matrix, filmic curve, auto-exposure |
| `lens.py` | X100V lens profile (distortion / vignetting / CA) |
| `lut.py`, `make_luts.py` | `.cube` 3D LUT loading / baking |
| `date_stamp.py`, `exif_info.py` | Film date-back rendering / EXIF extraction |
| `Main.qml`, `CurveEditor.qml`, `PreviewWindow.qml` | UI |
| `shaders/adjust.frag` | Main develop pipeline (fragment shader) |
| `shaders/blur.frag`, `shaders/convert.frag` | Separable blur (local contrast) / display-space base |
| `luts/*.cube` | Film-simulation LUTs |

---

## License

A hobby project — shared so others can use and learn from it.

- **Source code & original assets** — [MIT](LICENSE). Use, modify, and redistribute freely (including commercially).
- **Film-simulation LUTs** (`luts/*.cube`) — **CC BY-NC-SA 4.0** (attribution · **non-commercial** · share-alike), derived from [FujifilmCameraProfiles](https://github.com/abpy/FujifilmCameraProfiles); see [`luts/LICENSE`](luts/LICENSE). The code is reusable commercially, but the bundled LUTs are not.
- **Masking model** — downloaded at runtime under its own research-oriented license; see [`models/README.md`](models/README.md).

> Bundled third-party components keep their own licenses; the MIT grant covers this project's own code and assets only.

---

## Credits

- **Film-simulation LUTs** — derived from the [*FujifilmCameraProfiles*](https://github.com/abpy/FujifilmCameraProfiles) project (sRGB `.cube`), licensed CC BY-NC-SA 4.0
- **Date-back font** — [DSEG](https://github.com/keshikan/DSEG) by Keshikan (SIL Open Font License 1.1)
- **Masking model** — SegFormer-B2 finetuned on ADE20K, ONNX export by [Xenova](https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512) (transformers.js). ⚠️ Research-oriented license — verify before commercial use; see [`models/README.md`](models/README.md)
- **ONNX inference** — [ONNX Runtime](https://onnxruntime.ai/)
- **RAW decoding** — [rawpy](https://github.com/letmaik/rawpy) / LibRaw
- **UI & GPU pipeline** — [Qt for Python (PySide6)](https://doc.qt.io/qtforpython/)
- Plus [NumPy](https://numpy.org/), [SciPy](https://scipy.org/), and [ExifRead](https://github.com/ianare/exif-py)
