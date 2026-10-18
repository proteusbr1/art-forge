# Provenance of the code

The history of this repository starts clean. Part of its first code was seeded from the
**author's earlier private factory**, a local ComfyUI art factory written by the same
author for an earlier project. The seeded files were copied from that factory at source
revision `83783ef4201f95edcf29fd9a8cdf0b3e5b1c2821`; the SHA-256 below is the hash of
each source file as stored at that revision. One workflow came from a second private
repository of the same author (see the end of this page).

Every seeded file was then adapted: imports for the `forge` package, names and defaults
that pointed to the earlier project removed, and the extensions described in
[order.md](order.md). "identical" means the file in this repository still has the same
bytes as its source.

## Factory core

| Source file | Here | Source SHA-256 | State |
|---|---|---|---|
| `comfy/art_factory.py` | `forge/__main__.py` | `7c4f6b247d5b9e765d9dc12637f8143e0a83f9d95fd4fedf29cce6a4f412067a` | modified: `--order`, `--config`, `--server`, `--check`, `--order-base`; output folder inside the repository refused |
| `comfy/comfy_jobs.py` | `forge/comfy_jobs.py` | `f8aa784630e52a6036c0e40a2841f934e145eb0136598dd2b4bdccb2efca38e7` | modified: save prefix renamed, `{"file": path}` inputs, `drop` |
| `comfy/comfy_server.py` | `forge/comfy_server.py` | `b7929f1072c8893cf596482158c026e9391d0489f39ff540f05749a328c49685` | modified: client id renamed, address from the configuration |
| `comfy/factory_core.py` | `forge/factory_core.py` | `7c0142210820a33d3675e68debd5f1456a3c7d206c9bc800de79459e14569d37` | modified: loads the order through `forge.order`, order input files in the specification hash, `load_workflow` as a function, `FORGE_*` waiting variables |
| `comfy/factory_util.py` | `forge/factory_util.py` | `d990bf42f67c64db7769cd4dfdd8ad0ffa9b6811c9ae900f4814321a12967bc9` | modified: cached hash of order input files |
| `comfy/local_steps.py` | `forge/local_steps.py` | `e7a11ff4b323130541dfcad6a0f3eb0b5d5d3fe62cf23b65a09bc518bb2aea82` | modified: package imports |
| `comfy/provenance.py` | `forge/provenance.py` | `bc918ea860d8dcced658535f0b995fca3d61b992cfe3c3d0e4330aed56832771` | modified: format name, package imports |
| `comfy/recipes.py` | `forge/recipes.py` | `785be7ccf0d89402684470e6a00771d1d37a83e1f3c1d9cf3ebb50d38f4a15eb` | modified: `variants`, `params`, `inputs.reference` |
| `comfy/scoring.py` | `forge/scoring.py` | `66ed450657fd38ae4533d3be0bae4439ff3fce6fad9edfbfad22ced282d7b1df` | identical |
| `Run-ArtFactory.ps1` | `Run-Forge.ps1` | `d463fe39e53e9f549d3a164486c40317a453638cc0515c07f45e18ff9716f9e4` | modified: `-Order`, `-Out` required and outside the repository, `-Check`, machine configuration |

New in this repository: `forge/config.py`, `forge/order.py`, `forge/stages.py`,
`forge/__init__.py`.

## Tests

| Source file | Here | Source SHA-256 | State |
|---|---|---|---|
| `tests/art/fake_comfy.py` | `tests/fake_comfy.py` | `9bf7f99c075d84dcd133db43b7d96ed7734727771b79399b61ae1784413f6a58` | modified: save prefix renamed |
| `tests/art/jobs_test.json` | `tests/fixtures/jobs_test.json` | `63fa49b8de01138eff9988fffa9a259c44c097f124c6e5b3046f9bdccd4489fb` | modified: note |
| `tests/art/test_art_factory.py` | `tests/test_factory.py` | `36a3d41537391804532f5d66b631a3a31cfbe85ac09019075673fbda8d9c881a` | modified: paths and `python -m forge` |
| `tests/art/test_workflow_hash.py` | `tests/test_workflow_hash.py` | `719a072d1d92238e26c9c7062941be267791408a70034056ada3e621a624e5b5` | modified: package imports |
| `tests/art/test_character_lab.py` | `tests/test_comfy_jobs.py` | `ac5ac67b62a75f5bc2715cd6328c70a29d24a503ddcaf7096919b538f59d9a4a` | only its two prompt-building tests, with generic names; the tests of the earlier research tools did not come |

New in this repository: `tests/test_order.py`, `tests/test_inputs_hash.py`,
`tests/test_workflows.py`, the `drop` test in `tests/test_comfy_jobs.py` and
`tests/fixtures/order_generic.json`.

## Workflows

Only production workflows were copied. The earlier factory's research workflows did not
come; two of them were the starting point of derived workflows.

