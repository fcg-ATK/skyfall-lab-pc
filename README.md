# 崩塌实验室 · PC 版

使用 **Unreal Engine 5.8** 制作的原生 Windows 电脑版工程：办公楼拆除场景、火箭 / 陨石 / 铁球、局部破坏与连锁坍塌、中文界面和 360 度绕楼观察。

这是独立的 UE 工程，**不是网页版**。当前仓库不提供脱离引擎运行的 Windows `.exe` 或安装包；试玩需要安装 UE 5.8。手机版在另一个仓库：[fcg-ATK/skyfall-lab](https://github.com/fcg-ATK/skyfall-lab)。两版分别维护，本仓库无需下载手机版仓库或其他工作区目录。

## 首次运行

1. 在 Windows 安装 Unreal Engine **5.8**，保留随引擎安装的 Niagara、NiagaraToolsets、PythonScriptPlugin、EditorScriptingUtilities、EditorToolset 和 GeometryScripting 插件。项目采用 DX12 / SM6 与 Lumen，需要相应显卡和驱动；建议使用 SSD。开发时使用的版本为 5.8.4。
2. 下载本仓库全部文件并解压，或使用 `git clone`。不要单独下载 `.uproject`。
3. 双击 **`SetupProject.cmd`**。它从你本机已安装的 UE 模板生成六个 Niagara 特效并编译着色器，首次执行可能需要数分钟。成功后窗口显示 `Setup complete`。
4. 双击 **`LaunchSkyfall.cmd`**，以 1600 × 900 窗口进入游戏。也可双击 `SkyfallPC.uproject` 打开编辑器；首次仍须先完成 Setup。

脚本会查找 Epic Launcher 的引擎安装记录与常见安装目录。如果未找到引擎，设置环境变量 `UE_ROOT` 为含有 `Engine` 子目录的 UE 5.8 安装目录，或在命令提示符传入：

```bat
SetupProject.cmd -EngineRoot "E:\Epic Games\UE_5.8"
LaunchSkyfall.cmd -EngineRoot "E:\Epic Games\UE_5.8"
```

`SetupProject.cmd` 使用引擎内置 Python，不需要另装 Python。它只重建特效资源及包装蓝图，不重建玩法地图。失败时请查看 `Saved/setup-project.log` 与 `Saved/setup-project.json`；只有 `status` 为 `passed` 才表示该次初始化成功。重新运行 Setup 可重建缺失或损坏的特效。

## 操作

| 操作 | 按键 / 界面 |
| --- | --- |
| 选择武器 | `1` 火箭、`2` 陨石、`3` 铁球，或点击底部武器卡 |
| 发射 | `空格` 或“发射冲击”按钮；点击空白场景不会发射 |
| 环绕观察 | 按住鼠标右键拖动；右键 + A/D 连续环绕，右键 + W/S 拉近拉远 |
| 旋转 / 缩放 / 高度 | `Z/C` 每次旋转 45°，滚轮缩放，`Q/E` 调整观察高度 |
| 调整瞄准 | 松开右键后 `A/D` 调整横向，`W/S` 调整落点纵向或发射高度，上下方向键调整角度 |
| 重置视角 / 场景 | `F` 重置镜头；`R` 或“重建场景”重置建筑、武器与瞄准 |
| 帮助 / 隐藏界面 / 退出 | `H/F1`、`Tab`、`Alt+F4` |

默认武器为陨石。陨石入射角范围 30–90°：低于 78° 时贯穿楼体，78° 及以上首次命中后嵌入，约一秒后成为物理残骸。火箭沿路径贯穿并破坏构件，铁球受重力影响。真实命中才触发爆炸、局部碎裂和短暂慢动作，打空不会触发建筑损伤。

建筑使用预切分网格与局部物理模拟，失稳通过游戏规则向邻近结构传播。这是可编辑的拆除玩法原型，尚未包含人物、音效、完整结算流程或独立 Windows 安装包。

## 工程与源资源

| 目录 / 文件 | 内容 |
| --- | --- |
| `Content/Skyfall` | 地图、玩法蓝图、材质、网格、中文 HUD 和字体资源 |
| `SourceAssets` | FBX、纹理、HDR、字体、资源清单 |
| `SourceAssets/BuildingSource` | 自有建筑 GLB 和构件清单，可独立重新导出 PC 资源 |
| `tools` | UE Python 构建 / 运行检查脚本、Blender 导出脚本及便携启动入口 |
| `Config` | 工程输入、渲染和地图配置 |

日常试玩不需要 Blender，也无需重建已有建筑和玩法蓝图。需要修改美术时，可在 Blender 中运行 `export_pc_assets.py`、`record_import_cleanup.py`、`export_combat_assets.py` 或 `export_meteor_asset.py`；导出的清单使用相对工程根目录的路径。UE 导入与安装脚本须在 UE Python 环境运行。完整重建涉及脚本间的先后依赖，建议先另存工程副本；`SetupProject.cmd` 仅负责首次试玩所需的缺省特效。

`runtime_*.py` 是开发用运行检查脚本，多数须在完整编辑器的 PIE 环境运行。检查报告写入 `Saved`，截图写入 `Saved/QA`。仓库不附带原工作区的历史检查报告，静态扫描不能代替实际启动、玩法与画面验收。

## 许可

自有代码和原创资产采用 [MIT](LICENSE)。第三方资源按各自许可提供，完整范围与来源见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)：Poly Haven 纹理 / HDR 为 CC0，Noto Sans SC 字体为 OFL 1.1。

Unreal Engine、引擎内置资源及 Niagara 模板不属于本项目的 MIT 授权。本仓库**不分发六个由 Epic 模板派生的 `NS_*.uasset`**；首次 Setup 从用户自己安装的引擎模板生成它们，并由 `.gitignore` 排除。生成结果仍受适用的 Epic 许可约束，不能按本仓库 MIT 许可单独转授权。
