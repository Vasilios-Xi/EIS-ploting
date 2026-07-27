# Origin EIS Plotting

这是一个面向 Metrohm Autolab NOVA `.nox` 数据的安全解析与 Origin Nyquist
绘图工具。仓库同时提供 Codex skill 命令行入口和可打包为单文件 EXE 的中文桌面
界面。

## 功能

- 直接选择一个 `.nox` 文件或包含多个 `.nox` 的文件夹。
- 强制用户填写有效电极面积 `A`（cm²），不会猜测实验参数。
- 不加载或执行 NOX 内的 .NET 类型，直接按 MS-NRBF 记录安全解析。
- 计算 `Z′ × A` 和 NOVA 已给出的 `-Z″ × A`，不会重复反转虚部符号。
- 生成原始面积归一化图和左侧 `Y=0` 截距平移后的最终图。
- 保留可编辑 Origin `.opju`，并导出 1800 px 宽 PNG。

## 为什么使用 EXE

绘图依赖 Origin 的 Automation Server COM 接口。普通 HTML 页面受浏览器安全沙箱
限制，不能直接控制本机 Origin 或保存 `.opju`，因此 Windows EXE 才能完整保留
skill 的功能。

目标电脑仍需安装并授权 Origin 2021 或更高版本。EXE 内已包含官方 `originpro`
外部 Python 包、NOX 解析器和图片校验依赖。

## 使用方法

1. 双击 `Origin-EIS-Plotter.exe`。
2. 选择 NOX 文件或文件夹。
3. 填写有效电极面积，单位 `cm²`。
4. 检查或修改自动推断的样品标签。
5. 选择输出目录，点击“开始处理并绘图”。

成功后仅生成：

```text
<输出目录>/
├─ NOX处理结果/
├─ 原始EIS图/
└─ 最终EIS图/
```

如果这些固定结果文件夹已经存在，程序会先要求确认；输出目录中的其他文件不会
被删除。

## 本地运行源码

```powershell
pip install originpro Pillow
python -m eis_app.gui
```

## 构建单文件 EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 `
  -PythonExe "C:\Path\to\python.exe"
```

输出文件：

```text
dist\Origin-EIS-Plotter.exe
```

无界面自检：

```powershell
.\dist\Origin-EIS-Plotter.exe --self-test .\self-test.json
Get-Content .\self-test.json
```

## 代码结构

```text
eis_app/
├─ core.py                 # 工作流编排、输出事务与校验
└─ gui.py                  # 中文界面、冻结 EXE 后台工作进程
scripts/
├─ extract_nox.py          # 安全 MS-NRBF 解析与面积归一化
├─ plot_eis_origin.py      # Origin Nyquist 绘图
└─ run_eis_workflow.py     # 原 skill 命令行入口
```

GUI 与原 skill 共用同一份解析和绘图函数，不会形成两套计算规则。