| Source file | Here | Source SHA-256 | State |
|---|---|---|---|
| `workflows/qwen_image_21_turbo_t2i.json` | same name | `72189b659fe5ee8874e693f3ff75df16f869f365ab1b59481d9dcaf3a6036b1f` | identical |
| `workflows/qwen_image_21_turbo_hires.json` | same name | `91804b08b2f871a94707ed47321e48c62c529abbc1f210c5e854f2e8bae32917` | identical |
| `workflows/qwen_image_21_turbo_inpaint.json` | same name | `1c36085ed24cf5ec7b5397d79367fe6468b5ac12554dcd2c0e25ed66363fd592` | identical |
| `workflows/qwen_image_21_turbo_tile.json` | same name | `7e23bd1edfd8c51f1d091a509f16242fbf3a2caff5153874055b45392cc9fefd` | identical |
| `workflows/qwen_edit_2511.json` | same name | `4386d2ed6c41eb67ed8ead61fe1c7e0b146a29fa787cc649f751afe37952a4db` | identical |
| `workflows/qwen_edit_character.json` | same name | `46ac4af3bbc85593e98923fcb11d3540dc30b1d4b03cc2b97c441a6a947aaa9e` | identical |
| `workflows/qwen_edit_view_angles.json` | same name | `0663e0f0b6ea3420ad876ff886885c7357432564091272bf11b46da181aed09b` | modified: the two save prefixes became `$PREFIX$` (now `09796743e45d8db1905517b54a0ab9a40ac72ea8b245cffbeec13749b8a7fcbe`) |
| `workflows/klein_t2i.json` | same name | `8294165e4428f8d59aa183bbe29f221f07348136a762656f7701831544030122` | identical |
| `workflows/hunyuan21_shape_paint.json` | same name | `4b67ba53f449d670e238e630a93b6d460146f06ea04dd48039757765e8964c1a` | modified: a second post-process without face reduction and its export (nodes 15 and 16: the shape before the reduction, the bake source of a humanoid) (now `a9dc666b0cf2b25b73725b69278496abe34a4186a9c6d9761db35ae48b368745`) |
| `workflows/lab_trellis_1024_remesh.json` (research) | `trellis2_mesh.json` | `622fcb7af5405d57ce8ca52f64f8e4419f1edb3581d25504a743aa0789d7811c` | derived: `DecimateMesh` target `$TRIS$`, the untextured save now takes the remeshed and decimated shape, nodes sorted, then a second save of the remeshed shape before the reduction (nodes 404 and 405, the bake source of a humanoid) (now `27d95d4d3ff85b811c34a060e529890ef499066d190cb75ad285271647f0e1b1`) |
| `workflows/lab_hunyuan_paint.json` (research) | `hunyuan21_paint_mesh.json` | `0f28d0523671bf441ab2d4ebdf481ff2c527754af22610ab6d50ffd51ac10920` | derived: texture size `$TEXTURE$`, nodes sorted (now `87a707182ce8c3cac0c13ce2531e5abdb7f533c4b6d64d13df269a0dcdbff45d`) |

`qwen_image_21_base_t2i.json` was derived from `qwen_image_21_t2i.json`, a production
workflow of a second private repository of the same author, at revision
`9d7e15c4f09036d3a053de410026443372e4bf47` (source SHA-256
`dba7e2591942e046647606eb98e5ee5b0529749f85ea4db9ea3a12ad04640749`): size markers
`$WIDTH$` and `$HEIGHT$`, 60 steps and CFG 1.0 fixed in the sampler (now
`12f8b37333381acf8c66642fa350d30ea08f8de407695778c7e1cff7ffa88c66`).

## Humanoid tools

Ported from the second private repository of the same author (the humanoid exporter at
revision `6d82bb3df910a0299f7eb39acebeea6ae0c18e28`) and from the earlier factory's
research tools (the SkinTokens entry at revision `620f632c1ecfd0ce566172f971fa863b265402cb`):

| Source file | Here | Source SHA-256 | State |
|---|---|---|---|
| `tools/arte/blender/exportar_modelo_3d.py` | `tools/blender/humanoid_export.py` | `0cac16dc0f631142b00ef82f6fa5abe3fa11b171d5e6c59d3a2068fbcef6001c` | generic port: retarget by bone directions, IK feet and hands, item bones, normal bake, region mask and texture relief kept; the project's recolouring, level heights, per-tile phases, unit options and fixed hold placements removed (hold placements are now motion-library fields); new: validated bone map, bones renamed to Godot's humanoid profile, height in metres, bake source, mesh and motion reports |
| `tools/arte/Rigar com UniRig.ps1` | `run_rig(..., method="unirig")` in `forge/local_gpu.py` | `f465aece37bad32f9a4bf441a0fb018888ff244ea5721fdb6c0e77696c470ffc` | the same five python steps, run from Python |
| `tools/art/lab/skintokens_entry.py` | `tools/rig/skintokens_entry.py` | `297b8bd9f4ddae54b80c470476fed95b2d0511407efe30227d333ffed9a525df` | modified: messages |

The canonical joint names of the motion library (`docs/conventions.md`) are those of the
same author's motion tools. New in this repository: `tools/blender/humanoid_bones.py`,
`tools/review_godot/`, `forge/local_gpu.py`, `tests/fixtures/humanoid/` and their tests.

The workflows are stored without line-ending conversion (`.gitattributes`), so their
SHA-256, which enters every job's specification and provenance, is the same on every
machine.
