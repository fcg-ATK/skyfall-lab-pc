# Third-party notices

The root MIT license covers project-authored code and original creative contributions only. It does not replace the licenses of the resources below, including when those resources are embedded in an FBX, GLB, material or Unreal asset.

## Poly Haven resources — CC0 1.0

| Bundled source | Upstream asset | Creator |
| --- | --- | --- |
| `SourceAssets/Textures/concrete_surface.jpg` | [Concrete](https://polyhaven.com/a/concrete) | Rob Tuytel |
| `SourceAssets/Textures/concrete_surface_normal.png` | [Concrete](https://polyhaven.com/a/concrete) | Rob Tuytel |
| `SourceAssets/Textures/asphalt.jpg` | [Asphalt 02](https://polyhaven.com/a/asphalt_02) | Rob Tuytel |
| `SourceAssets/Textures/daylight_environment.hdr` | [Urban Street 01](https://polyhaven.com/a/urban_street_01) | Andreas Mischok |

These textures are also embedded or imported in `SourceAssets/BuildingSource/building-v4.glb`, the building / fracture source assets, `Content/Skyfall/Textures/T_*.uasset`, and project materials. Geometry and material logic authored for this project remain MIT; the underlying Poly Haven image content remains CC0.

License: [Poly Haven license statement](https://polyhaven.com/license), [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/), [CC0 legal text](https://creativecommons.org/publicdomain/zero/1.0/legalcode). A local source and license notice is provided in `SourceAssets/Textures/SOURCES.md`.

## Noto Sans SC — SIL Open Font License 1.1

`SourceAssets/UI/Fonts/NotoSansSC-Regular.otf` and `NotoSansSC-Bold.otf` are unmodified font files; only their filenames were simplified. Their font-table copyright notice is **© 2014-2020 Adobe (http://www.adobe.com/)**. The four imported assets under `Content/Skyfall/UI/Fonts` retain this font content and the same license obligations.

The complete OFL license and source notes are included in [OFL.txt](SourceAssets/UI/Fonts/OFL.txt) and [SOURCES.md](SourceAssets/UI/Fonts/SOURCES.md). Upstream: [Noto CJK](https://github.com/notofonts/noto-cjk), [official license](https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE). These fonts are not relicensed under MIT.

## Epic Games / Unreal Engine — external dependency

Unreal Engine 5.8 and its engine resources are not bundled or relicensed. Asset references beginning with `/Engine/` or `/Niagara/` resolve against the user's installed engine. Engine use and distribution remain subject to [Epic's Unreal Engine EULA](https://www.unrealengine.com/eula/unreal).

The following generated packages are intentionally omitted from this source distribution:

- `Content/Skyfall/CombatV2/Effects/NS_HighEnergyImpact.uasset`
- `Content/Skyfall/CombatV2/Effects/NS_LocalDust.uasset`
- `Content/Skyfall/CombatV2/Effects/NS_RocketExhaust.uasset`
- `Content/Skyfall/Meteor/NS_MeteorExit.uasset`
- `Content/Skyfall/Meteor/NS_MeteorImpact.uasset`
- `Content/Skyfall/Meteor/NS_MeteorTail.uasset`

`SetupProject.cmd` generates these locally using the installed Niagara templates `Fountain`, `UpwardMeshBurst` and `OmnidirectionalBurst`. Original Python configuration code is MIT; Epic's template content and derived Niagara packages are not granted an MIT license by this project. Do not add the generated packages to a public source export under this project's MIT license.
