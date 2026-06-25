# Film-simulation LUTs (`luts/`)

The film-simulation list is **dynamic**: a look appears in the app only if its
`<key>.cube` file exists in this folder, and is hidden otherwise. So you can add
or replace LUTs without touching code — just match the filename (key) below.
Any cube size works (the loader auto-detects N).

## Keys (filename → simulation)

**Color** — bundled, licensed **CC BY-NC-SA 4.0** (see [`LICENSE`](LICENSE)):

```
provia  velvia  astia  classic_chrome  classic_neg  nostalgic_neg
pro_neg_hi  pro_neg_std  eterna  reala_ace  bleach_bypass
```
(`identity` = "None" / no LUT applied)

**B&W** — **NOT bundled**, add your own:

```
acros  acros_ye  acros_r  acros_g  monochrome  sepia
```

## Adding the B&W simulations

These are not shipped (license, see below). To enable them, drop matching
`.cube` files into this folder using the key filenames above
(e.g. `acros.cube`, `acros_ye.cube`, `monochrome.cube`, `sepia.cube`).
They appear in the **Film Simulation** selector automatically on next launch.

A good source is **Stuart Sowerby's Fuji film-simulation profiles**:
<https://blog.sowerby.me/fuji-film-simulation-profiles/>
Convert each to a `.cube` (any size) and name it to the matching key.

> ⚠️ Stuart Sowerby's profiles are © Stuart Sowerby, **All Rights Reserved**, and
> are **not redistributed by this project**. Obtain them yourself and keep them
> local (they're git-ignored here).

## Replacing / updating the color LUTs

To use different or more accurate color LUTs, just overwrite the same
`<key>.cube` filename — no code change needed. The bundled color cubes are
derived from [FujifilmCameraProfiles](https://github.com/abpy/FujifilmCameraProfiles)
(CC BY-NC-SA 4.0). `make_luts.py` can also bake approximate fallbacks.
