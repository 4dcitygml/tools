# CityGML LOD2 Texture Editor

A local tool specialized for **replacing or newly adding facade photos (LOD2 textures)**
of buildings. A sister tool of the [attribute editor](../attr_editor/README.md), it
provides one flow: "pick a building on a 2D map → pick a face in the 3D/face list →
crop a photo and paste it → PR".
Photos can also be **newly added** to LOD2 data with no textures, using the same
operations (#119).

## Launch

```bash
python3 tools/tex_editor/app.py        # → opens http://localhost:8766
```

- The only dependency is Python 3.9+ (shared mechanisms are reused by loading
  attr_editor/app.py).
- When launched from inside the clone, `--repo` is auto-detected. It can run alongside
  the attribute editor (different ports).
- As in the attribute editor, mesh selection is intentionally manual: building
  footprints are loaded only after the user clicks a mesh frame, never automatically
  merely because the map was panned or zoomed.

## Usage

1. Click a mesh frame on the map → click a building (buildings with LOD2 are eligible.
   Buildings without textures enter **new-texture mode**, where you paste onto a plain
   placeholder with the same operations)
2. Select a wall via the **wall cards** in the right pane (composite facade thumbnails)
   or by **clicking a face in the 3D view**
   - Because LOD2 walls are split into fine polygons per storey band, editing is done in
     **"wall" units grouped by normal direction** (e.g. a 40-face high-rise → 4 walls +
     roof etc.). Roofs and the like are edited per face
3. Drag & drop a photo → **drag the four corners in the order top-left → top-right →
   bottom-right → bottom-left** to fit the whole wall (tilt and keystone are corrected
   automatically) → [Apply] **bakes it automatically onto all polygons composing the
   wall** and previews in 3D
   - The baking solves a per-face affine transform by least squares from the one-to-one
     correspondence between textureCoordinates and polygon vertices, so orientation,
     flips, and thin strips are handled accurately
4. After applying, the **before/after 3D buildings are shown side by side** above the
   flat image comparison in the right pane. Rotating or zooming either one synchronizes
   the other to the same viewpoint, so you can directly compare how it blends into the
   whole building. Below that, **before/after flat images** are shown per wall/face. The
   editing 3D view on the left can also be temporarily reverted to the original texture
   with [Switch editing 3D to before]
5. Multiple walls/faces can be edited together (within the same building only) → when
   satisfied, [Create pull request]. The generated PR title and body are written in the
   **repository's working language** (`lang` in `4dcitygml.json`; see the hub README's
   "Language policy") — commit messages stay English.

### Camera-alignment mode (batch pasting from a single photo)

From a single street-corner shot, you can update all faces visible in the photo
(including penthouse and set-back sides) at once.

1. [📷 Batch paste from one photo] → drop the photo
2. Click pairs of corresponding points, in the order **yellow vertex marker in the 3D
   view → the same spot in the photo**, at least 6 pairs
   - **If you pick a point at the bottom edge of a wall, a large instruction appears**:
     in the photo, click not the foot of the wall but **the point directly below the
     roof corner (the boundary with the ground)**. The model's walls are surfaces
     dropped vertically from the roof outline (eaves line), so on buildings with eaves
     overhang they do not coincide with the real wall foot
     (the generic fix for the #109 correspondence offset; taking the roof corner and
     the point directly below it gives vertically separated correspondence points and
     stabilizes the alignment)
3. [Align] → the camera pose is estimated (normalized DLT); confirm that the light-blue
   wireframe overlaps the building in the photo (the reprojection error is shown; if it
   is off, add correspondence points)
4. [Create paste candidates] → generates a **visibility-tested patch** per face, accept
   or reject with checkboxes (the visible fraction is shown; preview in 3D) → [Apply]

**Invisible pixels keep the original texture** (pixels behind the camera, occluded by
the building itself, or outside the frame are excluded by a z-buffer test, and
boundaries are feathered). Repeating with photos from other angles gradually updates
the whole building. Note: **occluders other than the building itself** — neighboring
buildings, street trees, etc. — cannot be detected, so uncheck faces where they appear.
   - Submitted photos are provided under **CC0 1.0** with your consent
     ([data contribution policy](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md), including
     care for faces, license plates, etc.)

## Editing method — atlas baking (key design points)

In real data the structure is "1 image = 1 ParameterizedTexture, no image sharing
between buildings, multiple faces of the same building sharing one image (atlas)", so a
replacement is:

1. Perspective-correct the photo in a browser canvas and **bake it into the UV region
   of a copy of the original atlas image**
2. **Add the new image under a content-addressed name (`tex_<first 12 of sha256>.jpg`)**
   (existing images are untouched = R1)
3. In the GML, **replace exactly one `app:imageURI` leaf value with a
   uniqueness-verified match** (UV and XML structure unchanged)

This keeps the diff minimal — "1 GML line + 1 new image file" — and stays consistent
with the existing CI (texture_check counts it as one building with an appearance change
(a) → classification=single, and the R3 dangling check passes because the new image is
committed together). In the unlikely case that an atlas is shared across multiple
buildings, that face is shown as non-editable (a safety net).

## Tone matching

- At apply time, submitted photos are **auto-corrected to "the current tone of the face
  being replaced"** (after the batch conversion, the city has sun/shade directionality
  baked in, so we match the per-face measured target rather than a city-wide goal:
  north faces stay north-like, south faces stay south-like). In YCbCr, luma uses mean
  and variance, chroma uses mean only = white balance. Saturation is preserved, so
  intentional color changes such as repainting are not lost. Comes with a "strength"
  slider and manual brightness / color-temperature fine-tuning.
  `tone_standard.json` is the standard for the batch conversion (initial preparation)
- Batch conversion of all existing textures (initial preparation before publication) is
  `scripts/retone_textures.py`
  (--stats / --write-standard reference photo / --preview / --apply; uses Pillow).
  The division of roles: the batch side applies **a single transform for the whole
  city** (preserving per-building light/dark character), while the submission side
  matches **each photo** to the standard (absorbing differences in shooting
  conditions)

### Comparing tones as a team (the play-off script)

```bash
python3 tools/tex_editor/tone_battle.py                 # opens 4 tabs: original / lift=100 / 115 / 130
python3 tools/tex_editor/tone_battle.py --lifts 105,120       # change the candidates
```

The 3D check revealed that "the darkness is not underexposure but **baked-in shade**
(sunlit faces are correctly exposed)", so the default is the **shadow-lift method** (a
tone curve that lifts only the dark parts; sunlit areas unchanged).
It works correctly even when sunlit and shaded faces coexist in one atlas. Once
decided, fix the standard with `python3 scripts/retone_textures.py --set-lift <value>`.

Converted textures for the candidate tones are generated automatically (a few minutes
the first time; Pillow required), and tabs opening the same building are lined up per
tone (the tone name is shown in the header and the header color changes too, so no
mix-ups). Press LOD2 → texture in the 3D panel to compare; use the `--set-lift` command
above to record the chosen value.

### Shooting tips

- **Preferably in overcast weather or shaded hours** (in sunshine, sun shadows are
  baked into walls and cannot be removed by correction)
- Face the wall as squarely as possible, and keep the subject near the center of the
  photo to avoid lens distortion
- For care regarding faces, license plates, etc., see the
  [data contribution policy](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md)

## Scope of support

- **Replacement** of faces that already have textures (v1)
- **New addition** to buildings without textures (#119):
  - **One atlas per building, new path only the first time**: on first addition, an
    atlas is generated defining the UVs of all the building's LOD2 wall faces at once
    (unphotographed walls get plain placeholder pixels). **From the second time on it
    merges into replacement mode**
  - UVs are generated from the wall coordinates s×h of the wall clusters (normal
    direction). **Walls only** (new pasting onto roof faces and deletion are out of
    scope)
  - The only write to the GML is inserting one appearance block (`xmlns:app` is
    declared element-locally; existing elements and geometry unchanged; BOM/CRLF
    preserved; consistent with R1/R3 and the CI checks; XSD-validated)
  - New additions get no automatic tone correction (there is no "current tone of the
    face being replaced"; manual brightness and color-temperature adjustment is
    available)
- Because baking re-encodes the whole atlas as JPEG (quality 0.92), other faces in the
  same atlas are also slightly re-compressed (visually essentially unchanged)
